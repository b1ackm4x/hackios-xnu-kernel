/* Board firmware bootstrap for the native iOS 16.5 BlueTool XPC contract.
 * Qualcomm firmware is loaded and verified by the kernel UART owner before
 * publishing LemonadeTransportReady. This service does not synthesize HCI
 * responses, substitute a chip identity, execute scripts, or log addresses.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dispatch/dispatch.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

/* Public C ABI from Apple's xpc/xpc.h and xpc/connection.h. This SDK omits
 * those headers. Opaque objects are never dereferenced. */
typedef struct _xpc_object_s *xpc_object_t;
typedef struct _xpc_connection_s *xpc_connection_t;
struct _xpc_type_s;
extern const struct _xpc_type_s _xpc_type_dictionary, _xpc_type_connection;
extern const struct _xpc_type_s *xpc_get_type(xpc_object_t);
extern xpc_connection_t xpc_connection_create_mach_service(const char *, dispatch_queue_t, uint64_t);
extern void xpc_connection_set_event_handler(xpc_connection_t, void (^)(xpc_object_t));
extern void xpc_connection_resume(xpc_connection_t);
extern void xpc_connection_cancel(xpc_connection_t);
extern uid_t xpc_connection_get_euid(xpc_connection_t);
extern void xpc_connection_send_message(xpc_connection_t, xpc_object_t);
extern xpc_object_t xpc_dictionary_create_reply(xpc_object_t);
extern xpc_object_t xpc_dictionary_get_value(xpc_object_t, const char *);
extern const char *xpc_dictionary_get_string(xpc_object_t, const char *);
extern void xpc_dictionary_set_int64(xpc_object_t, const char *, int64_t);
extern void xpc_dictionary_set_string(xpc_object_t, const char *, const char *);
extern void xpc_release(xpc_object_t);

static bool native_mode(void) {
    char args[2048] = {0}; size_t size = sizeof(args) - 1;
    if (sysctlbyname("kern.bootargs", args, &size, NULL, 0)) return false;
    const char *mode = strstr(args, "lemonade_bt=6");
    return mode && (mode == args || mode[-1] == ' ') && (!mode[13] || mode[13] == ' ');
}

static bool ready(void) {
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

static void request(xpc_connection_t peer, xpc_object_t event) {
    if (xpc_get_type(event) != &_xpc_type_dictionary) return;
    const char *operation = xpc_dictionary_get_string(event, "kMsgId");
    xpc_object_t args = xpc_dictionary_get_value(event, "kMsgArgs");
    const char *script = args && xpc_get_type(args) == &_xpc_type_dictionary ?
        xpc_dictionary_get_string(args, "script") : NULL;
    bool supported = operation && !strcmp(operation, "RunBuiltinScript") && script &&
        (!strcmp(script, "boot") || !strcmp(script, "init"));
    bool verified = false;
    if (supported) {
        for (unsigned i = 0; i < 100 && !(verified = ready()); i++) usleep(250000);
    }
    xpc_object_t reply = xpc_dictionary_create_reply(event);
    if (!reply) return;
    xpc_dictionary_set_int64(reply, "result", verified ? 0 : 1);
    if (verified) {
        /* BlueTool's manufacturer is a board-module vendor identifier, not
         * the HCI Bluetooth company ID. Zero explicitly means unspecified. */
        xpc_dictionary_set_int64(reply, "manufacturer", 0);
        xpc_dictionary_set_string(reply, "firmware", "Qualcomm WCN6851; kernel-verified RAM firmware");
    }
    xpc_connection_send_message(peer, reply);
    xpc_release(reply);
    printf("bt-bootstrap: request=%s kernel_firmware_ready=%u result=%u\n",
        supported ? script : "unsupported", verified, verified ? 0 : 1);
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (!native_mode()) { fprintf(stderr, "bt-bootstrap: native mode required\n"); return 1; }
    xpc_connection_t listener = xpc_connection_create_mach_service("com.apple.BlueTool", NULL, 1);
    if (!listener) return 1;
    xpc_connection_set_event_handler(listener, ^(xpc_object_t object) {
        if (xpc_get_type(object) != &_xpc_type_connection) return;
        xpc_connection_t peer = (xpc_connection_t)object;
        uid_t uid = xpc_connection_get_euid(peer);
        if (uid != 0 && uid != 501) { xpc_connection_cancel(peer); return; }
        xpc_connection_set_event_handler(peer, ^(xpc_object_t event) { request(peer, event); });
        xpc_connection_resume(peer);
    });
    xpc_connection_resume(listener);
    printf("bt-bootstrap: board service listening; real firmware readiness required\n");
    dispatch_main();
}
