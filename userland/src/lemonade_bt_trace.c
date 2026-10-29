/*
 * Bounded, observation-only iOS 16.5 Bluetooth transport probe.
 * Insert only into bluetoothd / BlueTool in a disposable RAM image.
 * No return values are replaced. No addresses, pairing keys, HCI payloads,
 * arbitrary registry values, or user data are logged.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <errno.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <os/log.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>
#include "../../xnu/files/bsd/dev/lemonade_bt_protocol.h"
/* The local iOS SDK omits public XPC headers. These two opaque C declarations
 * match Apple's xpc.h (available since iOS 5); no XPC layout is accessed. */
typedef struct _xpc_object_s *xpc_object_t;
extern const char *xpc_dictionary_get_string(xpc_object_t, const char *);
extern xpc_object_t xpc_dictionary_get_value(xpc_object_t, const char *);

/* The public iOS SDK omits the kernel-control header. Layout and ioctl from
 * apple-oss-distributions/xnu, xnu-8796.121.2, bsd/sys/kern_control.h. */
#define MAX_KCTL_NAME 96
#define SYSPROTO_CONTROL 2
struct ctl_info { uint32_t ctl_id; char ctl_name[MAX_KCTL_NAME]; };
#define CTLIOCGINFO _IOWR('N', 3, struct ctl_info)
_Static_assert(sizeof(struct ctl_info) == 100, "kernel-control query ABI");

#define TRACE_LIMIT 4000u
#define FD_LIMIT 4096
#define FD_CONTROL 1
#define FD_BLUETOOTH 2
static unsigned records;
static unsigned char tracked[FD_LIMIT];
static int enabled;
static __thread int logging;
static const uint8_t *literal_sections[2];
static unsigned long literal_sizes[2];
static const struct mach_header_64 *main_header;
static pthread_mutex_t hci_lock = PTHREAD_MUTEX_INITIALIZER;
static struct lemonade_bt_parser hci_observer, hci_transmit_observer;
static int hci_fd = -1;
static unsigned hci_events, hci_inquiries, hci_legacy_reports, hci_le_reports;
static unsigned hci_acl_rx, hci_acl_tx;

static void say(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void say(const char *format, ...)
{
    int saved = errno;
    if (!enabled || logging || __atomic_fetch_add(&records, 1, __ATOMIC_RELAXED) >= TRACE_LIMIT)
        return;
    logging = 1;
    char line[512];
    int prefix = snprintf(line, sizeof(line), "bt-trace: %s: ", getprogname());
    va_list args;
    va_start(args, format);
    int n = vsnprintf(line + prefix, sizeof(line) - (size_t)prefix, format, args);
    va_end(args);
    if (n >= 0) {
        size_t count = (size_t)prefix + (size_t)n;
        if (count >= sizeof(line) - 1) count = sizeof(line) - 2;
        line[count++] = '\n';
        (void)write(STDERR_FILENO, line, count);
    }
    logging = 0;
    errno = saved;
}

/* Log protocol metadata only. In particular, dynamic-channel contents may be
 * keyboard input, SDP strings or pairing material and must never be printed. */
static void observe_acl(const char *direction, const uint8_t *p, size_t size) {
    if (size < 5 || p[0] != 2) return;
    unsigned header = lemonade_bt_le16(p + 1), boundary = (header >> 12) & 3;
    unsigned *count = !strcmp(direction, "RX") ? &hci_acl_rx : &hci_acl_tx;
    (*count)++;
    if (*count > 120 && *count % 100) return;
    say("real HCI ACL %s count=%u handle=%#x boundary=%u bytes=%zu", direction,
        *count, header & 0xfff, boundary, size - 5);
    if (boundary != 0 && boundary != 2) return;
    if (size < 9) return;
    unsigned length = lemonade_bt_le16(p + 5), cid = lemonade_bt_le16(p + 7);
    say("real L2CAP %s cid=%#x declared_bytes=%u fragment_bytes=%zu", direction, cid, length, size - 9);
    if (cid != 1 || length > size - 9) return;
    size_t end = 9 + length;
    for (size_t offset = 9; offset + 4 <= end;) {
        unsigned code = p[offset], bytes = lemonade_bt_le16(p + offset + 2);
        if (bytes > end - offset - 4) return;
        const uint8_t *data = p + offset + 4;
        say("real L2CAP signal %s code=%#x bytes=%u", direction, code, bytes);
        if (code == 2 && bytes == 4)
            say("real L2CAP connection request %s psm=%#x scid=%#x", direction,
                lemonade_bt_le16(data), lemonade_bt_le16(data + 2));
        else if (code == 3 && bytes == 8)
            say("real L2CAP connection response %s dcid=%#x scid=%#x result=%u status=%u", direction,
                lemonade_bt_le16(data), lemonade_bt_le16(data + 2),
                lemonade_bt_le16(data + 4), lemonade_bt_le16(data + 6));
        else if (code == 5 && bytes >= 6)
            say("real L2CAP configuration response %s scid=%#x result=%u", direction,
                lemonade_bt_le16(data), lemonade_bt_le16(data + 4));
        offset += 4 + bytes;
    }
}
static int observe_transmit(void *context, const uint8_t *p, size_t size) {
    (void)context;
    if (size >= 4 && p[0] == 1)
        say("real HCI command TX opcode=%#x parameter_bytes=%u", lemonade_bt_le16(p + 1), p[3]);
    observe_acl("TX", p, size);
    return 0;
}

/* Observe the same complete H4 events delivered to the native daemon. Never
 * alter them or emit addresses, names, keys, or advertising bytes. */
static int observe_hci(void *context, const uint8_t *p, size_t size) {
    (void)context;
    observe_acl("RX", p, size);
    if (size < 3 || p[0] != 4) return 0;
    hci_events++;
    if (size >= 7 && p[1] == 0x0e)
        say("real HCI command complete opcode=%#x status=%u", lemonade_bt_le16(p+4), p[6]);
    if (size == 14 && p[1] == 3)
        say("real HCI connection complete status=%u handle=%#x link_type=%u encrypted=%u",
            p[3], lemonade_bt_le16(p+4), p[12], p[13]);
    if (size == 7 && p[1] == 5)
        say("real HCI disconnection status=%u handle=%#x reason=%#x", p[3], lemonade_bt_le16(p+4), p[6]);
    if (size == 6 && (p[1] == 6 || p[1] == 0x30))
        say("real HCI security event=%#x status=%u handle=%#x", p[1], p[3], lemonade_bt_le16(p+4));
    if (size == 7 && p[1] == 8)
        say("real HCI encryption status=%u handle=%#x enabled=%u", p[3], lemonade_bt_le16(p+4), p[6]);
    if ((size == 9 && (p[1] == 0x16 || p[1] == 0x17 || p[1] == 0x31 || p[1] == 0x34)) ||
        (size == 26 && p[1] == 0x18) || (size == 12 && p[1] == 0x32) ||
        (size == 13 && (p[1] == 0x33 || p[1] == 0x3b)))
        say("real HCI pairing event=%#x; identity and security data omitted", p[1]);
    if (size == 10 && p[1] == 0x36)
        say("real HCI simple pairing complete status=%u", p[3]);
    if (size >= 4 && p[1] == 0x3e && (p[3] == 1 || p[3] == 0x0a) && size >= 7)
        say("real HCI LE connection subevent=%#x status=%u handle=%#x", p[3], p[4], lemonade_bt_le16(p+5));
    if (size == 7 && p[1] == 0x0f)
        say("real HCI command status opcode=%#x status=%u", lemonade_bt_le16(p+5), p[3]);
    if (size == 4 && p[1] == 1) {
        hci_inquiries++;
        say("real HCI inquiry complete status=%u count=%u", p[3], hci_inquiries);
    }
    if (size >= 4 && (p[1] == 2 || p[1] == 0x22 || p[1] == 0x2f)) {
        hci_legacy_reports += p[3];
        if (hci_legacy_reports <= 20 || hci_legacy_reports % 100 == 0)
            say("real HCI inquiry reports event=%#x total=%u", p[1], hci_legacy_reports);
    }
    if (size >= 5 && p[1] == 0x3e && (p[3] == 2 || p[3] == 0x0d))
        hci_le_reports += p[4];
    if (hci_events % 100 == 0)
        say("real HCI totals events=%u inquiry_reports=%u le_reports=%u", hci_events,
            hci_legacy_reports, hci_le_reports);
    return 0;
}

static void exit_trace(int status) {
    void *frames[24]; int count = backtrace(frames, 24);
    say("process exit status=%d frames=%d", status, count);
    for (int i = 0; i < count; i++) {
        Dl_info info = {0};
        if (dladdr(frames[i], &info) && info.dli_fbase) {
            const char *name = info.dli_fname ? strrchr(info.dli_fname, '/') : NULL;
            name = name ? name + 1 : info.dli_fname;
            const char *label = name && (!strcmp(name, "bluetoothd") || !strcmp(name, "BlueTool")) ?
                name : "library";
            say("exit frame=%d module=%s image_offset=%#lx", i, label,
                (unsigned long)((uintptr_t)frames[i] - (uintptr_t)info.dli_fbase));
        }
    }
}
static void trace_exit(int status) __attribute__((noreturn));
static void trace_exit(int status) { exit_trace(status); exit(status); }
static void trace_immediate_exit(int status) __attribute__((noreturn));
static void trace_immediate_exit(int status) { exit_trace(status); _exit(status); }

static int is_literal_string(const char *string) {
    if (!string) return 0;
    for (unsigned i = 0; i < 2; i++) {
        uintptr_t p = (uintptr_t)string, begin = (uintptr_t)literal_sections[i];
        if (!begin || p < begin || p - begin >= literal_sizes[i]) continue;
        size_t remaining = literal_sizes[i] - (p - begin);
        return strnlen(string, remaining < 200 ? remaining : 200) < (remaining < 200 ? remaining : 200);
    }
    return 0;
}
static void static_log_format(os_log_type_t type, const char *format, const uint8_t *buffer, uint32_t size) {
    /* Only constants in this executable are printable. Arbitrary logging
     * arguments may contain identities and are never decoded or printed. */
    if (!format) return;
    for (unsigned i = 0; i < 2; i++) {
        uintptr_t p = (uintptr_t)format, begin = (uintptr_t)literal_sections[i];
        if (!begin || p < begin || p - begin >= literal_sizes[i]) continue;
        size_t remaining = literal_sizes[i] - (p - begin);
        size_t count = strnlen(format, remaining < 200 ? remaining : 200);
        if (count == remaining || count >= 200) return;
        if (type >= OS_LOG_TYPE_ERROR || strstr(format, "Unknown") || strstr(format, "initializ") ||
            strcasestr(format, "pair") || strcasestr(format, "connect") ||
            strcasestr(format, "encrypt") || strcasestr(format, "auth") ||
            strstr(format, "HID") || strstr(format, "L2CAP"))
            say("native log format only: %s", format);
        /* The observed native OI failure record has one static description
         * followed by one 32-bit status. Decode ONLY that exact typed record;
         * dynamic strings, other buffers and all private data are omitted. */
        if ((!strcmp(format, "%{public}s (status=%{bluetooth:OI_STATUS}u)") ||
             !strcmp(format, "%{public}s failed with result %{bluetooth:OI_STATUS}u")) && buffer && size == 18 &&
            buffer[1] == 2 && buffer[2] == 0x22 && buffer[3] == 8 && buffer[12] == 0 && buffer[13] == 4) {
            const char *description; uint32_t status;
            memcpy(&description, buffer + 4, sizeof(description));
            memcpy(&status, buffer + 14, sizeof(status));
            say("native OI status=%u description=%s", status,
                is_literal_string(description) ? description : "[dynamic text omitted]");
        }
        if ((!strcmp(format, "Scan stopped improperly with result %{bluetooth:OI_STATUS}u -- restart") ||
             !strcmp(format, "LE_GAP_InitAdvertising failed with status %d")) && buffer && size == 8 &&
            buffer[1] == 1 && buffer[2] == 0 && buffer[3] == 4) {
            uint32_t status; memcpy(&status, buffer + 4, sizeof(status));
            say("native scan/advertising OI status=%u", status);
        }
        if (!strcmp(format, "%{public}s failed with result %{bluetooth:OI_STATUS}u")) {
            static unsigned failures;
            if (failures++ < 24) {
                void *frames[9]; int count = backtrace(frames, 9);
                for (int frame = 0; frame < count; frame++) {
                    Dl_info info = {0};
                    if (dladdr(frames[frame], &info) && info.dli_fbase == (const void *)main_header)
                        say("native operation failure frame=%d image_offset=%#lx", frame,
                            (unsigned long)((uintptr_t)frames[frame] - (uintptr_t)info.dli_fbase));
                }
            }
        }
        return;
    }
}
static void trace_os_log(void *dso, os_log_t log, os_log_type_t type,
    const char *format, uint8_t *buffer, uint32_t size) {
    int saved = errno; static_log_format(type, format, buffer, size); errno = saved;
    _os_log_impl(dso, log, type, format, buffer, size);
}
static void trace_os_error(void *dso, os_log_t log, os_log_type_t type,
    const char *format, uint8_t *buffer, uint32_t size) {
    int saved = errno; static_log_format(type, format, buffer, size); errno = saved;
    _os_log_error_impl(dso, log, type, format, buffer, size);
}

static unsigned char kind(int fd)
{
    return fd >= 0 && fd < FD_LIMIT ? __atomic_load_n(&tracked[fd], __ATOMIC_RELAXED) : 0;
}

static void mark(int fd, unsigned char value)
{
    if (fd >= 0 && fd < FD_LIMIT) __atomic_store_n(&tracked[fd], value, __ATOMIC_RELAXED);
}

static int bt_name(const char *s)
{
    return s && (strstr(s, "Bluetooth") || strstr(s, "bluetooth") || strstr(s, "btwake") ||
                 strstr(s, "Converged") || strstr(s, "ACIPC") || strstr(s, "/dev/bt"));
}

/* BlueTool's request operation is protocol metadata. Arguments and arbitrary
 * strings may contain addresses or pairing data, so only their sizes appear. */
static const char *trace_xpc_string(xpc_object_t object, const char *key) {
    const char *value = xpc_dictionary_get_string(object, key);
    if (key && (!strcmp(key, "kMsgId") || !strcmp(key, "script") ||
                !strcmp(key, "command") || !strcmp(key, "firmware"))) {
        const char *operation = "[content omitted]";
        if (value && !strcmp(key, "kMsgId") &&
            (!strcmp(value, "RunExternalScript") || !strcmp(value, "RunBuiltinScript") ||
             !strcmp(value, "RunCommand"))) operation = value;
        say("XPC string key=%s present=%u bytes=%zu operation=%s", key,
            value != NULL, value ? strlen(value) : 0, operation);
    }
    return value;
}
static xpc_object_t trace_xpc_value(xpc_object_t object, const char *key) {
    xpc_object_t value = xpc_dictionary_get_value(object, key);
    if (key && (!strcmp(key, "kMsgArgs") || !strcmp(key, "script") || !strcmp(key, "command")))
        say("XPC value key=%s present=%u", key, value != NULL);
    return value;
}
static int trace_access(const char *path, int mode) {
    int result = access(path, mode), saved = errno;
    if (path && !strncmp(path, "/dev/bt", 7))
        say("access path=%s mode=%d result=%d errno=%d", path, mode, result, result < 0 ? saved : 0);
    errno = saved; return result;
}

static CFMutableDictionaryRef trace_matching(const char *name)
{
    CFMutableDictionaryRef r = IOServiceMatching(name);
    if (bt_name(name)) say("IOServiceMatching class=%s result=%s", name, r ? "dictionary" : "null");
    return r;
}

static CFMutableDictionaryRef trace_name_matching(const char *name)
{
    CFMutableDictionaryRef r = IOServiceNameMatching(name);
    if (bt_name(name)) say("IOServiceNameMatching name=%s result=%s", name, r ? "dictionary" : "null");
    return r;
}

static void matching_name(CFDictionaryRef matching, char name[128])
{
    memset(name, 0, 128);
    if (!matching) return;
    CFTypeRef value = CFDictionaryGetValue(matching, CFSTR("IOProviderClass"));
    if (!value) value = CFDictionaryGetValue(matching, CFSTR("IONameMatch"));
    if (value && CFGetTypeID(value) == CFStringGetTypeID())
        (void)CFStringGetCString(value, name, 128, kCFStringEncodingUTF8);
    name[127] = 0;
}

static io_service_t trace_get_matching(mach_port_t port, CFDictionaryRef matching)
{
    char name[128]; matching_name(matching, name);
    io_service_t r = IOServiceGetMatchingService(port, matching);
    say("IOServiceGetMatchingService match=%s result=%s", name, r ? "found" : "absent");
    return r;
}

static kern_return_t trace_get_matches(mach_port_t port, CFDictionaryRef matching, io_iterator_t *iterator)
{
    char name[128]; matching_name(matching, name);
    kern_return_t r = IOServiceGetMatchingServices(port, matching, iterator);
    say("IOServiceGetMatchingServices match=%s result=%#x", name, r);
    return r;
}

static kern_return_t trace_add_matching(IONotificationPortRef port, const io_name_t type,
    CFDictionaryRef matching, IOServiceMatchingCallback callback, void *reference, io_iterator_t *iterator)
{
    char name[128]; matching_name(matching, name);
    kern_return_t r = IOServiceAddMatchingNotification(port, type, matching, callback, reference, iterator);
    say("IOServiceAddMatchingNotification match=%s type=%s result=%#x", name, type, r);
    return r;
}

static CFTypeRef trace_property(io_registry_entry_t entry, CFStringRef key,
    CFAllocatorRef allocator, IOOptionBits options)
{
    CFTypeRef r = IORegistryEntryCreateCFProperty(entry, key, allocator, options);
    char name[160] = {0};
    if (key && CFStringGetCString(key, name, sizeof(name), kCFStringEncodingUTF8))
        say("IORegistryEntryCreateCFProperty key=%s result=%s", name, r ? "present" : "absent");
    return r;
}

static CFTypeRef trace_search_property(io_registry_entry_t entry, const io_name_t plane,
    CFStringRef key, CFAllocatorRef allocator, IOOptionBits options)
{
    CFTypeRef r = IORegistryEntrySearchCFProperty(entry, plane, key, allocator, options);
    char name[160] = {0};
    if (key && CFStringGetCString(key, name, sizeof(name), kCFStringEncodingUTF8))
        say("IORegistryEntrySearchCFProperty plane=%s key=%s result=%s", plane, name,
            r ? "present" : "absent");
    return r;
}

static kern_return_t trace_open_service(io_service_t service, task_port_t task,
                                      uint32_t type, io_connect_t *connection)
{
    io_name_t name = {0}, class_name = {0};
    (void)IORegistryEntryGetName(service, name);
    (void)IOObjectGetClass(service, class_name);
    kern_return_t r = IOServiceOpen(service, task, type, connection);
    say("IOServiceOpen name=%s class=%s type=%u result=%#x", name, class_name, type, r);
    return r;
}

static kern_return_t trace_method(mach_port_t c, uint32_t selector,
    const uint64_t *input, uint32_t input_count, const void *structure, size_t structure_size,
    uint64_t *output, uint32_t *output_count, void *output_structure, size_t *output_size)
{
    uint32_t out_count = output_count ? *output_count : 0;
    size_t out_size = output_size ? *output_size : 0;
    kern_return_t r = IOConnectCallMethod(c, selector, input, input_count, structure,
        structure_size, output, output_count, output_structure, output_size);
    say("IOConnectCallMethod selector=%u in=%u/%zu out-cap=%u/%zu result=%#x out=%u/%zu",
        selector, input_count, structure_size, out_count, out_size, r,
        output_count ? *output_count : 0, output_size ? *output_size : 0);
    return r;
}

static kern_return_t trace_scalar_method(mach_port_t c, uint32_t selector,
    const uint64_t *input, uint32_t input_count, uint64_t *output, uint32_t *output_count)
{
    kern_return_t r = IOConnectCallScalarMethod(c, selector, input, input_count, output, output_count);
    say("IOConnectCallScalarMethod selector=%u in=%u result=%#x out=%u", selector,
        input_count, r, output_count ? *output_count : 0);
    return r;
}

static kern_return_t trace_struct_method(mach_port_t c, uint32_t selector,
    const void *input, size_t input_size, void *output, size_t *output_size)
{
    kern_return_t r = IOConnectCallStructMethod(c, selector, input, input_size, output, output_size);
    say("IOConnectCallStructMethod selector=%u in=%zu result=%#x out=%zu", selector,
        input_size, r, output_size ? *output_size : 0);
    return r;
}

static kern_return_t trace_async_method(mach_port_t c, uint32_t selector, mach_port_t wake,
    uint64_t *reference, uint32_t reference_count, const uint64_t *input, uint32_t input_count,
    const void *structure, size_t structure_size, uint64_t *output, uint32_t *output_count,
    void *output_structure, size_t *output_size)
{
    kern_return_t r = IOConnectCallAsyncMethod(c, selector, wake, reference, reference_count,
        input, input_count, structure, structure_size, output, output_count, output_structure, output_size);
    say("IOConnectCallAsyncMethod selector=%u refs=%u in=%u/%zu result=%#x", selector,
        reference_count, input_count, structure_size, r);
    return r;
}

static kern_return_t trace_map(io_connect_t c, uint32_t type, task_port_t task,
    mach_vm_address_t *address, mach_vm_size_t *size, IOOptionBits options)
{
    kern_return_t r = IOConnectMapMemory64(c, type, task, address, size, options);
    say("IOConnectMapMemory64 type=%u options=%#x result=%#x size=%llu", type, options,
        r, (unsigned long long)(size ? *size : 0));
    return r;
}

static kern_return_t trace_notification(io_connect_t c, uint32_t type,
                                        mach_port_t port, uintptr_t reference)
{
    kern_return_t r = IOConnectSetNotificationPort(c, type, port, reference);
    say("IOConnectSetNotificationPort type=%u result=%#x", type, r);
    return r;
}

static int trace_open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args; va_start(args, flags); mode = (mode_t)va_arg(args, int); va_end(args);
    }
    int r = open(path, flags, mode), saved = errno;
    if (bt_name(path) && !strncmp(path, "/dev/", 5)) {
        mark(r, FD_BLUETOOTH);
        say("open path=%s flags=%#x result=%d errno=%d", path, flags, r, r < 0 ? saved : 0);
    }
    errno = saved;
    return r;
}

static int trace_socket(int domain, int type, int protocol)
{
    int r = socket(domain, type, protocol), saved = errno;
    if (domain == PF_SYSTEM && protocol == SYSPROTO_CONTROL) {
        mark(r, FD_CONTROL);
        say("socket PF_SYSTEM type=%d protocol=%d result=%d errno=%d", type, protocol,
            r, r < 0 ? saved : 0);
    }
    errno = saved;
    return r;
}

/* Only CTLIOCGINFO's specified pointer is inspected. */
__attribute__((used, noinline)) static int trace_ioctl_impl(int fd, unsigned long request, void *argument)
{
    char name[MAX_KCTL_NAME] = {0};
    if (request == CTLIOCGINFO && argument)
        memcpy(name, ((struct ctl_info *)argument)->ctl_name, sizeof(name) - 1);
    int r = ioctl(fd, request, argument), saved = errno;
    if (bt_name(name)) {
        if (r == 0) mark(fd, FD_BLUETOOTH);
        say("CTLIOCGINFO name=%s result=%d errno=%d id=%u", name, r, r < 0 ? saved : 0,
            r == 0 ? ((struct ctl_info *)argument)->ctl_id : 0);
    } else if (kind(fd)) {
        say("ioctl fd=%d request=%#lx result=%d errno=%d", fd, request, r, r < 0 ? saved : 0);
    }
    errno = saved;
    return r;
}
#include "lemonade_bt_ioctl_bridge.h"

static int trace_connect(int fd, const struct sockaddr *address, socklen_t length)
{
    int r = connect(fd, address, length), saved = errno;
    if (!r && kind(fd) == FD_BLUETOOTH) {
        pthread_mutex_lock(&hci_lock);
        hci_fd = fd;
        hci_events = hci_inquiries = hci_legacy_reports = hci_le_reports = 0;
        hci_acl_rx = hci_acl_tx = 0;
        lemonade_bt_parser_init(&hci_observer, LEMONADE_BT_FROM_CONTROLLER, 0);
        lemonade_bt_parser_init(&hci_transmit_observer, LEMONADE_BT_FROM_HOST, 0);
        pthread_mutex_unlock(&hci_lock);
    }
    if (kind(fd)) say("connect fd=%d family=%d len=%u result=%d errno=%d", fd,
        address && length >= 2 ? address->sa_family : -1, length, r, r < 0 ? saved : 0);
    errno = saved;
    return r;
}

static int trace_getopt(int fd, int level, int name, void *value, socklen_t *length)
{
    int r = getsockopt(fd, level, name, value, length), saved = errno;
    if (kind(fd)) say("getsockopt fd=%d level=%d option=%#x result=%d errno=%d len=%u", fd,
        level, name, r, r < 0 ? saved : 0, length ? *length : 0);
    errno = saved;
    return r;
}

static int trace_setopt(int fd, int level, int name, const void *value, socklen_t length)
{
    int r = setsockopt(fd, level, name, value, length), saved = errno;
    if (kind(fd)) say("setsockopt fd=%d level=%d option=%#x len=%u result=%d errno=%d", fd,
        level, name, length, r, r < 0 ? saved : 0);
    errno = saved;
    return r;
}

static ssize_t trace_write(int fd, const void *buffer, size_t length)
{
    ssize_t r = write(fd, buffer, length); int saved = errno;
    if (!logging && kind(fd) == FD_BLUETOOTH) {
        pthread_mutex_lock(&hci_lock);
        if (fd == hci_fd && r > 0 && !hci_transmit_observer.error &&
                lemonade_bt_parse(&hci_transmit_observer, buffer, (size_t)r, observe_transmit, NULL, NULL))
            say("H4 TX observation framing error=%u; native bytes unchanged", hci_transmit_observer.error);
        pthread_mutex_unlock(&hci_lock);
        say("write fd=%d bytes=%zu result=%zd errno=%d", fd, length, r, r < 0 ? saved : 0);
    }
    errno = saved;
    return r;
}

static ssize_t trace_read(int fd, void *buffer, size_t length)
{
    ssize_t r = read(fd, buffer, length); int saved = errno;
    if (kind(fd) == FD_BLUETOOTH) {
        pthread_mutex_lock(&hci_lock);
        if (fd == hci_fd && r > 0 && !hci_observer.error &&
                lemonade_bt_parse(&hci_observer, buffer, (size_t)r, observe_hci, NULL, NULL))
            say("H4 observation stopped at framing error=%u; native bytes unchanged", hci_observer.error);
        if (hci_events < 20 || r <= 0)
            say("read fd=%d capacity=%zu result=%zd errno=%d", fd, length, r, r < 0 ? saved : 0);
        pthread_mutex_unlock(&hci_lock);
    }
    errno = saved;
    return r;
}

static int trace_close(int fd)
{
    unsigned char was = kind(fd);
    if (was == FD_BLUETOOTH) {
        pthread_mutex_lock(&hci_lock);
        if (fd == hci_fd) {
            say("real HCI final events=%u inquiry_reports=%u le_reports=%u framing_error=%u",
                hci_events, hci_legacy_reports, hci_le_reports, hci_observer.error);
            hci_fd = -1; memset(&hci_observer, 0, sizeof(hci_observer));
        }
        pthread_mutex_unlock(&hci_lock);
    }
    mark(fd, 0);
    int r = close(fd), saved = errno;
    if (was) say("close fd=%d result=%d errno=%d", fd, r, r < 0 ? saved : 0);
    errno = saved;
    return r;
}

__attribute__((constructor)) static void trace_start(void)
{
    const char *name = getprogname();
    enabled = name && (!strcmp(name, "bluetoothd") || !strcmp(name, "BlueTool"));
    /* The custom RAM loader's injected image can occupy index zero. Locate
     * the executable by its exact basename instead of assuming image order. */
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *path = _dyld_get_image_name(i);
        const char *base = path ? strrchr(path, '/') : NULL;
        base = base ? base + 1 : path;
        if (!name || !base || strcmp(name, base)) continue;
        const struct mach_header_64 *header = (const struct mach_header_64 *)_dyld_get_image_header(i);
        main_header = header;
        literal_sections[0] = getsectiondata(header, "__TEXT", "__cstring", &literal_sizes[0]);
        literal_sections[1] = getsectiondata(header, "__TEXT", "__oslogstring", &literal_sizes[1]);
        break;
    }
    say("probe loaded; observation only; limit=%u; payload logging disabled", TRACE_LIMIT);
}

#define INTERPOSE(replacement, original) { (const void *)(replacement), (const void *)(original) }
__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement;
    const void *original;
} interposed[] = {
    INTERPOSE(trace_exit, exit),
    INTERPOSE(trace_immediate_exit, _exit),
    INTERPOSE(trace_os_log, _os_log_impl),
    INTERPOSE(trace_os_error, _os_log_error_impl),
    INTERPOSE(trace_xpc_string, xpc_dictionary_get_string),
    INTERPOSE(trace_xpc_value, xpc_dictionary_get_value),
    INTERPOSE(trace_access, access),
    INTERPOSE(trace_matching, IOServiceMatching),
    INTERPOSE(trace_name_matching, IOServiceNameMatching),
    INTERPOSE(trace_get_matching, IOServiceGetMatchingService),
    INTERPOSE(trace_get_matches, IOServiceGetMatchingServices),
    INTERPOSE(trace_add_matching, IOServiceAddMatchingNotification),
    INTERPOSE(trace_property, IORegistryEntryCreateCFProperty),
    INTERPOSE(trace_search_property, IORegistryEntrySearchCFProperty),
    INTERPOSE(trace_open_service, IOServiceOpen),
    INTERPOSE(trace_method, IOConnectCallMethod),
    INTERPOSE(trace_scalar_method, IOConnectCallScalarMethod),
    INTERPOSE(trace_struct_method, IOConnectCallStructMethod),
    INTERPOSE(trace_async_method, IOConnectCallAsyncMethod),
    INTERPOSE(trace_map, IOConnectMapMemory64),
    INTERPOSE(trace_notification, IOConnectSetNotificationPort),
    INTERPOSE(trace_open, open),
    INTERPOSE(trace_socket, socket),
    INTERPOSE(trace_ioctl, ioctl),
    INTERPOSE(trace_connect, connect),
    INTERPOSE(trace_getopt, getsockopt),
    INTERPOSE(trace_setopt, setsockopt),
    INTERPOSE(trace_read, read),
    INTERPOSE(trace_write, write),
    INTERPOSE(trace_close, close),
};
