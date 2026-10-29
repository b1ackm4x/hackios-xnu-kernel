/* Per-app first-run preferences for the local RAM base. No remote requests,
 * foreground transitions, UI probes, or game launches are performed here. */
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <OpenGLES/EAGL.h>
#import <CommonCrypto/CommonDigest.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <unistd.h>

static void configureSources(NSDictionary *config) {
    NSArray *sources=config[@"sources"];NSString *revision=config[@"revision"];
    if(![sources isKindOfClass:NSArray.class] || sources.count>20 || ![revision isKindOfClass:NSString.class])return;
    NSMutableArray *urls=[NSMutableArray array];
    for(NSDictionary *source in sources){
        NSURL *url=[NSURL URLWithString:source[@"url"]];
        if(![url.scheme isEqual:@"https"] || !url.host.length || url.user || url.password)return;
        [urls addObject:url.absoluteString];
    }
    NSUserDefaults *defaults=NSUserDefaults.standardUserDefaults;
    if([[defaults stringForKey:@"lemonade.sources.revision"] isEqual:revision])return;
    NSString *old=[defaults stringForKey:@"repos"];
    NSArray *previous=old?[NSJSONSerialization JSONObjectWithData:[old dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil]:nil;
    NSMutableOrderedSet *combined=[NSMutableOrderedSet orderedSet];
    if([previous isKindOfClass:NSArray.class])for(id value in previous)if([value isKindOfClass:NSString.class])[combined addObject:value];
    [combined addObjectsFromArray:urls];
    NSData *json=[NSJSONSerialization dataWithJSONObject:combined.array options:0 error:nil];
    if(!json)return;
    [defaults setObject:[[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding] forKey:@"repos"];
    [defaults setObject:revision forKey:@"lemonade.sources.revision"];
    [defaults synchronize];
}
static void importGame(NSDictionary *config,unsigned retry) {
    UIApplication *app=UIApplication.sharedApplication;
    if(app.applicationState!=UIApplicationStateActive || !app.delegate){
        if(retry<60)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC),dispatch_get_main_queue(),^{importGame(config,retry+1);});
        return;
    }
    NSString *expected=config[@"game_sha256"];
    NSUserDefaults *defaults=NSUserDefaults.standardUserDefaults;
    if(!expected || [[defaults stringForKey:@"lemonade.game.imported"] isEqual:expected])return;
    NSData *rom=[NSData dataWithContentsOfFile:@"/usr/share/lemonade-apps/Vulkanon.gba"];
    if(!rom || rom.length>8*1024*1024)return;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];CC_SHA256(rom.bytes,(CC_LONG)rom.length,digest);
    NSMutableString *hash=[NSMutableString string];for(unsigned i=0;i<sizeof(digest);i++)[hash appendFormat:@"%02x",digest[i]];
    if(![hash isEqual:expected])return;
    NSString *file=[NSTemporaryDirectory() stringByAppendingPathComponent:@"Vulkanon.gba"];
    if(![rom writeToFile:file atomically:YES])return;
    SEL method=@selector(application:openURL:options:);
    if([app.delegate respondsToSelector:method] && ((BOOL(*)(id,SEL,id,id,id))objc_msgSend)(app.delegate,method,app,[NSURL fileURLWithPath:file],@{})){
        [defaults setObject:expected forKey:@"lemonade.game.imported"];
        [defaults synchronize];
    }
}
__attribute__((constructor)) static void setup(void) {
    @autoreleasepool {
        NSDictionary *config=[NSDictionary dictionaryWithContentsOfFile:@"/usr/share/lemonade-apps/setup.plist"];
        if(![config[@"profile"] isEqual:@"lemonade-app-preferences-v1"])return;
        if(!strcmp(getprogname(),"TrollApps"))configureSources(config);
        else if(!strcmp(getprogname(),"Delta")){
            EAGLContext *native=[[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES2];
            if(!native)dlopen("/redwood/lib/libredwood-gl.dylib",RTLD_NOW|RTLD_LOCAL);
            NSUserDefaults *defaults=NSUserDefaults.standardUserDefaults;
            if(![defaults boolForKey:@"lemonade.graphics.initialized"]){
                [defaults setBool:YES forKey:@"metal"];
                [defaults setBool:YES forKey:@"lemonade.graphics.initialized"];
            }
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,18*NSEC_PER_SEC),dispatch_get_main_queue(),^{importGame(config,0);});
        }
    }
}
