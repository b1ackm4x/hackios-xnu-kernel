/* Enable the installed Metal plugin before launchd starts its clients. */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <stdio.h>
extern kern_return_t set_properties(io_registry_entry_t, CFTypeRef)
    __asm("_IORegistryEntrySetCFProperties");
int main(void)
{
    io_service_t service = IOServiceGetMatchingService(0, IOServiceMatching("IOAcceleratorES"));
    if (!service) return 1;
    const void *keys[] = { CFSTR("MetalPluginName"), CFSTR("MetalPluginClassName") };
    const void *values[] = { CFSTR("RedwoodMetal"), CFSTR("RedwoodMetalDevice") };
    CFDictionaryRef properties = CFDictionaryCreate(NULL, keys, values, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    kern_return_t result = properties ? set_properties(service, properties) : KERN_FAILURE;
    if (properties) CFRelease(properties);
    IOObjectRelease(service);
    if (result) fprintf(stderr, "Metal startup failed: %#x\n", result);
    return result != KERN_SUCCESS;
}
