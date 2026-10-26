/* iOS 16.5 board compatibility: gate an optional HCI initialization step
 * against the controller's unmodified Read Local Supported Commands response.
 * No HCI event, identity, status or feature bit is synthesized. Only a data
 * table entry in the exact reviewed native binary is replaced in this process.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

static uintptr_t native_base;
static void (*original_step)(void);
static void (*original_periodic_step)(void);
static bool native_active;

/* These are existing native configuration switches. They select the baseline
 * Bluetooth specification path rather than the proprietary controller paths
 * inferred from the emulated Apple product model. They do not change any
 * controller-reported capability or fabricate command completions.
 */
static CFDictionaryRef board_configuration(CFStringRef section, CFPropertyListRef previous) {
    if (!section || CFGetTypeID(section) != CFStringGetTypeID()) return NULL;
    struct setting { CFStringRef section, key; bool enabled; } settings[] = {
        {CFSTR("LE"), CFSTR("SupportsMultipleAdvertising"), false},
        {CFSTR("AE"), CFSTR("DisableExtScans"), true},
        {CFSTR("AE"), CFSTR("DisableExtAdvertising"), true},
        {CFSTR("AE"), CFSTR("DisablePeriodicAdvertisingAdv"), true},
        {CFSTR("AE"), CFSTR("DisablePeriodicAdvertisingSync"), true},
        {CFSTR("VSCScanParams"), CFSTR("DisableVSCScanParams"), true},
        {CFSTR("ScanEnhancements"), CFSTR("DisableScanEnhancements"), true},
        {CFSTR("LowPowerCoreScan"), CFSTR("DisableLowPowerCoreScan"), true},
        {CFSTR("ScanCore"), CFSTR("DisableScanCore"), true},
    };
    CFMutableDictionaryRef result = NULL;
    for (size_t i = 0; i < sizeof(settings) / sizeof(settings[0]); i++) {
        if (!CFEqual(section, settings[i].section)) continue;
        if (!result) {
            result = previous && CFGetTypeID(previous) == CFDictionaryGetTypeID()
                ? CFDictionaryCreateMutableCopy(kCFAllocatorDefault, 0, previous)
                : CFDictionaryCreateMutable(kCFAllocatorDefault, 0,
                    &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
            if (!result) return NULL;
        }
        CFDictionarySetValue(result, settings[i].key,
            settings[i].enabled ? kCFBooleanTrue : kCFBooleanFalse);
    }
    return result;
}

static CFPropertyListRef board_preferences(CFStringRef key, CFStringRef app) {
    CFPropertyListRef original = CFPreferencesCopyAppValue(key, app);
    if (!native_active || !app || !CFEqual(app, CFSTR("com.apple.MobileBluetooth.debug"))) return original;
    CFDictionaryRef configured = board_configuration(key, original);
    if (!configured) return original;
    if (original) CFRelease(original);
    return configured;
}

__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement, *original;
} configuration_interpose = {(const void *)board_preferences, (const void *)CFPreferencesCopyAppValue};

static bool board_ready(void) {
    io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault,
        IOServiceMatching("LemonadeBluetooth"));
    if (!service) return false;
    CFTypeRef compatible = IORegistryEntryCreateCFProperty(service, CFSTR("compatible"), kCFAllocatorDefault, 0);
    CFTypeRef state = IORegistryEntryCreateCFProperty(service, CFSTR("LemonadeTransportReady"), kCFAllocatorDefault, 0);
    const char expected[] = "qcom,wcn6851-bt";
    bool ok = compatible && CFGetTypeID(compatible) == CFDataGetTypeID() &&
        CFDataGetLength(compatible) == sizeof(expected) &&
        !memcmp(CFDataGetBytePtr(compatible), expected, sizeof(expected)) && state &&
        CFGetTypeID(state) == CFBooleanGetTypeID() && CFBooleanGetValue(state);
    if (compatible) CFRelease(compatible);
    if (state) CFRelease(state);
    IOObjectRelease(service);
    return ok;
}

static void optional_erroneous_reporting(void) {
    /* Command 0x0c5b: octet 18, bit 3. The native command descriptor and
     * initialization table are both checked before this wrapper is installed.
     * This bitmap is filled by the genuine controller response, not this shim.
     */
    const uint8_t *supported = (const uint8_t *)(native_base + 0x863928);
    void *state = *(void **)(native_base + 0x84f588);
    if ((supported[18] & 8) || !state || !board_ready()) {
        original_step();
        return;
    }
    fprintf(stderr, "bt-compat: optional erroneous-data reporting unsupported; disabled; real capabilities preserved\n");
    /* Same native sequencer used by other capability-gated initialization
     * steps. This does not invoke an HCI completion callback with fake success.
     */
    ((void (*)(void *))(native_base + 0x1c204c))(state);
}

static void optional_periodic_list(void) {
    void *state = *(void **)(native_base + 0x84f588);
    if (!native_active || !state || !board_ready()) {
        original_periodic_step(); return;
    }
    /* The selected native host policy disables extended/periodic operation.
     * Core Vol 4, Part E 3.1.1 puts Read Periodic Advertiser List Size (0x204a)
     * in the extended command family. Even this read locks the controller into
     * that family until Reset, making later legacy 0x2007/0x200b fail with 0x0c.
     * Skip the unused query via the same native sequencer, leaving the genuine
     * supported commands/features intact and periodic operation disabled.
     */
    fprintf(stderr, "bt-compat: legacy host policy; unused periodic-list query skipped before command-family selection\n");
    ((void (*)(void *))(native_base + 0x1c204c))(state);
}

static bool replace_data_pointer(uintptr_t *slot, uintptr_t replacement) {
    vm_address_t region = (vm_address_t)slot;
    vm_size_t region_size = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    kern_return_t kr = vm_region_64(mach_task_self(), &region, &region_size, VM_REGION_BASIC_INFO_64,
        (vm_region_info_t)&info, &count, &object);
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    if (kr || region > (vm_address_t)slot || region_size < sizeof(*slot) ||
        (vm_address_t)slot - region > region_size - sizeof(*slot) ||
        (info.protection & VM_PROT_EXECUTE)) {
        fprintf(stderr, "bt-compat: data region validation failed; unchanged\n"); return false;
    }
    vm_size_t page_size = (vm_size_t)getpagesize();
    vm_address_t page = (vm_address_t)slot & ~(page_size - 1);
    bool writable = (info.protection & VM_PROT_WRITE) != 0;
    if (!writable && vm_protect(mach_task_self(), page, page_size, FALSE,
            info.protection | VM_PROT_WRITE | VM_PROT_COPY)) {
        fprintf(stderr, "bt-compat: data page unavailable; unchanged\n"); return false;
    }
    uintptr_t original = *slot;
    __atomic_store_n(slot, replacement, __ATOMIC_RELEASE);
    if (!writable && vm_protect(mach_task_self(), page, page_size, FALSE, info.protection)) {
        /* Refuse to run with a weakened data-page protection. */
        __atomic_store_n(slot, original, __ATOMIC_RELEASE);
        fprintf(stderr, "bt-compat: cannot restore data page protection; terminating\n");
        _exit(1);
    }
    return true;
}

__attribute__((constructor)) static void install(void) {
    if (strcmp(getprogname(), "bluetoothd")) return;
    char args[2048] = {0}; size_t size = sizeof(args) - 1;
    if (sysctlbyname("kern.bootargs", args, &size, NULL, 0)) return;
    const char *mode = strstr(args, "lemonade_bt=6");
    if (!mode || (mode != args && mode[-1] != ' ') || (mode[13] && mode[13] != ' ')) return;
    bool verified = false;
    for (unsigned i = 0; i < 100 && !(verified = board_ready()); i++) usleep(250000);
    if (!verified) { fprintf(stderr, "bt-compat: verified board unavailable; unchanged\n"); return; }
    const struct mach_header_64 *header = NULL;
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        const char *leaf = name ? strrchr(name, '/') : NULL;
        if (name && !strcmp(leaf ? leaf + 1 : name, "bluetoothd")) {
            header = (const struct mach_header_64 *)_dyld_get_image_header(i); break;
        }
    }
    if (!header || header->magic != MH_MAGIC_64 || header->cputype != CPU_TYPE_ARM64 ||
        header->filetype != MH_EXECUTE || header->sizeofcmds > 65536) return;
    const uint8_t expected_uuid[16] = {0x96,0x39,0x30,0xf1,0xd5,0x65,0x32,0xb5,0x91,0xd6,0x43,0xaf,0x71,0xe2,0xc8,0x5a};
    const uint8_t *cursor = (const uint8_t *)(header + 1), *end = cursor + header->sizeofcmds;
    bool uuid_ok = false;
    for (uint32_t i = 0; i < header->ncmds; i++) {
        if ((size_t)(end - cursor) < sizeof(struct load_command)) return;
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmdsize < sizeof(*command) || command->cmdsize > (size_t)(end - cursor)) return;
        if (command->cmd == LC_UUID && command->cmdsize == sizeof(struct uuid_command))
            uuid_ok = !memcmp(((const struct uuid_command *)cursor)->uuid, expected_uuid, 16);
        cursor += command->cmdsize;
    }
    if (!uuid_ok) { fprintf(stderr, "bt-compat: native ABI mismatch; unchanged\n"); return; }
    native_base = (uintptr_t)header;
    const uint8_t step_bytes[16] = {0xff,0xc3,0x00,0xd1,0xf4,0x4f,0x01,0xa9,0xfd,0x7b,0x02,0xa9,0xfd,0x83,0x00,0x91};
    const uint8_t next_bytes[16] = {0xf4,0x4f,0xbe,0xa9,0xfd,0x7b,0x01,0xa9,0xfd,0x43,0x00,0x91,0xf3,0x03,0x00,0xaa};
    const uint8_t descriptor[6] = {2,0,0x5b,0x0c,8,18};
    const uint8_t periodic_descriptor[6] = {4,0,0x4a,0x20,0x40,0x26};
    const uint8_t periodic_bytes[16] = {0xff,0xc3,0x00,0xd1,0xf4,0x4f,0x01,0xa9,0xfd,0x7b,0x02,0xa9,0xfd,0x83,0x00,0x91};
    void (**slot)(void) = (void (**)(void))(native_base + 0x7fbde0);
    void (**periodic_slot)(void) = (void (**)(void))(native_base + 0x7fbdb0);
    if ((uintptr_t)*slot != native_base + 0x10b970 ||
        (uintptr_t)slot[-1] != native_base + 0x10b868 ||
        memcmp((const void *)(native_base + 0x10b970), step_bytes, sizeof(step_bytes)) ||
        memcmp((const void *)(native_base + 0x1c204c), next_bytes, sizeof(next_bytes)) ||
        memcmp((const void *)(native_base + 0x845470), descriptor, sizeof(descriptor)) ||
        (uintptr_t)*periodic_slot != native_base + 0x10b3bc ||
        memcmp((const void *)(native_base + 0x10b3bc), periodic_bytes, sizeof(periodic_bytes)) ||
        memcmp((const void *)(native_base + 0x845dd0), periodic_descriptor, sizeof(periodic_descriptor))) {
        fprintf(stderr, "bt-compat: native data/code validation failed; unchanged\n"); return;
    }
    /* Native BCM initialization copies this 0x750-byte callback table to
     * 0x850020. Its wrappers explicitly treat a null callback as OI 0xe11
     * (vendor command unsupported), without sending HCI or waiting for a
     * completion. Every one of the 230 direct callback loads was checked to
     * have a null guard. Standard HCI commands use a different dispatch table.
     * Qualcomm must not bind this Broadcom-only implementation.
     */
    uintptr_t *vendor = (uintptr_t *)(native_base + 0x7fc5b8);
    const size_t vendor_count = 0x750 / sizeof(*vendor);
    unsigned callbacks = 0;
    for (size_t i = 0; i < vendor_count; i++) {
        uintptr_t target = vendor[i];
        if (!target) continue;
        if (target < native_base + 0x127a4c || target > native_base + 0x14972c || (target & 3)) {
            fprintf(stderr, "bt-compat: vendor callback table mismatch; unchanged\n"); return;
        }
        callbacks++;
    }
    if (callbacks != 204 || vendor[16] != native_base + 0x128628) {
        fprintf(stderr, "bt-compat: vendor callback count mismatch; unchanged\n"); return;
    }
    original_step = *slot;
    original_periodic_step = *periodic_slot;
    if (!replace_data_pointer((uintptr_t *)slot, (uintptr_t)optional_erroneous_reporting)) return;
    if (!replace_data_pointer((uintptr_t *)periodic_slot, (uintptr_t)optional_periodic_list)) return;
    for (size_t i = 0; i < vendor_count; i++) {
        if (vendor[i] && !replace_data_pointer(&vendor[i], 0)) {
            fprintf(stderr, "bt-compat: incomplete vendor capability binding; terminating\n"); _exit(1);
        }
    }
    fprintf(stderr, "bt-compat: Qualcomm board; Broadcom extensions unbound; native unsupported errors retained\n");
    native_active = true;
    fprintf(stderr, "bt-compat: native configuration selects standard advertising and scanning; vendor scan extensions disabled\n");
    fprintf(stderr, "bt-compat: exact native ABI verified; optional HCI and vendor capability gates installed\n");
}
