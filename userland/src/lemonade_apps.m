/* Restore the explicitly selected, locally sealed apps onto the fresh RAM data
 * volume. No network access, foreground launches, or diagnostic workloads. */
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
extern int posix_spawnattr_set_persona_np(const posix_spawnattr_t *, uid_t, uint32_t);
extern int posix_spawnattr_set_persona_uid_np(const posix_spawnattr_t *, uid_t);
extern int posix_spawnattr_set_persona_gid_np(const posix_spawnattr_t *, uid_t);

static NSString *const assets=@"/usr/share/lemonade-apps";
static NSString *const stage=@"/private/var/tmp/lemonade-apps-stage";
static NSString *const ready=@"/private/var/tmp/lemonade-apps-bootstrap.json";
static NSString *const completed=@"/private/var/tmp/lemonade-apps-complete.json";

static NSURL *bundle(NSString *identifier) {
    Class cls=NSClassFromString(@"LSApplicationProxy");
    id app=cls?((id(*)(id,SEL,id))objc_msgSend)(cls,NSSelectorFromString(@"applicationProxyForIdentifier:"),identifier):nil;
    return app?((id(*)(id,SEL))objc_msgSend)(app,NSSelectorFromString(@"bundleURL")):nil;
}
static NSString *version(NSString *identifier) {
    NSURL *url=bundle(identifier);
    NSDictionary *info=url?[NSDictionary dictionaryWithContentsOfURL:[url URLByAppendingPathComponent:@"Info.plist"]]:nil;
    return info[@"CFBundleShortVersionString"]?:info[@"CFBundleVersion"];
}
static NSString *sha256(NSString *path) {
    FILE *f=fopen(path.fileSystemRepresentation,"rb");if(!f)return nil;
    CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);unsigned char buffer[65536],hash[CC_SHA256_DIGEST_LENGTH];size_t size;
    while((size=fread(buffer,1,sizeof(buffer),f)))CC_SHA256_Update(&ctx,buffer,(CC_LONG)size);
    int failed=ferror(f);fclose(f);if(failed)return nil;CC_SHA256_Final(hash,&ctx);
    NSMutableString *out=[NSMutableString string];
    for(unsigned i=0;i<sizeof(hash);i++)[out appendFormat:@"%02x",hash[i]];
    return out;
}
static NSString *checkedFile(NSDictionary *config,NSString *name) {
    if(![name isKindOfClass:NSString.class] || ![name.lastPathComponent isEqual:name])return nil;
    NSDictionary *spec=config[@"files"][name];
    NSString *path=[assets stringByAppendingPathComponent:name];
    struct stat st;
    if(!spec || lstat(path.fileSystemRepresentation,&st) || !S_ISREG(st.st_mode) || st.st_uid!=0 ||
       st.st_size!=[spec[@"bytes"] longLongValue] || ![sha256(path) isEqual:spec[@"sha256"]])return nil;
    return path;
}
static int run(NSArray<NSString *> *arguments,BOOL persona) {
    if(!arguments.count || arguments.count>=16)return 125;
    char *args[16]={0};for(NSUInteger i=0;i<arguments.count;i++)args[i]=(char *)arguments[i].UTF8String;
    posix_spawnattr_t attr;int error=posix_spawnattr_init(&attr);if(error)return error;
    if(persona){
        error=posix_spawnattr_set_persona_np(&attr,99,1);
        if(!error)error=posix_spawnattr_set_persona_uid_np(&attr,0);
        if(!error)error=posix_spawnattr_set_persona_gid_np(&attr,0);
    }
    pid_t pid=0;if(!error)error=posix_spawn(&pid,args[0],NULL,&attr,args,environ);
    posix_spawnattr_destroy(&attr);if(error)return error;
    int status=0;
    for(unsigned i=0;i<480;i++){
        pid_t result=waitpid(pid,&status,WNOHANG);
        if(result==pid)return WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);
        if(result<0 && errno!=EINTR)return 125;
        usleep(500000);
    }
    kill(pid,SIGTERM);usleep(500000);
    if(waitpid(pid,&status,WNOHANG)==0){kill(pid,SIGKILL);waitpid(pid,&status,0);}
    return 124;
}
static BOOL receipt(NSString *path,NSDictionary *value) {
    NSData *data=[NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    BOOL ok=[data writeToFile:path atomically:YES];
    if(ok)chmod(path.fileSystemRepresentation,0644);
    return ok;
}
static int bootstrap(NSDictionary *config) {
    if(getuid()!=0)return 90;
    NSString *tar=checkedFile(config,@"TrollStore.tar"),*ldid=checkedFile(config,@"ldid");
    NSString *store=checkedFile(config,@"TrollApps.ipa"),*helper=checkedFile(config,@"trollstorehelper");
    if(!tar || !ldid || !store || !helper)return 91;
    if(mkdir(stage.fileSystemRepresentation,0700) && errno!=EEXIST)return 92;
    struct stat st;
    if(lstat(stage.fileSystemRepresentation,&st) || !S_ISDIR(st.st_mode) || st.st_uid!=0)return 92;
    struct {uint32_t magic,version;uint64_t max_bytes;} options={0x52414d46,1,3ULL*1024*1024*1024};
    if(mount("ramfs",stage.fileSystemRepresentation,0,&options))return 93;
    if(chown(stage.fileSystemRepresentation,501,501) || chmod(stage.fileSystemRepresentation,0700))return 94;
    setenv("TMPDIR",[[stage stringByAppendingString:@"/"] fileSystemRepresentation],1);
    sleep(35);
    int rc=run(@[helper,@"install-trollstore",tar],NO);
    if(rc || ![version(@"com.opa334.TrollStore") isEqual:config[@"versions"][@"TrollStore"]])return 95;
    rc=run(@[helper,@"install-ldid",ldid,@"v2.1.5-procursus7-new"],NO);if(rc)return 96;
    NSString *installedLdid=[[bundle(@"com.opa334.TrollStore") URLByAppendingPathComponent:@"ldid"] path];
    if(![sha256(installedLdid) isEqual:config[@"files"][@"ldid"][@"sha256"]])return 96;
    rc=run(@[helper,@"url-scheme",@"enable"],NO);if(rc)return 97;
    rc=run(@[helper,@"install",store],NO);
    if(rc || ![version(@"org.resonance.store") isEqual:config[@"versions"][@"TrollApps"]])return 98;
    if(!receipt(ready,@{@"TrollStore":version(@"com.opa334.TrollStore"),@"TrollApps":version(@"org.resonance.store")}))return 99;
    puts("apps-restore: stores_registered=1 offline=1");
    for(unsigned i=0;i<720 && access(completed.fileSystemRepresentation,F_OK);i++)sleep(1);
    if(!access(completed.fileSystemRepresentation,F_OK)){
        int rc=unmount(stage.fileSystemRepresentation,0);
        printf("apps-restore: scratch_unmounted=%d\n",rc==0);
    }
    return 0;
}
static int applications(NSDictionary *config) {
    if(getuid()!=501)return 90;
    for(unsigned i=0;i<360 && access(ready.fileSystemRepresentation,F_OK);i++)sleep(1);
    if(access(ready.fileSystemRepresentation,F_OK))return 91;
    setenv("TMPDIR",[[stage stringByAppendingString:@"/"] fileSystemRepresentation],1);
    NSString *helper=[[bundle(@"com.opa334.TrollStore") URLByAppendingPathComponent:@"trollstorehelper"] path];
    if(!helper || access(helper.fileSystemRepresentation,X_OK))return 92;
    NSArray *apps=config[@"applications"];
    if(![apps isKindOfClass:NSArray.class] || apps.count>8)return 93;
    NSMutableDictionary *installed=[NSMutableDictionary dictionary];
    for(NSDictionary *app in apps){
        NSString *identifier=app[@"identifier"],*expected=app[@"version"];
        NSString *ipa=checkedFile(config,app[@"file"]);
        if(!ipa || !identifier.length || !expected.length)return 94;
        NSString *present=version(identifier);
        if(present && ![present isEqual:expected])return 95;
        int rc=present?0:run(@[helper,@"install",ipa],YES);
        BOOL good=[version(identifier) isEqual:expected];
        printf("apps-restore: app=%s helper=%d registered=%d\n",identifier.UTF8String,rc,good);
        if(rc || !good)return 96;
        installed[identifier]=expected;
    }
    if(!receipt(completed,@{@"installed":installed,@"offline":@YES}))return 97;
    puts("apps-restore: complete=1 foreground_unchanged=1");
    return 0;
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOLBF,0);
    setenv("PATH","/var/jb/usr/bin:/var/jb/bin:/usr/bin:/bin:/usr/sbin:/sbin",1);
    @autoreleasepool {
        if(argc!=2)return 80;
        NSDictionary *config=[NSDictionary dictionaryWithContentsOfFile:[assets stringByAppendingPathComponent:@"manifest.plist"]];
        if(![config[@"profile"] isEqual:@"lemonade-private-apps-v1"])return 81;
        dlopen("/System/Library/Frameworks/MobileCoreServices.framework/MobileCoreServices",RTLD_NOW);
        if(!strcmp(argv[1],"--bootstrap"))return bootstrap(config);
        if(!strcmp(argv[1],"--install"))return applications(config);
        return 82;
    }
}
