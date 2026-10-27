/* Safari's page-rendering policy for the measured 120 Hz RAM desktop.
 * Seed its real experimental preference once in the fresh RAM user profile.
 * Subsequent changes in Safari settings remain effective. No frame counters,
 * JavaScript clocks or CADisplayLink getters are replaced.
 */
#import <Foundation/Foundation.h>
#import <WebKit/WebKit.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <sys/sysctl.h>
#include <stdlib.h>
#include <string.h>

static id (*original_init)(id, SEL, CGRect, WKWebViewConfiguration *);

static BOOL argument(const char *args, const char *value)
{
    size_t n = strlen(value);
    for (const char *p = args; *p;) {
        while (*p == ' ') ++p;
        size_t length = strcspn(p, " ");
        if (length == n && !memcmp(p, value, n)) return YES;
        p += length;
    }
    return NO;
}

static void configure(WKPreferences *preferences)
{
    SEL features = NSSelectorFromString(@"_features");
    SEL setter = NSSelectorFromString(@"_setEnabled:forFeature:");
    SEL keyForFeature = NSSelectorFromString(@"safari_keyForWebKitExperimentalFeature:");
    if (![WKPreferences respondsToSelector:features] ||
        ![preferences respondsToSelector:setter] ||
        ![NSUserDefaults respondsToSelector:keyForFeature]) return;

    NSArray *all = ((id (*)(id, SEL))objc_msgSend)(WKPreferences.class, features);
    for (id feature in all) {
        SEL keySelector = NSSelectorFromString(@"key");
        if (![feature respondsToSelector:keySelector]) continue;
        NSString *name = ((id (*)(id, SEL))objc_msgSend)(feature, keySelector);
        if (![name isEqualToString:@"PreferPageRenderingUpdatesNear60FPSEnabled"]) continue;
        NSString *key = ((id (*)(id, SEL, id))objc_msgSend)(NSUserDefaults.class, keyForFeature, feature);
        if (![key isKindOfClass:NSString.class] || !key.length) return;
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSString *marker = @"LemonadeSafariRefreshDefaultInitialized";
        BOOL initialized = [defaults boolForKey:marker];
        if (!initialized) {
            [defaults setBool:NO forKey:key];
            [defaults setBool:YES forKey:marker];
            [defaults synchronize];
        }
        BOOL prefer60 = [defaults boolForKey:key];
        ((void (*)(id, SEL, BOOL, id))objc_msgSend)(preferences, setter, prefer60, feature);
        return;
    }
}

static id web_init(id self, SEL selector, CGRect frame, WKWebViewConfiguration *configuration)
{
    configure(configuration.preferences);
    return original_init(self, selector, frame, configuration);
}

__attribute__((constructor)) static void safari_refresh_init(void)
{
    if (strcmp(getprogname(), "MobileSafari")) return;
    char args[2048] = {0}; size_t length = sizeof(args) - 1;
    if (sysctlbyname("kern.bootargs", args, &length, NULL, 0) ||
        !argument(args, "lemonade_runtime=1") ||
        !argument(args, "lemonade_display_hz=120") ||
        !argument(args, "var_store=0")) return;
    io_service_t service = IOServiceGetMatchingService(0, IOServiceMatching("IOMobileFramebuffer"));
    if (!service) return;
    CFTypeRef value = IORegistryEntryCreateCFProperty(service, CFSTR("RedwoodMeasuredRefreshHz"), NULL, 0);
    int measured = 0;
    if (value && CFGetTypeID(value) == CFNumberGetTypeID())
        CFNumberGetValue(value, kCFNumberIntType, &measured);
    if (value) CFRelease(value);
    IOObjectRelease(service);
    if (measured != 120) return;
    Method method = class_getInstanceMethod(WKWebView.class, @selector(initWithFrame:configuration:));
    if (!method) return;
    original_init = (void *)method_getImplementation(method);
    method_setImplementation(method, (IMP)web_init);
}
