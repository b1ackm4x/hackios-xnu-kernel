#!/usr/bin/env python3
"""Exercise the production command-buffer state machine with a gated GPU fence.

Compiles its actual methods against host Foundation and a controlled fence API.
This catches premature completion, callback ordering, waiter and lifetime races;
hardware rendering is covered separately on the phone.
"""
from pathlib import Path
import importlib.util
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lifetime', ROOT/'tools/test-lemonade-metal-lifetime.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
source = (ROOT/'userland/src/redwoodmetal.m').read_text()
block = helper.implementation(source, 'RedwoodMetalCommandBuffer')
ivar_end = helper.balanced(block, block.index('{'))
methods = helper.methods(block, {'initWithRedwoodQueue','redwoodReturnSlot','dealloc','status','error',
    'commit','redwoodFinish','failWith','waitUntilCompleted','waitUntilScheduled','redwoodDeliver',
    'addScheduledHandler','addCompletedHandler'})
prelude = r'''
#import <Foundation/Foundation.h>
#include <dispatch/dispatch.h>
#include <pthread.h>
#include <assert.h>
#include <stdio.h>
#include <unistd.h>
#include <stdarg.h>
static void rw_print(const char *fmt, ...) {}
#define RW_ONCE(...) rw_print(__VA_ARGS__)
static BOOL rw_metrics_active=NO;
static pthread_once_t rw_metrics_once=PTHREAD_ONCE_INIT;
static void rw_metrics_init(void) {}
static uint64_t rw_metrics_us(void) {return 0;}
static void rw_metrics_record(uint64_t a,uint64_t b,uint64_t c,NSUInteger d) {}
enum {RW_CB_NOT_ENQUEUED=0,RW_CB_ENQUEUED=1,RW_CB_COMMITTED=2,RW_CB_SCHEDULED=3,RW_CB_COMPLETED=4,RW_CB_ERROR=5};
struct rwa_device {int placeholder;};
struct rwa_completion {int placeholder;};
static struct rwa_device device;
static struct rwa_completion completion;
static dispatch_semaphore_t gpuGate,waitEntered;
static BOOL encodeFailure,fenceFailure;
static unsigned submitted,waited,deaths;
static int begin(struct rwa_device *d,char *why,size_t size) {return 0;}
static int end(struct rwa_device *d,char *why,size_t size) {return 0;}
static int submit(struct rwa_device *d,struct rwa_completion **p,char *why,size_t size) {
    submitted++;*p=&completion;return 0;
}
static int complete(struct rwa_completion *p,char *why,size_t size) {
    assert(p==&completion);dispatch_semaphore_signal(waitEntered);
    assert(dispatch_semaphore_wait(gpuGate,dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC))==0);
    waited++;
    if(fenceFailure){snprintf(why,size,"controlled GPU timeout");return -1;}
    return 0;
}
static struct {int (*batch_begin)(struct rwa_device *,char *,size_t);
    int (*batch_end)(struct rwa_device *,char *,size_t);
    int (*batch_submit)(struct rwa_device *,struct rwa_completion **,char *,size_t);
    int (*completion_wait)(struct rwa_completion *,char *,size_t);
    int (*finish)(struct rwa_device *,char *,size_t);
} mesa={begin,end,submit,complete,end};
@interface HostDevice:NSObject
- (struct rwa_device *)mesaDevice;
@end
@implementation HostDevice
- (struct rwa_device *)mesaDevice {return &device;}
@end
@interface RedwoodMetalCommandQueue:NSObject {
@public dispatch_queue_t fences,callbacks;dispatch_semaphore_t slots;unsigned taken,returned;
    HostDevice *_owner;
}
- (id)device;
- (void)redwoodTookSlot;
- (void)redwoodReturnedSlot;
- (dispatch_queue_t)redwoodFenceQueue;
- (dispatch_queue_t)redwoodCallbackQueue;
- (dispatch_queue_t)redwoodCompletionQueue;
@end
@implementation RedwoodMetalCommandQueue
- (instancetype)init {if((self=[super init])){
    fences=dispatch_queue_create("test.fences",DISPATCH_QUEUE_SERIAL);
    callbacks=dispatch_queue_create("test.callbacks",DISPATCH_QUEUE_SERIAL);
    slots=dispatch_semaphore_create(0);_owner=[[HostDevice alloc]init];}return self;}
- (id)device{return _owner;}
- (void)redwoodTookSlot {taken++;}
- (void)redwoodReturnedSlot {returned++;}
- (dispatch_queue_t)redwoodFenceQueue{return fences;}
- (dispatch_queue_t)redwoodCallbackQueue{return callbacks;}
- (dispatch_queue_t)redwoodCompletionQueue{return nil;}
- (void)dealloc{[fences release];[callbacks release];[slots release];[_owner release];[super dealloc];}
@end
static dispatch_semaphore_t redwood_queue_semaphore(RedwoodMetalCommandQueue *q){return q->slots;}
static void redwood_clear_semaphore_debt(id c){}
@interface _MTLCommandBuffer:NSObject
- (instancetype)initWithQueue:(id)q retainedReferences:(BOOL)r synchronousDebugMode:(BOOL)d;
@end
@implementation _MTLCommandBuffer
- (instancetype)initWithQueue:(id)q retainedReferences:(BOOL)r synchronousDebugMode:(BOOL)d{return [super init];}
@end
@interface RedwoodMetalCommandBuffer:_MTLCommandBuffer
- (instancetype)initWithRedwoodQueue:(RedwoodMetalCommandQueue *)q;
- (NSUInteger)status;
- (NSError *)error;
- (void)commit;
- (void)redwoodFinish;
- (void)redwoodReturnSlot;
- (void)runRecordedWork;
- (void)failWith:(const char *)why;
- (void)holdForTest:(id)value;
- (void)waitUntilCompleted;
- (void)waitUntilScheduled;
- (void)redwoodDeliver:(void (^)(id))handler;
- (void)addScheduledHandler:(void (^)(id))handler;
- (void)addCompletedHandler:(void (^)(id))handler;
@end
@interface Probe:NSObject @end
@implementation Probe
- (void)dealloc{__atomic_fetch_add(&deaths,1,__ATOMIC_SEQ_CST);[super dealloc];}
@end
'''
extra = r'''
- (void)runRecordedWork {if(encodeFailure)[self failWith:"controlled partial encoding error"];}
- (void)holdForTest:(id)value {[_work addObject:value];}
@end
static void drain(RedwoodMetalCommandQueue *q) {
    dispatch_sync(q->fences,^{});dispatch_sync(q->callbacks,^{});
}
int main(void) {
    @autoreleasepool {
        RedwoodMetalCommandQueue *q=[[RedwoodMetalCommandQueue alloc]init];
        gpuGate=dispatch_semaphore_create(0);waitEntered=dispatch_semaphore_create(0);
        for(unsigned round=0;round<90;round++) @autoreleasepool {
            encodeFailure=round%3==1;fenceFailure=round%3==2;
            BOOL failed=encodeFailure||fenceFailure;
            unsigned oldSubmitted=submitted,oldReturned=q->returned,oldDeaths=deaths;
            RedwoodMetalCommandBuffer *cb=[[RedwoodMetalCommandBuffer alloc]initWithRedwoodQueue:q];
            Probe *probe=[[Probe alloc]init];[cb holdForTest:probe];[probe release];
            dispatch_semaphore_t scheduled=dispatch_semaphore_create(0),done=dispatch_semaphore_create(0);
            dispatch_semaphore_t waiter=dispatch_semaphore_create(0);
            __block unsigned stage=0;
            [cb addScheduledHandler:^(id c){assert([c status]>=RW_CB_SCHEDULED);stage=1;dispatch_semaphore_signal(scheduled);}];
            [cb addCompletedHandler:^(id c){assert(stage==1);assert([c status]==(failed?RW_CB_ERROR:RW_CB_COMPLETED));stage=2;dispatch_semaphore_signal(done);}];
            [cb commit];[cb commit];assert(submitted==oldSubmitted+1);
            assert(dispatch_semaphore_wait(waitEntered,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC))==0);
            [cb waitUntilScheduled];
            assert(dispatch_semaphore_wait(scheduled,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC))==0);
            assert(dispatch_semaphore_wait(done,DISPATCH_TIME_NOW)!=0);
            assert(q->returned==oldReturned && deaths==oldDeaths);
            dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{[cb waitUntilCompleted];dispatch_semaphore_signal(waiter);});
            assert(dispatch_semaphore_wait(waiter,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_MSEC))!=0);
            [cb release]; /* only driver/handler/waiter ownership remains */
            dispatch_semaphore_signal(gpuGate);
            assert(dispatch_semaphore_wait(waiter,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC))==0);
            assert(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC))==0);
            drain(q);assert(q->returned==oldReturned+1);
            for(unsigned i=0;deaths==oldDeaths&&i<100;i++)usleep(1000);
            assert(deaths==oldDeaths+1);
            [scheduled release];[done release];[waiter release];
        }
        assert(submitted==90 && waited==90 && q->taken==90 && q->returned==90);
        /* Register handlers after completion and on an empty synchronous batch. */
        mesa.batch_submit=NULL;encodeFailure=NO;fenceFailure=NO;
        RedwoodMetalCommandBuffer *cb=[[RedwoodMetalCommandBuffer alloc]initWithRedwoodQueue:q];
        [cb commit];[cb waitUntilCompleted];assert(cb.status==RW_CB_COMPLETED);
        __block unsigned late=0;
        [cb addScheduledHandler:^(id c){late++;}];[cb addCompletedHandler:^(id c){late++;}];
        drain(q);assert(late==2);[cb release];
        cb=[[RedwoodMetalCommandBuffer alloc]initWithRedwoodQueue:q];[cb release];
        assert(q->taken==q->returned);
        [gpuGate release];[waitEntered release];[q release];
        puts("PASS: asynchronous return, real completion wait, ordered callbacks, partial errors, GPU errors, early release, late handlers, synchronous fallback, and exact slot return (90 gated fences)");
    }
    return 0;
}
'''
text = (prelude + '\n' + block[:ivar_end] + '\n' + methods + '\n' + extra).replace('_MTLCommandBuffer', 'HostCommandBufferBase')
with tempfile.TemporaryDirectory(prefix='metal-completion-') as d:
    path=Path(d)/'completion.m';binary=Path(d)/'completion';path.write_text(text)
    subprocess.run(['clang','-fblocks','-Werror','-Wno-unused-function','-framework','Foundation',str(path),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=30)
