/* Real OnePlus controller identity for native bluetoothd's IORegistry lookup.
 * This is an IOService identity provider, not an Apple controller emulator. */
#include <IOKit/IOLib.h>
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IOService.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <libkern/c++/OSData.h>

extern "C" bool LemonadeBluetoothIdentity(const uint8_t *address, bool ready);
class LemonadeBluetooth : public IOService {
    OSDeclareDefaultStructors(LemonadeBluetooth);
};
OSDefineMetaClassAndStructors(LemonadeBluetooth, IOService);
static LemonadeBluetooth *identity;

bool LemonadeBluetoothIdentity(const uint8_t *address, bool ready) {
    if (!address) {
        if (identity) identity->setProperty("LemonadeTransportReady", false);
        return false;
    }
    uint8_t public_order[6];
    unsigned nonzero = 0;
    for (unsigned i = 0; i < 6; i++) { public_order[i] = address[5 - i]; nonzero |= address[i]; }
    if (!nonzero || (public_order[0] & 1)) return false;
    if (!identity) {
        IOService *platform = IOService::getPlatform();
        IORegistryEntry *armio = IORegistryEntry::fromPath("/arm-io", gIODTPlane);
        LemonadeBluetooth *made = OSTypeAlloc(LemonadeBluetooth);
        if (!platform || !armio || !made || !made->init()) {
            OSSafeReleaseNULL(armio); OSSafeReleaseNULL(made); return false;
        }
        made->setName("bluetooth");
        OSData *name = OSData::withBytes("bluetooth", sizeof("bluetooth"));
        OSData *compatible = OSData::withBytes("qcom,wcn6851-bt", sizeof("qcom,wcn6851-bt"));
        if (!name || !compatible) {
            OSSafeReleaseNULL(name); OSSafeReleaseNULL(compatible);
            armio->release(); made->release(); return false;
        }
        made->setProperty("name", name); made->setProperty("compatible", compatible);
        name->release(); compatible->release();
        made->setProperty("local-mac-address", public_order, sizeof(public_order));
        /* Native iOS 16.5 transport selection maps 1 to ordinary H4. Its
         * default 3 is the Broadcom-specific H4BC protocol, which this real
         * Qualcomm transport does not implement. The property is CFData. */
        uint32_t h4_encoding = 1;
        made->setProperty("transport-encoding", &h4_encoding, sizeof(h4_encoding));
        made->setProperty("LemonadeTransportReady", ready);
        if (!made->attach(platform)) { armio->release(); made->release(); return false; }
        if (!made->attachToParent(armio, gIODTPlane) || !made->start(platform)) {
            made->detachFromParent(armio, gIODTPlane); made->detach(platform);
            armio->release(); made->release(); return false;
        }
        armio->release();
        made->registerService(); identity = made;
        IOLog("LemonadeBluetooth: real device identity published; address omitted; transport ready=%u\n", ready);
    } else {
        identity->setProperty("local-mac-address", public_order, sizeof(public_order));
        identity->setProperty("LemonadeTransportReady", ready);
    }
    return true;
}
