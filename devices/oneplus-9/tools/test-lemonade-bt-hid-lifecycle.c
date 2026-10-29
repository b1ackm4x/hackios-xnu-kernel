/* Use the real host CF runtime and dispatch queues, with a deterministic event
 * server, to exercise object ownership and pending callbacks without hardware. */
#define LEMONADE_BT_HID_TEST
#include "../../../userland/src/lemonade_bt_hid.c"
#include <assert.h>
static bool allow_service=true;
static unsigned dispatched,removed,native_calls;
static dispatch_queue_t event_queue;
user_device IOHIDUserDeviceCreateWithOptions(CFAllocatorRef a,CFDictionaryRef p,uint32_t o) {
    (void)a;(void)p;(void)o;native_calls++;return NULL;
}
IOReturn IOHIDUserDeviceHandleReportWithTimeStamp(user_device d,uint64_t t,const uint8_t *b,CFIndex n) {
    (void)d;(void)t;(void)b;(void)n;assert(false);return -1;
}
void IOHIDUserDeviceRegisterGetReportWithReturnLengthCallback(user_device d,get_report_fn f,void *c) { (void)d;(void)f;(void)c;assert(false); }
void IOHIDUserDeviceRegisterSetReportCallback(user_device d,report_fn f,void *c) { (void)d;(void)f;(void)c;assert(false); }
void IOHIDUserDeviceScheduleWithDispatchQueue(user_device d,dispatch_queue_t q) { (void)d;(void)q;assert(false); }
void IOHIDUserDeviceUnscheduleFromDispatchQueue(user_device d,dispatch_queue_t q) { (void)d;(void)q;assert(false); }
client_ref IOHIDEventSystemClientCreateWithType(CFAllocatorRef a,int t,CFDictionaryRef p) {
    (void)a;(void)t;(void)p;return allow_service?(client_ref)CFRetain(CFSTR("client")):NULL;
}
void IOHIDEventSystemClientScheduleWithDispatchQueue(client_ref c,dispatch_queue_t q) { (void)c;event_queue=q; }
void IOHIDEventSystemClientUnscheduleFromDispatchQueue(client_ref c,dispatch_queue_t q) { (void)c;assert(q==event_queue); }
service_ref IOHIDVirtualServiceClientCreateWithCallbacks(client_ref c,CFDictionaryRef p,const struct service_callbacks *v,void *t,void *r) {
    (void)c;(void)p;assert(v->version==2);
    dispatch_async(event_queue,^{v->set_property(t,r,NULL,CFSTR("DeviceOpenedByEventSystem"),kCFBooleanTrue);});
    return (service_ref)CFRetain(CFSTR("service"));
}
Boolean IOHIDVirtualServiceClientRemove(service_ref s) { (void)s;removed++;return true; }
Boolean IOHIDVirtualServiceClientDispatchEvent(service_ref s,event_ref e) { assert(s&&e);dispatched++;return true; }
event_ref IOHIDEventCreateKeyboardEvent(CFAllocatorRef a,uint64_t t,uint32_t p,uint32_t u,Boolean d,uint32_t o) {
    (void)a;(void)t;(void)p;(void)u;(void)d;(void)o;return (event_ref)CFRetain(CFSTR("event"));
}
int main(void) {
    const uint8_t descriptor[]={0x05,1,0x09,6,0xa1,1,0x05,7,0x19,0xe0,0x29,0xe7,
        0x15,0,0x25,1,0x75,1,0x95,8,0x81,2,0x75,8,0x95,1,0x81,1,
        0x19,0,0x29,0x65,0x15,0,0x25,0x65,0x75,8,0x95,6,0x81,0,0xc0};
    CFMutableDictionaryRef p=CFDictionaryCreateMutable(NULL,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    CFDataRef data=CFDataCreate(NULL,descriptor,sizeof(descriptor));
    CFDictionarySetValue(p,CFSTR("Transport"),CFSTR("Bluetooth"));
    CFDictionarySetValue(p,CFSTR("ReportDescriptor"),data);CFRelease(data);
    enabled=true;server_mode=true;hid_type=_CFRuntimeRegisterClass(&hid_class);assert(hid_type);
    dispatch_queue_t q=dispatch_queue_create("test.native",NULL);
    for (unsigned i=0;i<20;i++) {
        user_device d=create(NULL,p,0);assert(d&&state_of(d));
        uint8_t press[]={2,0,4,0,0,0,0,0};
        assert(report(d,mach_absolute_time(),press,sizeof(press))==0);
        schedule(d,q);register_get(d,NULL,NULL);register_set(d,NULL,NULL);
        assert(dispatched==i*4+2);
        if (i%2) unschedule(d,q);
        CFRelease(d);assert(dispatched==i*4+4);
    }
    assert(removed==20);
    CFDataRef plist=CFPropertyListCreateData(NULL,p,kCFPropertyListBinaryFormat_v1_0,0,NULL);
    for (unsigned i=0;i<10;i++) {
        int pair[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,pair));
        assert(!peer_is(pair[0],0,"/usr/sbin/bluetoothd"));
        atomic_fetch_add(&active_connections,1);pthread_t thread;
        assert(!pthread_create(&thread,NULL,serve,(void *)(intptr_t)pair[1]));
        struct lbh_reply reply;
        assert(lbh_send(pair[0],LBH_IPC_CREATE,0,CFDataGetBytePtr(plist),(uint32_t)CFDataGetLength(plist)));
        assert(lbh_io(pair[0],&reply,sizeof(reply),false)&&reply.status==0);
        uint8_t press[]={2,0,4,0,0,0,0,0};
        assert(lbh_send(pair[0],LBH_IPC_INPUT,mach_absolute_time(),press,sizeof(press)));
        assert(lbh_io(pair[0],&reply,sizeof(reply),false)&&reply.status==0&&reply.events==2);
        if (i%2) {
            assert(lbh_send(pair[0],LBH_IPC_CLOSE,0,NULL,0));
            assert(lbh_io(pair[0],&reply,sizeof(reply),false)&&reply.status==0&&reply.events==4);
        }
        close(pair[0]);pthread_join(thread,NULL);
        assert(dispatched==80+(i+1)*4);assert(!atomic_load(&active_connections));
    }
    /* Exercise the actual proxy: bad local input must not tear down IPC, and
     * a real input report is valid before output callbacks are scheduled. */
    {
        int pair[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,pair));
        atomic_fetch_add(&active_connections,1);pthread_t thread;
        assert(!pthread_create(&thread,NULL,serve,(void *)(intptr_t)pair[1]));
        struct lbh_reply reply;
        assert(lbh_send(pair[0],LBH_IPC_CREATE,0,CFDataGetBytePtr(plist),(uint32_t)CFDataGetLength(plist)));
        assert(lbh_io(pair[0],&reply,sizeof(reply),false)&&reply.status==0);
        struct hid_object *proxy=(struct hid_object *)_CFRuntimeCreateInstance(NULL,hid_type,sizeof(*proxy)-sizeof(proxy->base),NULL);
        assert(proxy);struct hid_state *s=calloc(1,sizeof(*s));assert(s);
        pthread_mutex_init(&s->lock,NULL);atomic_init(&s->refs,1);s->remote=true;s->remote_fd=pair[0];s->opened=true;proxy->state=s;
        uint8_t press[]={2,0,4,0,0,0,0,0};
        assert(report(proxy,mach_absolute_time(),press,0)!=0);assert(s->remote_fd==pair[0]);
        assert(report(proxy,mach_absolute_time(),press,sizeof(press))==0);assert(s->events==2);
        CFRelease(proxy);pthread_join(thread,NULL);assert(dispatched==124);
    }
    for (unsigned i=0;i<4;i++) {
        int pair[2];assert(!socketpair(AF_UNIX,SOCK_STREAM,0,pair));
        atomic_fetch_add(&active_connections,1);pthread_t thread;
        assert(!pthread_create(&thread,NULL,serve,(void *)(intptr_t)pair[1]));
        struct lbh_message h={LBH_IPC_MAGIC,1,LBH_IPC_CREATE,LBH_IPC_PROPERTIES+1,0};
        if (i==1) h.version=2;
        if (i==2) h.operation=99;
        size_t length=i==3?sizeof(h)-1:sizeof(h);
        assert(lbh_io(pair[0],&h,length,true));shutdown(pair[0],SHUT_WR);
        uint8_t byte;assert(recv(pair[0],&byte,1,0)==0);
        close(pair[0]);pthread_join(thread,NULL);
    }
    CFRelease(plist);
    allow_service=false;assert(!create(NULL,p,0));
    CFDictionarySetValue(p,CFSTR("Transport"),CFSTR("USB"));
    assert(!create(NULL,p,0));assert(native_calls==33);
    dispatch_release(q);CFRelease(p);
    puts("PASS: CF ownership, 20 local + 10 IPC lifecycles, EOF key release, bounded malformed IPC, service failure and peer/transport guards");
    return 0;
}
