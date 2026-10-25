/* Maintainer-only, read-only comparison of XNU, IORegistry and powerd/IOPS. */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/sysctl.h>
#include <unistd.h>

extern CFTypeRef IOPSCopyPowerSourcesInfo(void);
extern CFArrayRef IOPSCopyPowerSourcesList(CFTypeRef);
extern CFDictionaryRef IOPSGetPowerSourceDescription(CFTypeRef, CFTypeRef);
extern int IOPSGetPercentRemaining(int *, bool *, bool *);

static int kernel(const char *key)
{
    int value = -1;
    size_t size = sizeof(value);
    sysctlbyname(key, &value, &size, NULL, 0);
    return value;
}

static int number(CFDictionaryRef d, CFStringRef key)
{
    int value = -1;
    CFTypeRef obj = d ? CFDictionaryGetValue(d, key) : NULL;
    if (obj && CFGetTypeID(obj) == CFNumberGetTypeID()) CFNumberGetValue(obj, kCFNumberIntType, &value);
    else if (obj && CFGetTypeID(obj) == CFBooleanGetTypeID()) value = CFBooleanGetValue(obj);
    return value;
}

static int source_external(CFDictionaryRef d)
{
    CFTypeRef value = d ? CFDictionaryGetValue(d, CFSTR("Power Source State")) : NULL;
    if (!value || CFGetTypeID(value) != CFStringGetTypeID()) return -1;
    if (CFEqual(value, CFSTR("AC Power"))) return 1;
    if (CFEqual(value, CFSTR("Battery Power"))) return 0;
    return -1;
}

int main(int argc, char **argv)
{
    int count = argc > 1 ? atoi(argv[1]) : 1;
    if (count < 1 || count > 60) return 2;
    for (int i = 0; i < count; i++) {
        if (i) CFRunLoopRunInMode(kCFRunLoopDefaultMode, 5.0, false);
        io_service_t service = IOServiceGetMatchingService(0, IOServiceMatching("IOPMPowerSource"));
        CFMutableDictionaryRef props = NULL;
        if (service) IORegistryEntryCreateCFProperties(service, &props, kCFAllocatorDefault, 0);
        int percent = -1;
        bool charging = false, full = false;
        int result = IOPSGetPercentRemaining(&percent, &charging, &full);
        CFTypeRef info = IOPSCopyPowerSourcesInfo();
        CFArrayRef list = info ? IOPSCopyPowerSourcesList(info) : NULL;
        long sources = list ? CFArrayGetCount(list) : 0;
        CFDictionaryRef first = sources ? IOPSGetPowerSourceDescription(info, CFArrayGetValueAtIndex(list, 0)) : NULL;
        printf("lemonade-battery-test: n=%d kernel_soc=%d stale=%d valid=%x updates=%d registry_soc=%d max=%d telemetry=%d voltage=%d current=%d temperature=%d iops_result=%d iops_soc=%d sources=%ld source_capacity=%d source_max=%d source_present=%d charging=%d full=%d external=%d charge_state=%d gauge_status=%x registry_charging=%d registry_full=%d iops_external=%d\n",
            i, kernel("kern.lemonade_battery.soc"), kernel("kern.lemonade_battery.stale"),
            kernel("kern.lemonade_battery.valid"), kernel("kern.lemonade_battery.updates"),
            number(props, CFSTR("CurrentCapacity")), number(props, CFSTR("MaxCapacity")),
            number(props, CFSTR("LemonadeTelemetryValid")), number(props, CFSTR("Voltage")),
            number(props, CFSTR("Amperage")), number(props, CFSTR("Temperature")), result, percent, sources,
            number(first, CFSTR("Current Capacity")), number(first, CFSTR("Max Capacity")),
            number(first, CFSTR("Is Present")), charging, full,
            kernel("kern.lemonade_battery.external"), kernel("kern.lemonade_battery.charge_state"),
            kernel("kern.lemonade_battery.battery_status"), number(props, CFSTR("IsCharging")), number(props, CFSTR("FullyCharged")), source_external(first));
        fflush(stdout);
        if (props) CFRelease(props);
        if (service) IOObjectRelease(service);
        if (list) CFRelease(list);
        if (info) CFRelease(info);
    }
    return 0;
}
