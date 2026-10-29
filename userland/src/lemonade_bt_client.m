/* Bounded native BluetoothManager acceptance client. No device identities or
 * advertisement payloads are printed. This client never fabricates state. */
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <objc/runtime.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

static NSInvocation *invocation(id target, NSString *name, NSUInteger arguments) {
    SEL selector = NSSelectorFromString(name);
    NSMethodSignature *signature = [target methodSignatureForSelector:selector];
    if (!signature || signature.numberOfArguments != arguments + 2) {
        printf("bt-client: unavailable selector=%s\n", name.UTF8String); return nil;
    }
    NSInvocation *call = [NSInvocation invocationWithMethodSignature:signature];
    call.target = target; call.selector = selector;
    return call;
}
static long long number(id target, NSString *name) {
    NSInvocation *call = invocation(target, name, 0);
    if (!call) return -1;
    const char *type = call.methodSignature.methodReturnType;
    NSUInteger size = call.methodSignature.methodReturnLength;
    if (!strchr("BcCsSiIlLqQ", type[0]) || !size || size > 8) return -1;
    uint64_t value = 0;
    [call invoke]; [call getReturnValue:&value];
    return (long long)value;
}
static BOOL set_boolean(id target, NSString *name, BOOL value) {
    NSInvocation *call = invocation(target, name, 1);
    if (!call) return NO;
    const char *type = [call.methodSignature getArgumentTypeAtIndex:2];
    if (strcmp(type, @encode(BOOL)) && strcmp(type, "c") && strcmp(type, "B")) {
        printf("bt-client: unsupported boolean signature selector=%s\n", name.UTF8String); return NO;
    }
    [call setArgument:&value atIndex:2]; [call invoke];
    printf("bt-client: native request selector=%s value=%u\n", name.UTF8String, value);
    return YES;
}
static void run_for(double seconds) {
    NSDate *until = [NSDate dateWithTimeIntervalSinceNow:seconds];
    while (until.timeIntervalSinceNow > 0)
        if (![[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:until]) usleep(10000);
}
static void open_bluetooth_settings(void) {
    /* The compatibility framework may be absent from this system image while
     * LaunchServices is already loaded by Foundation/CoreServices. */
    dlopen("/System/Library/Frameworks/CoreServices.framework/CoreServices", RTLD_NOW);
    dlopen("/System/Library/Frameworks/MobileCoreServices.framework/MobileCoreServices", RTLD_NOW);
    Class cls = NSClassFromString(@"LSApplicationWorkspace");
    if (!cls) { puts("bt-client: Settings launcher class unavailable"); return; }
    NSInvocation *shared = invocation(cls, @"defaultWorkspace", 0);
    if (!shared || strcmp(shared.methodSignature.methodReturnType, @encode(id))) {
        puts("bt-client: Settings workspace signature unavailable"); return;
    }
    [shared invoke]; __unsafe_unretained id workspace = nil; [shared getReturnValue:&workspace];
    NSInvocation *open = invocation(workspace, @"openSensitiveURL:withOptions:", 2);
    if (!open || strcmp([open.methodSignature getArgumentTypeAtIndex:2], @encode(id)) ||
        strcmp([open.methodSignature getArgumentTypeAtIndex:3], @encode(id)) ||
        open.methodSignature.methodReturnLength != sizeof(BOOL)) {
        puts("bt-client: Settings URL signature unavailable"); return;
    }
    NSURL *url = [NSURL URLWithString:@"prefs:root=Bluetooth"];
    NSDictionary *options = @{};
    [open setArgument:&url atIndex:2]; [open setArgument:&options atIndex:3];
    [open invoke]; BOOL result = NO; [open getReturnValue:&result];
    printf("bt-client: native Bluetooth Settings launch requested result=%u\n", result);
}
static int observe(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    char args[2048] = {0}; size_t length = sizeof(args);
    if (sysctlbyname("kern.bootargs", args, &length, NULL, 0)) return 1;
    args[sizeof(args) - 1] = 0;
    BOOL enabled = NO, ui = NO;
    for (char *word = strtok(args, " "); word; word = strtok(NULL, " ")) {
        if (!strcmp(word, "lemonade_bt=6")) enabled = YES;
        if (!strcmp(word, "lemonade_bt_ui=1")) ui = YES;
    }
    if (!enabled) return 0;
    if (argc == 2 && !strcmp(argv[1], "--settings-only")) {
        if (!ui) return 1;
        alarm(100);
        sleep(30);
        @autoreleasepool { open_bluetooth_settings(); }
        puts("bt-client: one-shot Settings launcher ended; no BluetoothManager requests");
        return 0;
    }
    if (argc != 1) return 2;
    alarm(150);
    puts("bt-client: bounded test started; waiting for native service");
    sleep(10);
    @autoreleasepool {
        puts("bt-client: loading native framework");
        if (!dlopen("/System/Library/PrivateFrameworks/BluetoothManager.framework/BluetoothManager", RTLD_NOW)) {
            puts("bt-client: native framework unavailable"); return 1;
        }
        Class cls = NSClassFromString(@"BluetoothManager");
        NSInvocation *shared = invocation(cls, @"sharedInstance", 0);
        if (!shared || strcmp(shared.methodSignature.methodReturnType, @encode(id))) return 1;
        puts("bt-client: requesting native manager");
        [shared invoke]; __unsafe_unretained id instance = nil; [shared getReturnValue:&instance];
        id manager = instance;
        if (!manager) { puts("bt-client: no native manager"); return 1; }
        puts("bt-client: native manager obtained");
        unsigned methods_count = 0;
        Method *methods = class_copyMethodList(cls, &methods_count);
        for (unsigned i = 0; methods && i < methods_count; i++) {
            const char *name = sel_getName(method_getName(methods[i]));
            if (strcasestr(name, "scan") || strcasestr(name, "discover"))
                printf("bt-client: native discovery API selector=%s signature=%s\n", name, method_getTypeEncoding(methods[i]));
        }
        free(methods);
        __block unsigned reports = 0;
        id observer = [[NSNotificationCenter defaultCenter]
            addObserverForName:@"BluetoothDeviceDiscoveredNotification" object:nil queue:nil
            usingBlock:^(NSNotification *notification) { (void)notification; reports++; }];
        run_for(5);
        printf("bt-client: initial enabled=%lld powered=%lld available=%lld\n",
            number(manager, @"enabled"), number(manager, @"powered"), number(manager, @"available"));
        set_boolean(manager, @"setEnabled:", YES);
        set_boolean(manager, @"setPowered:", YES);
        /* The complete base initializes Wi-Fi and graphics concurrently with
         * the UART firmware worker. Give the real native initializer a bounded
         * startup window before sampling its steady-state availability. */
        unsigned waited = 0;
        while (waited < 45 && (number(manager, @"powered") != 1 || number(manager, @"available") != 1)) {
            run_for(1); waited++;
        }
        printf("bt-client: initialization wait=%u powered=%lld available=%lld\n",
            waited, number(manager, @"powered"), number(manager, @"available"));
        if (number(manager, @"powered") != 1 || number(manager, @"available") != 1) return 1;
        if (ui) open_bluetooth_settings();
        BOOL scanning = NO;
        for (unsigned i = 0; i < 14; i++) {
            run_for(5);
            long long powered = number(manager, @"powered"), available = number(manager, @"available");
            printf("bt-client: state tick=%u powered=%lld available=%lld discovery_reports=%u\n",
                i, powered, available, reports);
            if (!scanning && powered == 1 && available == 1)
                scanning = set_boolean(manager, @"setDeviceScanningEnabled:", YES);
        }
        if (scanning) set_boolean(manager, @"setDeviceScanningEnabled:", NO);
        [[NSNotificationCenter defaultCenter] removeObserver:observer];
        puts("bt-client: bounded native client ended; Settings acceptance remains separate");
    }
    return 0;
}

extern void xpc_transaction_begin(void);
extern void xpc_transaction_end(void);
int main(int argc, char **argv) {
    // Timed observations are active work even while the run loop is waiting.
    xpc_transaction_begin();
    int result=observe(argc,argv);
    xpc_transaction_end();
    return result;
}
