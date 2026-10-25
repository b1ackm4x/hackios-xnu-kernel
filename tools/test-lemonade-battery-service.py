#!/usr/bin/env python3
"""Run the production power-source adapter against a small IOKit model.

This verifies adapter properties and notifications, not powerd/SpringBoard.
The model mirrors updateStatus's copy-only behavior: deletion from the internal
dictionary alone must not make the IORegistry value disappear.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <map>
#include <string>
#define OSDeclareDefaultStructors(...)
#define OSDefineMetaClassAndStructors(...)
#define OSTypeAlloc(t) new t
#define OSSafeReleaseNULL(o) do { if(o) (o)->release(); (o) = nullptr; } while(0)
struct OSObject {
    mutable unsigned refs = 1;
    virtual ~OSObject() = default;
    void retain() const { ++refs; }
    void release() const { assert(refs); if (!--refs) delete this; }
};
struct OSString : OSObject {
    std::string value;
    explicit OSString(const char *s) : value(s) {}
    static OSString *withCString(const char *s) { return new OSString(s); }
};
using OSSymbol = OSString;
struct OSNumber : OSObject {
    uint32_t value;
    explicit OSNumber(uint64_t v) : value(v) {}
    static OSNumber *withNumber(unsigned long long v, unsigned) { return new OSNumber(v); }
};
struct OSBoolean : OSObject { bool value; explicit OSBoolean(bool v) : value(v) {} };
static OSBoolean yes(true), no(false);
static OSBoolean *kOSBooleanTrue = &yes, *kOSBooleanFalse = &no;
struct OSDictionary : OSObject {
    std::map<std::string, OSObject *> items;
    ~OSDictionary() override { for (auto &p : items) p.second->release(); }
    static OSDictionary *withCapacity(unsigned) { return new OSDictionary; }
    void setObject(const char *key, OSObject *v) {
        v->retain(); auto old = items.find(key);
        if (old != items.end()) old->second->release();
        items[key] = v;
    }
    OSObject *getObject(const char *key) const {
        auto p = items.find(key); return p == items.end() ? nullptr : p->second;
    }
    void removeObject(const char *key) {
        auto p = items.find(key); if (p != items.end()) { p->second->release(); items.erase(p); }
    }
};
struct IOService : OSObject {
    OSDictionary registry;
    std::string name;
    bool registered = false;
    virtual bool init() { return true; }
    static IOService *getPlatform() { static IOService platform; return &platform; }
    bool attach(IOService *) { return true; }
    bool start(IOService *) { return true; }
    void detach(IOService *) {}
    void registerService() { registered = true; }
    void setName(const char *s) { name = s; }
    OSObject *getProperty(const char *s) { return registry.getObject(s); }
    void removeProperty(const char *s) { registry.removeObject(s); }
};
struct IOPMPowerSource : IOService {
    OSDictionary *properties = new OSDictionary;
    bool settingsChangedSinceUpdate = false;
    unsigned notifications = 0;
    ~IOPMPowerSource() override { properties->release(); }
    void setPSProperty(const OSSymbol *s, OSObject *v) {
        properties->setObject(s->value.c_str(), v); settingsChangedSinceUpdate = true;
    }
    void putNumber(const char *key, uint32_t value) {
        OSNumber *n = OSNumber::withNumber(value, 32);
        properties->setObject(key, n); n->release(); settingsChangedSinceUpdate = true;
    }
    void putFlag(const char *key, bool value) {
        properties->setObject(key, value ? kOSBooleanTrue : kOSBooleanFalse); settingsChangedSinceUpdate = true;
    }
#define N(method,key) void method(unsigned v) { putNumber(key, v); }
    N(setCurrentCapacity,"CurrentCapacity") N(setMaxCapacity,"MaxCapacity")
    N(setVoltage,"Voltage") N(setCycleCount,"CycleCount")
    void setAmperage(int v) { putNumber("Amperage", static_cast<uint32_t>(v)); }
#undef N
#define B(method,key) void method(bool v) { putFlag(key,v); }
    B(setBatteryInstalled,"BatteryInstalled") B(setExternalConnected,"ExternalConnected")
    B(setExternalChargeCapable,"ExternalChargeCapable") B(setIsCharging,"IsCharging")
#undef B
    void updateStatus() {
        if (!settingsChangedSinceUpdate) return;
        for (auto &p : properties->items) registry.setObject(p.first.c_str(), p.second);
        settingsChangedSinceUpdate = false; notifications++;
    }
};
static void IOLog(const char *, ...) {}
#define kIOPMPSAdapterDetailsWattsKey "Watts"
#define kIOPMPSAdapterDetailsAmperageKey "Amperage"
#define kIOPMPSAdapterDetailsVoltage "Voltage"
#define kIOPMPSAdapterDetailsFamilyKey "FamilyCode"
#define kIOPMPSAdapterDetailsKey "AdapterDetails"
'''
TEST = r'''
static int value(const char *key) {
    auto n = dynamic_cast<OSNumber *>(battery->getProperty(key)); assert(n); return static_cast<int32_t>(n->value);
}
static bool flag(const char *key) {
    auto n = dynamic_cast<OSBoolean *>(battery->getProperty(key)); assert(n); return n->value;
}
static void absent(const char *key) {
    assert(!battery->getProperty(key) && !battery->properties->getObject(key));
}
int main() {
    lemonade_battery_sample s = {LEMONADE_BATTERY_ALL, 0, 7800000, -400000, 2495, 0, 2200000, 0, 1, 0xc0};
    LemonadeBatteryReport(nullptr, 0, 0); assert(!battery);
    LemonadeBatteryReport(&s, 1, 1); assert(!battery);
    s.percent_hundredths = 10001; LemonadeBatteryReport(&s, sizeof(s), 1); assert(!battery);
    s.percent_hundredths = 0; LemonadeBatteryReport(&s, sizeof(s), 1);
    assert(battery && battery->registered && battery->name == "LemonadeBattery");
    assert(value("CurrentCapacity") == 0 && value("MaxCapacity") == 100);
    assert(value("AppleRawCurrentCapacity") == 0 && value("AppleRawMaxCapacity") == 2200);
    assert(value("CycleCount") == 0 && value("Amperage") == -400 && value("Voltage") == 7800);
    assert(value("Temperature") == 2495 && flag("BatteryInstalled") && flag("LemonadeTelemetryValid"));
    assert(flag("ExternalConnected") && !flag("IsCharging"));
    assert(!flag("FullyCharged") && value("LemonadeChargeState") == LEMONADE_CHARGE_NOT_CHARGING);
    absent("ExternalChargeCapable"); absent("DesignCapacity");
    auto original = battery;
    unsigned before = battery->notifications;
    s.valid = LEMONADE_BATTERY_REQUIRED;
    s.percent_hundredths = 7300;
    LemonadeBatteryReport(&s, sizeof(s), 1);
    assert(battery == original && battery->notifications > before && value("CurrentCapacity") == 73);
    for (const char *key : {"Amperage", "AppleRawCurrentCapacity", "AppleRawMaxCapacity", "NominalChargeCapacity",
        "CycleCount", "ExternalConnected", "AppleRawExternalConnected", "IsCharging", "FullyCharged", "LemonadeBatteryStatus"}) absent(key);
    before = battery->notifications;
    LemonadeBatteryReport(nullptr, 0, 0);
    assert(!flag("LemonadeTelemetryValid") && value("LemonadeValidFields") == 0 && flag("BatteryInstalled"));
    assert(battery->notifications > before);
    for (const char *key : {"CurrentCapacity", "MaxCapacity", "Voltage", "Temperature"}) absent(key);
    s.valid = LEMONADE_BATTERY_ALL; s.current_ua = 300000; s.battery_status = 0x80;
    LemonadeBatteryReport(&s, sizeof(s), 1);
    assert(battery == original && value("CurrentCapacity") == 73 && flag("IsCharging"));
    s.current_ua = 4; s.battery_status = 0xc0; LemonadeBatteryReport(&s, sizeof(s), 1);
    assert(!flag("IsCharging") && flag("ExternalConnected") && !flag("FullyCharged"));
    s.battery_status = 0xe0; LemonadeBatteryReport(&s, sizeof(s), 1);
    assert(flag("FullyCharged") && !flag("IsCharging"));
    s.external = 0; LemonadeBatteryReport(&s, sizeof(s), 1); assert(!flag("IsCharging"));
    battery->release(); battery = nullptr;
    redwood_battery_state legacy = {sizeof(legacy), 5100, 3800000, 100000, 2400, 1, 1, 1, 2000000, 4000000, 4500000, 10, 1, 5000000, 500000};
    RedwoodBatteryReport(&legacy, sizeof(legacy));
    assert(battery->name == "RedwoodBattery" && value("CurrentCapacity") == 51 && flag("IsCharging"));
    battery->release(); battery = nullptr;
    assert(yes.refs == 1 && no.refs == 1);
    puts("PASS: production IOKit adapter, zero values, stale/optional property removal, notifications, recovery and Redwood compatibility");
}
'''

if __name__ == '__main__':
    source = (ROOT / 'xnu/files/iokit/Kernel/RedwoodBattery.cpp').read_text()
    source = re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)
    header = ROOT / 'xnu/files/bsd/sys/lemonade_battery.h'
    with tempfile.TemporaryDirectory(prefix='lemonade-battery-service-') as directory:
        cpp, binary = Path(directory) / 'test.cpp', Path(directory) / 'test'
        cpp.write_text(PRELUDE + '\n#include "' + str(header) + '"\n' + source + TEST)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                        str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
