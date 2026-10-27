#!/usr/bin/env python3
"""Run the production scanout queue/retirement with two competing host threads."""
import importlib.util,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('fences',ROOT/'devices/oneplus-9/tools/test-lemonade-display-fences.py');fences=importlib.util.module_from_spec(spec);spec.loader.exec_module(fences)
s=(ROOT/'xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp').read_text()
pre=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>
#include <atomic>
#include <condition_variable>
#include <chrono>
#include <functional>
#include <cstdarg>
#include <string>
#include <vector>
using namespace std::chrono;
using AbsoluteTime=uint64_t;
#define SWAP_QUEUE 2
#define SWAP_SIZE 0x46c
#define SWAP_ID 0x50
#define SWAP_WAIT_MS 5000
#define kMillisecondScale 1000000
#define THREAD_UNINT 0
struct StopWorker{};
struct IOLock{std::mutex mutex;std::condition_variable cv;};
static std::atomic<bool> stopWorker=false, immediateDeadline=false;
static uint64_t mach_absolute_time(){return duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count();}
static uint64_t micros(uint64_t n){return n/1000;}
static void clock_interval_to_deadline(unsigned ms,unsigned scale,uint64_t *p){*p=mach_absolute_time()+(immediateDeadline?0:uint64_t(ms)*scale);}
static void IOLockLock(IOLock *l){l->mutex.lock();}
static void IOLockUnlock(IOLock *l){l->mutex.unlock();}
static void IOLockWakeup(IOLock *l,void*,bool){l->cv.notify_all();}
static void IOLockSleep(IOLock *l,void*,int){
 std::unique_lock<std::mutex> held(l->mutex,std::adopt_lock);
 if(stopWorker)throw StopWorker{};
 l->cv.wait(held);
 if(stopWorker)throw StopWorker{};
 held.release();
}
static void IOLockSleepDeadline(IOLock *l,void*,uint64_t deadline,int){std::unique_lock<std::mutex> held(l->mutex,std::adopt_lock);l->cv.wait_until(held,steady_clock::time_point(nanoseconds(deadline)));held.release();}
static bool gTouchLat=false,gFences=true;
static std::mutex traceLock;
static std::vector<std::string> traces;
static void redwood_tl(const char *fmt,...){char line[512];va_list args;va_start(args,fmt);vsnprintf(line,sizeof(line),fmt,args);va_end(args);std::lock_guard<std::mutex> held(traceLock);traces.emplace_back(line);}
static std::vector<std::string> frameTraces(){std::lock_guard<std::mutex> held(traceLock);std::vector<std::string> result;for(auto &s:traces)if(s.rfind("tl: ",0)==0)result.push_back(s);return result;}
static void IOLog(const char*,...){}
struct MiniIOSurface{
 struct{unsigned allocSize=16,offset=0,bytesPerRow=16;}info;
 unsigned sid;uint32_t data[4]={};uint8_t *pixels(){return (uint8_t*)data;}
};
class IOMobileFramebuffer{
public:
 IOLock *fSwapLock;
 MiniIOSurface *fScanoutSurface=nullptr;uint32_t fScanoutSwapID=0;
 uint64_t fScanoutReady=0;
 bool fScanoutPending=false,fScanoutFailed=false,fStopping=false,fReadingSwap=false;
 unsigned fSwapsWaiting=0,fSwapFirst=0;uint32_t fReadingSwapID=0,fFenceGeneration=0;
 uint8_t fSwap[SWAP_QUEUE][SWAP_SIZE]={};
 bool queueComposite(MiniIOSurface*,uint32_t);bool waitScanout();void scanout();
 bool swapPending(uint32_t,uint32_t);bool stopForReturn();void finishSwap();
 std::function<void(MiniIOSurface*)> onRecord;
 void record(MiniIOSurface *s){onRecord(s);}
};
static std::mutex gate;static std::condition_variable event;
static unsigned transfers=0,allowTransfer=0,records=0,allowRecord=0;
static int transferResult=1;
static int lemonade_display_direct_present(void *pixels,unsigned,unsigned,unsigned,unsigned){
 std::unique_lock<std::mutex> held(gate);unsigned n=++transfers;uint32_t copy[4];memcpy(copy,pixels,16);event.notify_all();
 event.wait(held,[&]{return allowTransfer>=n;});
 assert(!memcmp(copy,pixels,16)); // CPU cannot overwrite the buffer while DMA is active.
 return transferResult;
}
static void until(const std::function<bool()> &predicate){std::unique_lock<std::mutex> held(gate);assert(event.wait_for(held,seconds(3),predicate));}
static void allow(unsigned transfer,unsigned record){std::lock_guard<std::mutex> held(gate);allowTransfer=transfer;allowRecord=record;event.notify_all();}
static void finishWorker(IOMobileFramebuffer &f,std::thread &worker){IOLockLock(f.fSwapLock);stopWorker=true;IOLockWakeup(f.fSwapLock,nullptr,false);IOLockUnlock(f.fSwapLock);worker.join();stopWorker=false;}
'''
post=r'''
int main(){
 IOLock lock;IOMobileFramebuffer f;f.fSwapLock=&lock;
 MiniIOSurface a{{16,0,16},1,{10,11,12,13}},b{{16,0,16},2,{20,21,22,23}};
 f.onRecord=[&](MiniIOSurface*){
   IOLockLock(&lock);assert(f.fScanoutPending&&f.swapPending(f.fScanoutSwapID,0));IOLockUnlock(&lock);
   std::unique_lock<std::mutex> held(gate);unsigned n=++records;event.notify_all();event.wait(held,[&]{return allowRecord>=n;});
 };
 std::thread worker([&]{try{f.scanout();}catch(const StopWorker&) {}});
 assert(f.queueComposite(&a,0xffffffffu));until([]{return transfers==1;});
 std::atomic<bool> prepared=false,queued=false;
 std::thread cpu([&]{b.data[0]=99;prepared=true;assert(f.queueComposite(&b,0));queued=true;});
 while(!prepared)std::this_thread::yield();
 IOLockLock(&lock);assert(f.fScanoutSurface==&a&&f.swapPending(0xffffffffu,0)&&f.swapPending(123,2));IOLockUnlock(&lock);
 assert(!queued&&a.data[0]==10);
 allow(1,0);until([]{return records==1;});assert(!queued);
 IOLockLock(&lock);assert(f.fScanoutPending);IOLockUnlock(&lock);
 allow(1,1);until([]{return transfers==2;});cpu.join();assert(queued);
 a.data[0]=77; // First buffer is reusable only after its transfer and capture completed.
 IOLockLock(&lock);assert(f.fScanoutSurface==&b&&!f.swapPending(0xffffffffu,0)&&f.swapPending(0,0));IOLockUnlock(&lock);
 puts("PASS next composition overlaps prior DMA; queued transfer and buffer reuse wait for real completion and capture");
 std::atomic<bool> drained=false;
 std::thread shutdown([&]{assert(f.stopForReturn());drained=true;});
 for(;;){IOLockLock(&lock);bool stopping=f.fStopping;IOLockUnlock(&lock);if(stopping)break;std::this_thread::yield();}
 assert(!drained);allow(2,2);shutdown.join();assert(drained&&transfers==2&&records==2&&!f.fScanoutPending);
 finishWorker(f,worker);
 puts("PASS shutdown includes the outstanding display transfer, including wrapped swap IDs");
 // Exercise both production retirement orderings. Display completion must
 // precede exactly one retirement, even when source release happens first.
 gTouchLat=true;
 for(bool sourceFirst:{true,false}){
  {std::lock_guard<std::mutex> held(traceLock);traces.clear();}
  {std::lock_guard<std::mutex> held(gate);transfers=records=allowTransfer=allowRecord=0;}
  IOLock orderLock;IOMobileFramebuffer order;order.fSwapLock=&orderLock;
  order.fReadingSwap=true;order.fReadingSwapID=45;
  order.onRecord=[&](MiniIOSurface*){std::unique_lock<std::mutex> held(gate);++records;event.notify_all();event.wait(held,[]{return allowRecord>=1;});};
  std::thread orderWorker([&]{try{order.scanout();}catch(const StopWorker&) {}});
  assert(order.queueComposite(&a,45));until([]{return transfers==1;});assert(frameTraces().empty());
  if(sourceFirst){order.finishSwap();assert(frameTraces().empty());}
  allow(1,0);until([]{return records==1;});auto duringCapture=frameTraces();
  assert(duringCapture.size()==1&&duringCapture[0].rfind("tl: v 45 ",0)==0);
  allow(1,1);
  for(;;){IOLockLock(&orderLock);bool pending=order.fScanoutPending;IOLockUnlock(&orderLock);if(!pending)break;std::this_thread::yield();}
  if(!sourceFirst){assert(frameTraces().size()==1);order.finishSwap();}
  auto final=frameTraces();assert(final.size()==2&&final[1].rfind("tl: r 45 ",0)==0);
  assert(order.stopForReturn());finishWorker(order,orderWorker);
 }
 puts("PASS both source-first and display-first completion emit real display completion then exactly one retirement");
 gTouchLat=false;
 // A transfer that cannot prove completion must retain ownership and its pending fence.
 IOLock badLock;IOMobileFramebuffer bad;bad.fSwapLock=&badLock;bad.onRecord=[](MiniIOSurface*){assert(false);};
 {std::lock_guard<std::mutex> held(gate);transferResult=-1;allowTransfer=3;}
 std::thread failedWorker([&]{try{bad.scanout();}catch(const StopWorker&) {}});
 assert(bad.queueComposite(&a,33));
 for(;;){IOLockLock(&badLock);bool failed=bad.fScanoutFailed;IOLockUnlock(&badLock);if(failed)break;std::this_thread::yield();}
 assert(!bad.waitScanout()&&!bad.queueComposite(&b,34));
 IOLockLock(&badLock);assert(bad.fScanoutPending&&bad.fScanoutSurface==&a&&bad.swapPending(33,0));IOLockUnlock(&badLock);
 immediateDeadline=true;assert(!bad.stopForReturn());assert(bad.fScanoutPending);
 finishWorker(bad,failedWorker);
 IOLock stalledLock;IOMobileFramebuffer stalled;stalled.fSwapLock=&stalledLock;stalled.fScanoutPending=true;stalled.fScanoutSurface=&a;
 assert(!stalled.waitScanout()&&!stalled.queueComposite(&b,8));assert(stalled.fScanoutSurface==&a&&stalled.fScanoutPending);
 puts("PASS failure and timeout retain the buffer, refuse a new transfer and never manufacture retirement");
}
'''
functions='\n'.join(fences.method(s,n) for n in ('swapWord','IOMobileFramebuffer::waitScanout','IOMobileFramebuffer::queueComposite','IOMobileFramebuffer::scanout','IOMobileFramebuffer::swapPending','IOMobileFramebuffer::stopForReturn','IOMobileFramebuffer::finishSwap'))
with tempfile.TemporaryDirectory(prefix='lemonade-pipeline-') as temp:
 p=Path(temp);(p/'test.cpp').write_text(pre+functions+post)
 subprocess.run(['clang++','-std=c++17','-pthread','-fsanitize=address,undefined',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,timeout=15)
