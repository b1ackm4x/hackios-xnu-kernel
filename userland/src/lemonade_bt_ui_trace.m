/* Explicit development-only observer for the real Preferences process.
 * No view text, device identifiers, payloads, or application memory is logged. */
#import <UIKit/UIKit.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/arm/thread_status.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

static mach_port_t main_thread;
static void say(const char *format, ...) {
    char line[768]; va_list arguments;
    va_start(arguments, format);
    int length = vsnprintf(line, sizeof(line), format, arguments);
    va_end(arguments);
    if (length <= 0) return;
    int fd = open("/dev/console", O_WRONLY | O_NOCTTY);
    if (fd >= 0) {
        write(fd, line, (size_t)length < sizeof(line) ? (size_t)length : sizeof(line)-1);
        close(fd);
    }
}
static void address(const char *kind, uintptr_t pc) {
    Dl_info info = {0};
    if (dladdr((void *)pc, &info) && info.dli_fname && info.dli_fbase) {
        const char *name = strrchr(info.dli_fname, '/');
        say("bt-ui: %s %s+0x%lx\n", kind, name ? name+1 : info.dli_fname,
            (unsigned long)(pc - (uintptr_t)info.dli_fbase));
    }
}
static void sample_main(unsigned tick) {
    arm_thread_state64_t state = {0};
    mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
    kern_return_t result = thread_get_state(main_thread, ARM_THREAD_STATE64,
        (thread_state_t)&state, &count);
    say("bt-ui: main sample tick=%u result=%d\n", tick, result);
    if (result != KERN_SUCCESS) return;
    address("pc", (uintptr_t)arm_thread_state64_get_pc(state));
    address("lr", (uintptr_t)arm_thread_state64_get_lr(state));
    uintptr_t fp = (uintptr_t)arm_thread_state64_get_fp(state);
    for (unsigned i = 0; fp && !(fp & 15) && i < 12; i++) {
        uintptr_t frame[2]; vm_size_t copied = 0;
        if (vm_read_overwrite(mach_task_self(), fp, sizeof(frame),
                (vm_address_t)frame, &copied) != KERN_SUCCESS || copied != sizeof(frame)) break;
        address("frame", frame[1]);
        if (frame[0] <= fp || frame[0] - fp > 0x100000) break;
        fp = frame[0];
    }
}
static void views(UIView *view, unsigned depth, unsigned *budget) {
    if (!view || depth > 10 || !*budget) return;
    (*budget)--;
    say("bt-ui: view depth=%u class=%s hidden=%d size=%.0fx%.0f\n", depth,
        NSStringFromClass(view.class).UTF8String, view.hidden, view.bounds.size.width, view.bounds.size.height);
    if ([view isKindOfClass:UITableView.class]) {
        UITableView *table = (UITableView *)view;
        NSInteger sections = table.numberOfSections;
        say("bt-ui: table sections=%ld\n", (long)sections);
        for (NSInteger i = 0; i < sections && i < 16; i++)
            say("bt-ui: table section=%ld rows=%ld\n", (long)i, (long)[table numberOfRowsInSection:i]);
    }
    for (UIView *child in view.subviews) views(child, depth+1, budget);
}
static void controllers(UIViewController *controller, unsigned depth) {
    if (!controller || depth > 6) return;
    say("bt-ui: controller depth=%u class=%s loaded=%d\n", depth,
        NSStringFromClass(controller.class).UTF8String, controller.isViewLoaded);
    if ([NSStringFromClass(controller.class) isEqualToString:@"BTSDevicesController"] && controller.isViewLoaded) {
        unsigned budget = 70;
        views(controller.view, 0, &budget);
    }
    for (UIViewController *child in controller.childViewControllers) controllers(child, depth+1);
    controllers(controller.presentedViewController, depth+1);
}
static void inspect_ui(unsigned tick) {
    UIApplication *app = UIApplication.sharedApplication;
    say("bt-ui: main queue tick=%u state=%ld scenes=%lu\n", tick,
        (long)app.applicationState, (unsigned long)app.connectedScenes.count);
    unsigned budget = 60;
    for (UIScene *scene in app.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class]) continue;
        for (UIWindow *window in ((UIWindowScene *)scene).windows) {
            say("bt-ui: window class=%s key=%d hidden=%d\n", NSStringFromClass(window.class).UTF8String,
                window.keyWindow, window.hidden);
            controllers(window.rootViewController, 0);
            if (tick == 0 || tick == 3) views(window, 0, &budget);
        }
    }
}
__attribute__((constructor)) static void start(void) {
    char args[2048] = {0}; size_t length = sizeof(args);
    if (strcmp(getprogname(), "Preferences") || !pthread_main_np() ||
            sysctlbyname("kern.bootargs", args, &length, NULL, 0)) return;
    args[sizeof(args)-1] = 0;
    BOOL bt = NO, ui = NO;
    for (char *word = strtok(args, " "); word; word = strtok(NULL, " ")) {
        if (!strcmp(word, "lemonade_bt=6")) bt = YES;
        if (!strcmp(word, "lemonade_bt_ui=1")) ui = YES;
    }
    if (!bt || !ui) return;
    main_thread = mach_thread_self();
    say("bt-ui: Preferences observer initialized\n");
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        for (unsigned i = 0; i < 6; i++) {
            sleep(i ? 15 : 5);
            sample_main(i);
            dispatch_async(dispatch_get_main_queue(), ^{ inspect_ui(i); });
        }
        mach_port_deallocate(mach_task_self(), main_thread);
        say("bt-ui: bounded observer ended\n");
    });
}
