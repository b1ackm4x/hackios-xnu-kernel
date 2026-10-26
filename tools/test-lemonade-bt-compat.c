/* Exercise production data-only mutation against real Mach VM protections.
 * Run on an Apple Silicon macOS host; this never accesses a radio or phone.
 */
#include <assert.h>
#include "../userland/src/lemonade_bt_compat.c"

static vm_prot_t protection(vm_address_t address) {
    vm_size_t size = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    assert(vm_region_64(mach_task_self(), &address, &size, VM_REGION_BASIC_INFO_64,
        (vm_region_info_t)&info, &count, &object) == KERN_SUCCESS);
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    return info.protection;
}

int main(void) {
    CFMutableDictionaryRef original = CFDictionaryCreateMutable(NULL, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(original, CFSTR("unrelated"), CFSTR("preserved"));
    CFDictionarySetValue(original, CFSTR("SupportsMultipleAdvertising"), kCFBooleanTrue);
    CFDictionaryRef configured = board_configuration(CFSTR("LE"), original);
    assert(configured && CFDictionaryGetValue(configured, CFSTR("SupportsMultipleAdvertising")) == kCFBooleanFalse);
    assert(CFEqual(CFDictionaryGetValue(configured, CFSTR("unrelated")), CFSTR("preserved")));
    assert(CFDictionaryGetValue(original, CFSTR("SupportsMultipleAdvertising")) == kCFBooleanTrue);
    CFRelease(configured);
    configured = board_configuration(CFSTR("ScanEnhancements"), NULL);
    assert(configured && CFDictionaryGetValue(configured, CFSTR("DisableScanEnhancements")) == kCFBooleanTrue);
    CFRelease(configured);
    assert(!board_configuration(CFSTR("UnrelatedDomain"), original));
    CFRelease(original);
    vm_address_t address = 0;
    vm_size_t size = (vm_size_t)getpagesize();
    assert(vm_allocate(mach_task_self(), &address, size, VM_FLAGS_ANYWHERE) == KERN_SUCCESS);
    uintptr_t *slot = (uintptr_t *)address;
    *slot = 0x1234;
    assert(vm_protect(mach_task_self(), address, size, FALSE, VM_PROT_READ) == KERN_SUCCESS);
    assert(replace_data_pointer(slot, 0x5678));
    assert(*slot == 0x5678 && protection(address) == VM_PROT_READ);
    assert(replace_data_pointer(slot, 0));
    assert(*slot == 0 && protection(address) == VM_PROT_READ);
    assert(vm_protect(mach_task_self(), address, size, FALSE, VM_PROT_READ | VM_PROT_EXECUTE) == KERN_SUCCESS);
    assert(!replace_data_pointer(slot, 1));
    assert(*slot == 0 && protection(address) == (VM_PROT_READ | VM_PROT_EXECUTE));
    assert(vm_deallocate(mach_task_self(), address, size) == KERN_SUCCESS);
    assert(!replace_data_pointer(slot, 1));
    puts("PASS: real Mach read-only data replacement, protection restoration, executable/unmapped refusal");
    return 0;
}
