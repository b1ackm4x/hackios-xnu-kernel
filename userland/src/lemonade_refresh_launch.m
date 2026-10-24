/* Bounded diagnostic for this one test bundle; no application inventory. */
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <stdio.h>

int main(void)
{
    @autoreleasepool {
        io_service_t service=IOServiceGetMatchingService(0,IOServiceMatching("IOMobileFramebuffer"));
        if(service) {
            CFTypeRef timings=IORegistryEntryCreateCFProperty(service,CFSTR("TimingElements"),NULL,0);
            if(timings && CFGetTypeID(timings)==CFArrayGetTypeID()) {
                NSArray *list=(__bridge NSArray *)timings;
                NSDictionary *timing=list.firstObject;
                printf("refresh-launch: registry_sync=%s\n",[timing[@"VerticalAttributes"][@"SyncRate"] description].UTF8String ?: "missing");
            }
            if(timings)CFRelease(timings);IOObjectRelease(service);
        }
        Class displayClass=NSClassFromString(@"CADisplay");
        SEL mainDisplay=NSSelectorFromString(@"mainDisplay");
        id display=[displayClass respondsToSelector:mainDisplay] ? ((id(*)(id,SEL))objc_msgSend)(displayClass,mainDisplay) : nil;
        for(NSString *name in @[@"refreshRate",@"hardwareRefreshRate",@"maximumRefreshRate",@"minimumRefreshRate",@"minimumFrameDuration",@"maximumFramesPerSecond"]) {
            SEL sel=NSSelectorFromString(name);
            Method method=class_getInstanceMethod(object_getClass(display),sel);char type[16]={0};
            if(method)method_getReturnType(method,type,sizeof(type));
            if(type[0]=='d')printf("refresh-launch: CADisplay %s=%.9f\n",name.UTF8String,((double(*)(id,SEL))objc_msgSend)(display,sel));
            if(type[0]=='f')printf("refresh-launch: CADisplay %s=%.9f\n",name.UTF8String,(double)((float(*)(id,SEL))objc_msgSend)(display,sel));
            if(type[0]=='q')printf("refresh-launch: CADisplay %s=%lld\n",name.UTF8String,((long long(*)(id,SEL))objc_msgSend)(display,sel));
        }
        for(NSString *name in @[@"currentMode",@"preferredMode"]) {
            SEL sel=NSSelectorFromString(name);
            id mode=[display respondsToSelector:sel] ? ((id(*)(id,SEL))objc_msgSend)(display,sel) : nil;
            SEL rate=NSSelectorFromString(@"refreshRate");
            Method method=class_getInstanceMethod(object_getClass(mode),rate);char type[16]={0};
            if(method)method_getReturnType(method,type,sizeof(type));
            if(type[0]=='d')printf("refresh-launch: CADisplay %s rate=%.9f\n",name.UTF8String,((double(*)(id,SEL))objc_msgSend)(mode,rate));
        }
        NSString *path=@"/Applications/LemonadeRefresh.app";
        NSBundle *bundle=[NSBundle bundleWithPath:path];
        printf("refresh-launch: bundle=%s executable=%s executable_file=%d\n",
            bundle.bundleIdentifier.UTF8String ?: "missing", bundle.executablePath.UTF8String ?: "missing",
            [[NSFileManager defaultManager] isExecutableFileAtPath:bundle.executablePath ?: @""]);
        dlopen("/System/Library/Frameworks/MobileCoreServices.framework/MobileCoreServices",RTLD_NOW);
        Class cls=NSClassFromString(@"LSApplicationWorkspace");
        SEL get=NSSelectorFromString(@"defaultWorkspace");
        id workspace=[cls respondsToSelector:get] ? ((id(*)(id,SEL))objc_msgSend)(cls,get) : nil;
        SEL reg=NSSelectorFromString(@"registerApplication:");
        BOOL registered=NO;
        if ([workspace respondsToSelector:reg])
            registered=((BOOL(*)(id,SEL,id))objc_msgSend)(workspace,reg,[NSURL fileURLWithPath:path]);
        SEL launch=NSSelectorFromString(@"openApplicationWithBundleID:");
        BOOL opened=NO;
        if ([workspace respondsToSelector:launch])
            opened=((BOOL(*)(id,SEL,id))objc_msgSend)(workspace,launch,@"com.redwood.refresh");
        printf("refresh-launch: workspace=%d registered=%d opened=%d\n",workspace!=nil,registered,opened);
        fflush(stdout);
        return opened?0:1;
    }
}
