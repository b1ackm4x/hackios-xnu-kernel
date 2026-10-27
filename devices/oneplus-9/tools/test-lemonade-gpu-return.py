#!/usr/bin/env python3
"""Exercise the actual GPU admission/drain with competing host threads."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('fences',Path(__file__).with_name('test-lemonade-display-fences.py'))
fences=importlib.util.module_from_spec(spec);spec.loader.exec_module(fences)
s=(ROOT/'xnu/files/iokit/Kernel/RedwoodGPU.cpp').read_text()
block=s[s.index('static IOLock * gGPUCallLock;'):s.index('\nstatic bool\ngpuDrainUserCalls')]
for name in ('RedwoodGPUUserClient::start','RedwoodGPUUserClient::clientClose','RedwoodGPUUserClient::clientMemoryForType','RedwoodGPUUserClient::externalMethod'):
 assert 'GPUUserCall call;' in fences.method(s,name),name
stop=fences.method(s,'RedwoodGPUStop')
assert stop.index('gpuDrainUserCalls()')<stop.index('MiniIOMobileFramebufferStop()')<stop.index('redwood_gpu_stop()')
pre=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <thread>
#include <atomic>
#include <condition_variable>
#include <chrono>
using namespace std::chrono;
using AbsoluteTime=uint64_t;
#define kMillisecondScale 1000000
#define THREAD_UNINT 0
struct IOLock{std::mutex mutex;std::condition_variable cv;};
static std::atomic<bool> immediateDeadline=false;
static uint64_t mach_absolute_time(){return duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count();}
static void clock_interval_to_deadline(unsigned ms,unsigned scale,uint64_t *p){*p=mach_absolute_time()+(immediateDeadline?0:uint64_t(ms)*scale);}
static void IOLockLock(IOLock *l){l->mutex.lock();}
static void IOLockUnlock(IOLock *l){l->mutex.unlock();}
static void IOLockWakeup(IOLock *l,void*,bool){l->cv.notify_all();}
static void IOLockSleep(IOLock *l,void*,int){std::unique_lock<std::mutex> held(l->mutex,std::adopt_lock);l->cv.wait(held);held.release();}
static void IOLockSleepDeadline(IOLock *l,void*,uint64_t deadline,int){std::unique_lock<std::mutex> held(l->mutex,std::adopt_lock);l->cv.wait_until(held,steady_clock::time_point(nanoseconds(deadline)));held.release();}
static void redwood_tl(const char*,...){}
'''
post=r'''
int main(){
 IOLock lock;gGPUCallLock=&lock;
 {GPUUserCall call;assert(gGPUCallsActive==1);}assert(gGPUCallsActive==0);
 puts("PASS every admitted method leaves the active count on scope exit");
 std::atomic<bool> entered=false,release=false,drained=false,lateEntered=false;
 std::thread worker([&]{GPUUserCall call;entered=true;while(!release)std::this_thread::yield();});
 while(!entered)std::this_thread::yield();
 std::thread shutdown([&]{assert(gpuDrainUserCalls());drained=true;});
 for(;;){IOLockLock(&lock);bool stopping=gGPUCallsStopping;IOLockUnlock(&lock);if(stopping)break;std::this_thread::yield();}
 std::thread late([&]{GPUUserCall call;lateEntered=true;});
 for(;;){IOLockLock(&lock);bool parked=gGPUCallsParked==1;IOLockUnlock(&lock);if(parked)break;std::this_thread::yield();}
 assert(!drained&&!lateEntered);release=true;worker.join();shutdown.join();
 assert(drained&&!lateEntered&&gGPUCallsActive==0&&gGPUCallsStopping);
 puts("PASS shutdown waits for an existing call; a concurrent caller cannot enter or receive an allocation error");
 // Test-only release of the terminal gate allows pthread teardown. Production never reopens it.
 IOLockLock(&lock);gGPUCallsStopping=false;IOLockWakeup(&lock,&gGPUCallsStopping,false);IOLockUnlock(&lock);late.join();assert(lateEntered&&gGPUCallsActive==0);
 {GPUUserCall call;immediateDeadline=true;assert(!gpuDrainUserCalls());assert(gGPUCallsActive==1&&gGPUCallsStopping);}
 assert(gGPUCallsActive==0);
 puts("PASS timeout retains the active call and leaves admission closed; hardware shutdown is conditional on successful drain");
 puts("PASS all four user entry points are guarded; call drain precedes display drain and physical GPU stop");
}
'''
with tempfile.TemporaryDirectory(prefix='lemonade-gpu-return-') as temp:
 p=Path(temp);(p/'test.cpp').write_text(pre+block+fences.method(s,'gpuDrainUserCalls')+post)
 subprocess.run(['clang++','-std=c++17','-pthread','-fsanitize=address,undefined',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,timeout=15)
