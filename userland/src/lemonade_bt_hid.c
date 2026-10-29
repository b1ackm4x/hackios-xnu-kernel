/* Native bluetoothd HID reports -> the existing user-space HID event server.
 * The custom kernel has no IOHIDResource. Keep pairing, encryption, SDP and
 * HIDP in the native daemon; accept a device only after a real service opens.
 * No report bytes, key usages, peripheral names or addresses are logged.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dispatch/dispatch.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>
#include "lemonade_bt_hid_report.h"
#include "lemonade_bt_hid_ipc.h"
extern int proc_pidpath(int,void *,uint32_t);

/* ABI from apple-oss-distributions/CF/CFRuntime.h, arm64. */
struct cf_base { uintptr_t isa; uint8_t info[4]; uint32_t rc; };
struct cf_class {
    CFIndex version; const char *name;
    void (*init)(CFTypeRef); CFTypeRef (*copy)(CFAllocatorRef,CFTypeRef);
    void (*finalize)(CFTypeRef); Boolean (*equal)(CFTypeRef,CFTypeRef);
    CFHashCode (*hash)(CFTypeRef); CFStringRef (*format)(CFTypeRef,CFDictionaryRef);
    CFStringRef (*debug)(CFTypeRef); void (*reclaim)(CFTypeRef);
    uint32_t (*refcount)(intptr_t,CFTypeRef); uintptr_t alignment;
};
extern CFTypeID _CFRuntimeRegisterClass(const struct cf_class *);
extern CFTypeRef _CFRuntimeCreateInstance(CFAllocatorRef,CFTypeID,CFIndex,unsigned char *);
typedef CFTypeRef user_device;
typedef struct __IOHIDEvent *event_ref;
typedef struct __IOHIDEventSystemClient *client_ref;
typedef struct __IOHIDServiceClient *service_ref;
typedef IOReturn (*report_fn)(void *,int,uint32_t,uint8_t *,CFIndex);
typedef IOReturn (*get_report_fn)(void *,int,uint32_t,uint8_t *,CFIndex *);
extern user_device IOHIDUserDeviceCreateWithOptions(CFAllocatorRef,CFDictionaryRef,uint32_t);
extern IOReturn IOHIDUserDeviceHandleReportWithTimeStamp(user_device,uint64_t,const uint8_t *,CFIndex);
extern void IOHIDUserDeviceRegisterGetReportWithReturnLengthCallback(user_device,get_report_fn,void *);
extern void IOHIDUserDeviceRegisterSetReportCallback(user_device,report_fn,void *);
extern void IOHIDUserDeviceScheduleWithDispatchQueue(user_device,dispatch_queue_t);
extern void IOHIDUserDeviceUnscheduleFromDispatchQueue(user_device,dispatch_queue_t);
struct service_callbacks {
    uint64_t version;
    void (*notification)(void *,void *,service_ref,uint32_t,CFDictionaryRef);
    Boolean (*set_property)(void *,void *,service_ref,CFStringRef,CFTypeRef);
    CFTypeRef (*copy_property)(void *,void *,service_ref,CFStringRef);
    event_ref (*copy_event)(void *,void *,service_ref,uint32_t,event_ref,uint32_t);
    int (*set_output)(void *,void *,service_ref,event_ref);
    CFTypeRef (*copy_matching)(void *,void *,service_ref,CFDictionaryRef);
};
extern client_ref IOHIDEventSystemClientCreateWithType(CFAllocatorRef,int,CFDictionaryRef);
extern void IOHIDEventSystemClientScheduleWithDispatchQueue(client_ref,dispatch_queue_t);
extern void IOHIDEventSystemClientUnscheduleFromDispatchQueue(client_ref,dispatch_queue_t);
extern service_ref IOHIDVirtualServiceClientCreateWithCallbacks(client_ref,CFDictionaryRef,const struct service_callbacks *,void *,void *);
extern Boolean IOHIDVirtualServiceClientRemove(service_ref);
extern Boolean IOHIDVirtualServiceClientDispatchEvent(service_ref,event_ref);
extern event_ref IOHIDEventCreateKeyboardEvent(CFAllocatorRef,uint64_t,uint32_t,uint32_t,Boolean,uint32_t);

struct hid_state {
    atomic_uint refs;
    pthread_mutex_t lock;
    CFMutableDictionaryRef properties;
    dispatch_queue_t queue,native_queue;
    client_ref client; service_ref service;
    bool opened,scheduled,closing;
    bool remote;
    int remote_fd;
    get_report_fn get_report; void *get_context;
    report_fn set_report; void *set_context;
    uint64_t timestamp;
    unsigned reports,events,failures,input_calls;
    struct lbh_device decoder;
};
struct hid_object { struct cf_base base; struct hid_state *state; };
static CFTypeID hid_type;
static bool enabled;
#ifdef LEMONADE_BT_HID_SERVER
static bool server_mode=true;
#else
static bool server_mode;
#endif



static bool peer_is(int fd,uid_t expected_uid,const char *expected_path) {
    uid_t uid;gid_t gid;pid_t pid=0;socklen_t size=sizeof(pid);char path[1024]={0};
    return !getpeereid(fd,&uid,&gid) && uid==expected_uid &&
        !getsockopt(fd,SOL_LOCAL,LOCAL_PEERPID,&pid,&size) && size==sizeof(pid) && pid>0 &&
        proc_pidpath(pid,path,sizeof(path))>0 && !strcmp(path,expected_path);
}
static void socket_options(int fd,int receive_seconds) {
    int one=1;struct timeval send_timeout={2,0},receive_timeout={receive_seconds,0};
    setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one));
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&send_timeout,sizeof(send_timeout));
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&receive_timeout,sizeof(receive_timeout));
}
static int connect_helper(CFDictionaryRef properties) {
    CFMutableDictionaryRef allowed=CFDictionaryCreateMutable(NULL,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    const CFStringRef keys[]={CFSTR("ReportDescriptor"),CFSTR("Transport"),CFSTR("VendorID"),CFSTR("ProductID"),
        CFSTR("Product"),CFSTR("Manufacturer"),CFSTR("VersionNumber"),CFSTR("CountryCode"),CFSTR("KeyboardType")};
    for (unsigned i=0;i<sizeof(keys)/sizeof(keys[0]);i++) {
        CFTypeRef value=CFDictionaryGetValue(properties,keys[i]);
        if (value) CFDictionarySetValue(allowed,keys[i],value);
    }
    CFDataRef data=CFPropertyListCreateData(NULL,allowed,kCFPropertyListBinaryFormat_v1_0,0,NULL);
    CFRelease(allowed);
    if (!data) return -1;
    CFIndex length=CFDataGetLength(data);int fd=-1;
    if (length>0 && length<=LBH_IPC_PROPERTIES) {
        fd=socket(AF_UNIX,SOCK_STREAM,0);
        if (fd>=0) {
            socket_options(fd,8);
            struct sockaddr_un address={.sun_len=sizeof(address),.sun_family=AF_UNIX};
            strlcpy(address.sun_path,LBH_SOCKET,sizeof(address.sun_path));
            struct lbh_reply reply;
            if (connect(fd,(struct sockaddr *)&address,sizeof(address)) ||
                !peer_is(fd,0,"/redwood/bin/lemonade-bt-hid") ||
                !lbh_send(fd,LBH_IPC_CREATE,0,CFDataGetBytePtr(data),(uint32_t)length) ||
                !lbh_io(fd,&reply,sizeof(reply),false) || reply.status) {
                close(fd);fd=-1;
            } else socket_options(fd,2);
        }
    }
    CFRelease(data);
    fprintf(stderr,"bt-hid: privileged input service accepted=%d\n",fd>=0);
    return fd;
}
static struct hid_state *state_of(user_device device) {
    return device && hid_type && CFGetTypeID(device)==hid_type ? ((const struct hid_object *)device)->state : NULL;
}
static void state_release(struct hid_state *s) {
    if (atomic_fetch_sub(&s->refs,1)!=1) return;
    if (s->properties) CFRelease(s->properties);
    if (s->native_queue) dispatch_release(s->native_queue);
    if (s->queue) dispatch_release(s->queue);
    pthread_mutex_destroy(&s->lock); free(s);
}
static bool emit(void *context,uint32_t usage,bool down) {
    struct hid_state *s=context;
    if (!s->service || !s->opened) return false;
    event_ref event=IOHIDEventCreateKeyboardEvent(NULL,s->timestamp,usage>>16,usage&0xffff,down,0);
    if (!event) return false;
    bool ok=IOHIDVirtualServiceClientDispatchEvent(s->service,event);
    CFRelease(event);
    if (ok) s->events++;
    return ok;
}
static void stop(struct hid_state *s) {
    pthread_mutex_lock(&s->lock);
    if (s->closing) { pthread_mutex_unlock(&s->lock);return; }
    s->scheduled=false; s->closing=true; s->timestamp=mach_absolute_time();
    if (s->remote) {
        if (s->remote_fd>=0) {
            struct lbh_reply reply;
            if (lbh_send(s->remote_fd,LBH_IPC_CLOSE,0,NULL,0) && lbh_io(s->remote_fd,&reply,sizeof(reply),false)) {
                s->reports=reply.reports;s->events=reply.events;s->failures=reply.failures;
            }
            close(s->remote_fd);s->remote_fd=-1;
        }
        pthread_mutex_unlock(&s->lock);
        fprintf(stderr,"bt-hid: remote service closed; reports=%u events=%u failures=%u\n",s->reports,s->events,s->failures);
        return;
    }
    bool released=lbh_release(&s->decoder,emit,s);
    pthread_mutex_unlock(&s->lock);
    if (s->service) { IOHIDVirtualServiceClientRemove(s->service); CFRelease(s->service); s->service=NULL; }
    if (s->client) {
        IOHIDEventSystemClientUnscheduleFromDispatchQueue(s->client,s->queue);
        dispatch_sync(s->queue,^{});
        CFRelease(s->client); s->client=NULL;
    }
    fprintf(stderr,"bt-hid: removed; reports=%u events=%u failures=%u released=%d\n",s->reports,s->events,s->failures,released);
}
static void finalize(CFTypeRef object) {
    struct hid_state *s=((const struct hid_object *)object)->state;
    if (s) { stop(s); state_release(s); }
}
static const struct cf_class hid_class={.name="LemonadeBluetoothKeyboard",.finalize=finalize};
static CFTypeRef copy_property(void *target,void *context,service_ref service,CFStringRef key) {
    (void)target;(void)service;struct hid_state *s=context;
    pthread_mutex_lock(&s->lock);
    CFTypeRef value=key?CFDictionaryGetValue(s->properties,key):NULL;
    if (value) CFRetain(value);
    pthread_mutex_unlock(&s->lock);return value;
}
static Boolean set_property(void *target,void *context,service_ref service,CFStringRef key,CFTypeRef value) {
    (void)target;(void)service;struct hid_state *s=context;
    if (!key || !value) return false;
    pthread_mutex_lock(&s->lock);
    if (CFEqual(key,CFSTR("DeviceOpenedByEventSystem")) && CFGetTypeID(value)==CFBooleanGetTypeID())
        s->opened=CFBooleanGetValue(value);
    if (!s->closing) CFDictionarySetValue(s->properties,key,value);
    pthread_mutex_unlock(&s->lock);return true;
}
static void notification(void *target,void *context,service_ref service,uint32_t type,CFDictionaryRef properties) {
    (void)target;(void)context;(void)service;(void)properties;
    fprintf(stderr,"bt-hid: event-service notification type=%u\n",type);
}
static int set_output(void *target,void *context,service_ref service,event_ref event) {
    (void)target;(void)context;(void)service;(void)event;
    /* Do not report output success until its actual native callback is wired. */
    return kIOReturnUnsupported;
}
static const struct service_callbacks callbacks={.version=2,.notification=notification,
    .set_property=set_property,.copy_property=copy_property,.set_output=set_output};
static void set_number(CFMutableDictionaryRef dictionary,CFStringRef key,int value) {
    CFNumberRef n=CFNumberCreate(NULL,kCFNumberIntType,&value);
    CFDictionarySetValue(dictionary,key,n);CFRelease(n);
}
static void add_usages(struct hid_state *s) {
    CFMutableArrayRef pairs=CFArrayCreateMutable(NULL,0,&kCFTypeArrayCallBacks);
    const int pages[]={1,12},usages[]={6,1};
    for (unsigned i=0;i<2;i++) {
        CFMutableDictionaryRef pair=CFDictionaryCreateMutable(NULL,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
        set_number(pair,CFSTR("DeviceUsagePage"),pages[i]);set_number(pair,CFSTR("DeviceUsage"),usages[i]);
        CFArrayAppendValue(pairs,pair);CFRelease(pair);
    }
    set_number(s->properties,CFSTR("PrimaryUsagePage"),1);set_number(s->properties,CFSTR("PrimaryUsage"),6);
    set_number(s->properties,CFSTR("DeviceUsagePage"),1);set_number(s->properties,CFSTR("DeviceUsage"),6);
    CFDictionarySetValue(s->properties,CFSTR("DeviceUsagePairs"),pairs);CFRelease(pairs);
    CFDictionarySetValue(s->properties,CFSTR("Built-In"),kCFBooleanFalse);
}
static user_device create(CFAllocatorRef allocator,CFDictionaryRef properties,uint32_t options) {
    user_device original=IOHIDUserDeviceCreateWithOptions(allocator,properties,options);
    if (original || !enabled || !hid_type || !properties || CFGetTypeID(properties)!=CFDictionaryGetTypeID()) return original;
    CFTypeRef descriptor=CFDictionaryGetValue(properties,CFSTR("ReportDescriptor"));
    CFTypeRef transport=CFDictionaryGetValue(properties,CFSTR("Transport"));
    if (!descriptor || CFGetTypeID(descriptor)!=CFDataGetTypeID() || !transport ||
        CFGetTypeID(transport)!=CFStringGetTypeID() || !CFEqual(transport,CFSTR("Bluetooth"))) return NULL;
    struct hid_state *s=calloc(1,sizeof(*s));if (!s) return NULL;
    if (!lbh_descriptor(&s->decoder,CFDataGetBytePtr(descriptor),(size_t)CFDataGetLength(descriptor))) {
        fprintf(stderr,"bt-hid: unsupported descriptor; no service created\n");free(s);return NULL;
    }
    atomic_init(&s->refs,1);pthread_mutex_init(&s->lock,NULL);s->remote_fd=-1;
    s->properties=CFDictionaryCreateMutableCopy(NULL,0,properties);
    s->queue=dispatch_queue_create("lemonade.bluetooth.hid",DISPATCH_QUEUE_SERIAL);
    if (!s->properties || !s->queue) { state_release(s);return NULL; }
    add_usages(s);
    if (!server_mode) {
        s->remote=true;s->remote_fd=connect_helper(s->properties);
        s->opened=s->remote_fd>=0;
    }
    for (int type=2;server_mode && type<=3;type++) {
        s->client=IOHIDEventSystemClientCreateWithType(NULL,type,NULL);
        if (!s->client) { fprintf(stderr,"bt-hid: no event client type=%d\n",type);continue; }
        IOHIDEventSystemClientScheduleWithDispatchQueue(s->client,s->queue);
        s->service=IOHIDVirtualServiceClientCreateWithCallbacks(s->client,s->properties,&callbacks,NULL,s);
        bool opened=false;
        for (unsigned wait=0;s->service && wait<40;wait++) {
            pthread_mutex_lock(&s->lock);opened=s->opened;pthread_mutex_unlock(&s->lock);
            if (opened) break;usleep(50000);
        }
        fprintf(stderr,"bt-hid: event client type=%d service=%d opened=%d\n",type,s->service!=NULL,opened);
        if (opened) break;
        stop(s);s->closing=false;s->opened=false;
    }
    if ((!s->service && !s->remote) || !s->opened) { stop(s);state_release(s);return NULL; }
    struct hid_object *object=(struct hid_object *)_CFRuntimeCreateInstance(allocator,hid_type,
        sizeof(*object)-sizeof(object->base),NULL);
    if (!object) { stop(s);state_release(s);return NULL; }
    object->state=s;
    fprintf(stderr,"bt-hid: keyboard registered; reports=%u fields=%u; payload logging disabled\n",s->decoder.report_count,s->decoder.field_count);
    return object;
}
static void register_get(user_device device,get_report_fn callback,void *context) {
    struct hid_state *s=state_of(device);
    if (!s) { IOHIDUserDeviceRegisterGetReportWithReturnLengthCallback(device,callback,context);return; }
    pthread_mutex_lock(&s->lock);s->get_report=callback;s->get_context=context;pthread_mutex_unlock(&s->lock);
}
static void register_set(user_device device,report_fn callback,void *context) {
    struct hid_state *s=state_of(device);
    if (!s) { IOHIDUserDeviceRegisterSetReportCallback(device,callback,context);return; }
    pthread_mutex_lock(&s->lock);s->set_report=callback;s->set_context=context;pthread_mutex_unlock(&s->lock);
}
static void schedule(user_device device,dispatch_queue_t queue) {
    struct hid_state *s=state_of(device);
    if (!s) { IOHIDUserDeviceScheduleWithDispatchQueue(device,queue);return; }
    pthread_mutex_lock(&s->lock);
    if (!s->native_queue && queue) { s->native_queue=queue;dispatch_retain(queue);s->scheduled=true; }
    pthread_mutex_unlock(&s->lock);
    fprintf(stderr,"bt-hid: native output callbacks scheduled=%d\n",queue!=NULL);
}
static void unschedule(user_device device,dispatch_queue_t queue) {
    struct hid_state *s=state_of(device);
    if (!s) { IOHIDUserDeviceUnscheduleFromDispatchQueue(device,queue);return; }
    stop(s);
}
static IOReturn report(user_device device,uint64_t timestamp,const uint8_t *bytes,CFIndex length) {
    struct hid_state *s=state_of(device);
    if (!s) return IOHIDUserDeviceHandleReportWithTimeStamp(device,timestamp,bytes,length);
    pthread_mutex_lock(&s->lock);
    s->timestamp=timestamp;
    /* Scheduling is required only for get/set-report callbacks. The native HID
     * API accepts input reports before scheduling, including cached reports. */
    bool ready=!s->closing && s->opened && bytes && length>0 && length<=LBH_BYTES+1;
    bool ok=false;
    s->input_calls++;
    if (!ready) {
        s->failures++;
        if (s->input_calls<=3) fprintf(stderr,"bt-hid: input rejected; opened=%d closing=%d length=%ld\n",
            s->opened,s->closing,(long)length);
    } else if (s->remote) {
        struct lbh_reply reply;
        if (s->remote_fd>=0 && lbh_send(s->remote_fd,LBH_IPC_INPUT,timestamp,bytes,(uint32_t)length) &&
            lbh_io(s->remote_fd,&reply,sizeof(reply),false)) {
            s->reports=reply.reports;s->events=reply.events;s->failures=reply.failures;ok=reply.status==0;
        } else {
            if (s->input_calls<=3) fprintf(stderr,"bt-hid: input IPC failed; errno=%d\n",errno);
            if (s->remote_fd>=0) { close(s->remote_fd);s->remote_fd=-1; }
            s->failures++;
        }
    } else {
        ok=lbh_input(&s->decoder,bytes,(size_t)length,emit,s);
        s->reports++;if (!ok) s->failures++;
    }
    if (s->input_calls<=3 || (s->input_calls%64)==0)
        fprintf(stderr,"bt-hid: input reports=%u events=%u failures=%u\n",s->reports,s->events,s->failures);
    pthread_mutex_unlock(&s->lock);
    return ok?kIOReturnSuccess:kIOReturnError;
}
__attribute__((constructor)) static void start(void) {
    const char *name=getprogname();char arguments[1024]={0};size_t size=sizeof(arguments)-1;
    if (!name || strcmp(name,server_mode?"lemonade-bt-hid":"bluetoothd") || sysctlbyname("kern.bootargs",arguments,&size,NULL,0) ||
        !strstr(arguments,"lemonade_bt_hid=1")) return;
    io_service_t service=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("LemonadeBluetooth"));
    if (!service) return;
    CFTypeRef value=IORegistryEntryCreateCFProperty(service,CFSTR("compatible"),NULL,0);
    const char expected[]="qcom,wcn6851-bt";
    enabled=value && CFGetTypeID(value)==CFDataGetTypeID() && CFDataGetLength(value)==sizeof(expected) &&
        !memcmp(CFDataGetBytePtr(value),expected,sizeof(expected));
    if (value) CFRelease(value);IOObjectRelease(service);
    if (enabled) hid_type=_CFRuntimeRegisterClass(&hid_class);
    fprintf(stderr,"bt-hid: keyboard adapter enabled=%d class=%d server=%d\n",enabled,hid_type!=0,server_mode);
}
#ifndef LEMONADE_BT_HID_SERVER
#define INTERPOSE(a,b) {(const void *)(a),(const void *)(b)}
__attribute__((used,section("__DATA,__interpose"))) static const struct { const void *replacement,*original; } interposed[]={
    INTERPOSE(create,IOHIDUserDeviceCreateWithOptions),
    INTERPOSE(register_get,IOHIDUserDeviceRegisterGetReportWithReturnLengthCallback),
    INTERPOSE(register_set,IOHIDUserDeviceRegisterSetReportCallback),
    INTERPOSE(schedule,IOHIDUserDeviceScheduleWithDispatchQueue),
    INTERPOSE(unschedule,IOHIDUserDeviceUnscheduleFromDispatchQueue),
    INTERPOSE(report,IOHIDUserDeviceHandleReportWithTimeStamp),
};
#endif

#if defined(LEMONADE_BT_HID_SERVER) || defined(LEMONADE_BT_HID_TEST)
static atomic_uint active_connections;
static void *serve(void *argument) {
    int fd=(int)(intptr_t)argument;user_device device=NULL;
    struct lbh_message header;
    socket_options(fd,10);
#ifndef LEMONADE_BT_HID_TEST
    if (!peer_is(fd,501,"/usr/sbin/bluetoothd")) {
        fprintf(stderr,"bt-hid: local peer rejected\n");goto done;
    }
#endif
    if (!lbh_io(fd,&header,sizeof(header),false) || !lbh_header_valid(&header) || header.operation!=LBH_IPC_CREATE) goto done;
    uint8_t *bytes=malloc(header.length);
    if (!bytes) goto done;
    bool received=lbh_io(fd,bytes,header.length,false);
    CFDataRef data=received?CFDataCreate(NULL,bytes,header.length):NULL;free(bytes);
    if (!data) goto done;
    CFPropertyListRef properties=CFPropertyListCreateWithData(NULL,data,kCFPropertyListImmutable,NULL,NULL);CFRelease(data);
    if (properties && CFGetTypeID(properties)==CFDictionaryGetTypeID()) device=create(NULL,properties,0);
    if (properties) CFRelease(properties);
    struct lbh_reply reply={.status=device?kIOReturnSuccess:kIOReturnNotOpen};
    if (!lbh_io(fd,&reply,sizeof(reply),true) || !device) goto done;
    struct hid_state *s=state_of(device);s->scheduled=true;
    socket_options(fd,0); /* A connected keyboard may remain idle indefinitely. */
    while (lbh_io(fd,&header,sizeof(header),false) && lbh_header_valid(&header)) {
        if (header.operation==LBH_IPC_CLOSE) {
            stop(s);
            reply=(struct lbh_reply){0,s->reports,s->events,s->failures};
            lbh_io(fd,&reply,sizeof(reply),true);break;
        }
        if (header.operation!=LBH_IPC_INPUT) break;
        uint8_t input[LBH_BYTES+1];
        if (!lbh_io(fd,input,header.length,false)) break;
        IOReturn status=report(device,header.timestamp,input,header.length);
        reply=(struct lbh_reply){status,s->reports,s->events,s->failures};
        if (!lbh_io(fd,&reply,sizeof(reply),true)) break;
    }
done:
    if (device) CFRelease(device);
    close(fd);atomic_fetch_sub(&active_connections,1);return NULL;
}
#ifndef LEMONADE_BT_HID_TEST
int main(void) {
    if (!enabled || !hid_type || geteuid()!=0) return 1;
    int listener=socket(AF_UNIX,SOCK_STREAM,0);
    if (listener<0) return 2;
    struct sockaddr_un address={.sun_len=sizeof(address),.sun_family=AF_UNIX};
    strlcpy(address.sun_path,LBH_SOCKET,sizeof(address.sun_path));
    struct stat previous;
    if (!lstat(LBH_SOCKET,&previous)) {
        if (!S_ISSOCK(previous.st_mode) || previous.st_uid!=501) return 3;
        int probe=socket(AF_UNIX,SOCK_STREAM,0);
        if (probe<0) return 3;
        int result=connect(probe,(struct sockaddr *)&address,sizeof(address));int error=errno;close(probe);
        if (!result || error!=ECONNREFUSED || unlink(LBH_SOCKET)) return 3;
    } else if (errno!=ENOENT) return 3;
    umask(0077);
    if (bind(listener,(struct sockaddr *)&address,sizeof(address)) || chown(LBH_SOCKET,501,501) ||
        chmod(LBH_SOCKET,0600) || listen(listener,4)) return 4;
    fprintf(stderr,"bt-hid: privileged input helper ready; authenticated local transport\n");
    for (;;) {
        int fd=accept(listener,NULL,NULL);
        if (fd<0) { if (errno==EINTR) continue;return 5; }
        if (atomic_fetch_add(&active_connections,1)>=4) { atomic_fetch_sub(&active_connections,1);close(fd);continue; }
        pthread_t worker;
        if (pthread_create(&worker,NULL,serve,(void *)(intptr_t)fd)) { atomic_fetch_sub(&active_connections,1);close(fd); }
        else pthread_detach(worker);
    }
}
#endif
#endif
