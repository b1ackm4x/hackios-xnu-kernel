#!/usr/bin/env python3
"""Execute the framebuffer's real queue, wait and retirement code with host locks.

The actual presenter loop and close method run unchanged. GPU/copy and kernel
scheduling are substitutes; hooks query while references are being dropped and
while kept surfaces are cleared. This verifies ordering, not panel DMA timing.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def method(source, name):
    match = re.search(r'^(?:static )?\w+\s*\n' + re.escape(name) + r'\([^;]*?\)\s*\{', source, re.M)
    if match is None:
        raise ValueError('Missing method: ' + name)
    start = source.index('{', match.start())
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                    lambda m: ' ' * len(m[0]), source, flags=re.S)
    depth = 0
    for end in range(start, len(source)):
        depth += (masked[end] == '{') - (masked[end] == '}')
        if not depth:
            return source[match.start():end + 1]
    raise ValueError('Unclosed method: ' + name)


PRELUDE = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#define SWAP_QUEUE 2
#define SWAP_SIZE 0x46c
#define SWAP_ID 0x50
#define SWAP_LAYERS 4
#define SWAP_SURFACE(n) (0x54 + 4 * (n))
#define SWAP_WAIT_MS 5000
#define SWAP_HELD_MS 700
#define FRAME_PAUSE 90
#define NSEC_PER_SEC 1000000000ull
#define NSEC_PER_MSEC 1000000ull
#define kMillisecondScale 1000000
#define THREAD_ABORTSAFE 2
#define THREAD_UNINT 0
#define THREAD_TIMED_OUT 1
#define THREAD_INTERRUPTED 2
using IOReturn = uint32_t;
using AbsoluteTime = uint64_t;
constexpr IOReturn kIOReturnSuccess = 0, kIOReturnBadArgument = 0xe00002c2,
    kIOReturnNotOpen = 0xe00002cd, kIOReturnBusy = 0xe00002d5,
    kIOReturnTimeout = 0xe00002d6, kIOReturnAborted = 0xe00002eb,
    kIOReturnNotReady = 0xe00002d8;
struct IOLock { bool held = false; };
static uint64_t now, lastDeadline;
static unsigned sleeps, wakes, drops, kept, shown, copies, cancelled, unregistered;
static bool allWoken;
static std::function<int()> onSleep;
static std::function<void()> onDrop, onKept;
static bool gTouchLat = false, gFences = true;
static uint64_t mach_absolute_time() { return now; }
static uint64_t micros(uint64_t t) { return t / 1000; }
static unsigned refreshHz() { return 60; }
static unsigned swapQueue() { return SWAP_QUEUE; }
static void redwood_tl(const char *, ...) {}
static void nanoseconds_to_absolutetime(uint64_t ns, uint64_t *out) { *out = ns; }
static void clock_interval_to_deadline(uint32_t ms, uint32_t scale, uint64_t *out) { *out = now + uint64_t(ms) * scale; }
static void IOLockLock(IOLock *l) { assert(!l->held); l->held = true; }
static void IOLockUnlock(IOLock *l) { assert(l->held); l->held = false; }
static void IOLockWakeup(IOLock *l, void *, bool one) { assert(l->held); wakes++; allWoken = !one; }
struct EndPresenter {};
static bool showSafe = true;
static void IOLog(const char *, ...) {}
static void IOSleep(unsigned) { throw EndPresenter{}; }
static void IOLockSleep(IOLock *l, void *, int) { IOLockUnlock(l); throw EndPresenter{}; }
static int IOLockSleepDeadline(IOLock *l, void *, uint64_t deadline, int) {
    assert(l->held); sleeps++; lastDeadline = deadline; IOLockUnlock(l);
    int result;
    if (onSleep) result = onSleep();
    else { now = deadline; result = THREAD_TIMED_OUT; }
    IOLockLock(l); return result;
}
struct MiniIOSurface { unsigned id, refs, drawn; };
static IOLock *gGPULock;
static bool hostGPUReady, hostGPUComplete;
static unsigned hostGPUWaits;
static bool redwood_gpu_ready() { return hostGPUReady; }
static bool gpuWait(uint32_t, uint64_t) { assert(gGPULock && gGPULock->held); hostGPUWaits++; return hostGPUComplete; }
static MiniIOSurface surface[8];
static MiniIOSurface *MiniIOSurfaceCopy(unsigned id) { assert(id < 8); copies++; surface[id].refs++; return &surface[id]; }
static void MiniIOSurfaceDrop(MiniIOSurface *s) { assert(s->refs); if(onDrop)onDrop(); s->refs--; drops++; }
static void MiniIOSurfaceClearKept() { if(onKept)onKept(); kept++; }
static void lemonade_display_vsync_unregister(void *) { unregistered++; }
static void thread_call_cancel_wait(void *) { cancelled++; }
class IOMobileFramebuffer {
public:
    IOLock *fSwapLock;
    uint8_t fSwap[SWAP_QUEUE][SWAP_SIZE] = {};
    MiniIOSurface *fSwapSurfaces[SWAP_QUEUE][SWAP_LAYERS] = {};
    unsigned fSwapFirst = 0, fSwapsWaiting = 0;
    uint32_t fReadingSwapID = 0;
    bool fReadingSwap = false, fStopping = false;
    bool fScanoutPending = false;
    uint32_t fScanoutSwapID = 0;
    uint32_t fFenceGeneration = 0, fStatSwapsLate = 0, fStatHeld = 0, fStatLost = 0;
    uint64_t fSwapTime = 0;
    bool swapPending(uint32_t, uint32_t);
    IOReturn waitSwap(uint32_t, uint32_t, uint32_t, const bool *);
    void finishSwap();
    IOReturn present(const uint8_t *);
    bool stopForReturn();
    void presenter();
    bool show(const uint8_t *, MiniIOSurface **);
};
class IOMobileFramebufferUserClient {
public:
    IOMobileFramebuffer *fOwner;
    IOLock *fLock;
    bool fClosed = false, fFenceClosed = false, inactive = false;
    void *fVSyncCall = nullptr;
    bool isInactive() { return inactive; }
    void terminate() { inactive = true; }
    IOReturn clientClose();
};
'''

TESTS = r'''
static bool open = false;
static void expectBusy(IOMobileFramebuffer &f, unsigned id) {
    for (unsigned flags : {0x80000000u, 0x80000001u, 0x80000002u})
        assert(f.waitSwap(id, flags, 0, &open) == kIOReturnBusy);
}
bool IOMobileFramebuffer::show(const uint8_t *swap, MiniIOSurface **surfaces) {
    unsigned id = swapWord(swap, SWAP_ID);
    expectBusy(*this, id);
    assert(surfaces[0] && surfaces[0]->refs);
    IOLockLock(fSwapLock);
    fSwapFirst = (fSwapFirst + 1) % SWAP_QUEUE;
    fSwapsWaiting--;
    IOLockWakeup(fSwapLock, &fSwapsWaiting, true);
    IOLockUnlock(fSwapLock);
    expectBusy(*this, id); // the queue no longer contains this frame
    shown++;
    return showSafe;
}
static IOReturn queue(IOMobileFramebuffer &f, unsigned id, unsigned sid = 1) {
    uint8_t swap[SWAP_SIZE] = {};
    memcpy(swap + SWAP_ID, &id, sizeof(id));
    memcpy(swap + SWAP_SURFACE(0), &sid, sizeof(sid));
    return f.present(swap);
}
static void runPresenter(IOMobileFramebuffer &f) {
    try { f.presenter(); assert(false); } catch (const EndPresenter &) {}
}
int main() {
    IOLock gpuLock;
    MiniIOSurface produced{};
    assert(MiniIOSurfaceDrawn(&produced,500));
    produced.drawn=17;
    assert(!MiniIOSurfaceDrawn(&produced,500));
    gGPULock=&gpuLock;
    assert(!MiniIOSurfaceDrawn(&produced,500) && hostGPUWaits==0);
    hostGPUReady=true;
    assert(!MiniIOSurfaceDrawn(&produced,500) && hostGPUWaits==1 && !gpuLock.held);
    hostGPUComplete=true;
    assert(MiniIOSurfaceDrawn(&produced,500) && hostGPUWaits==2 && !gpuLock.held);
    puts("PASS actual GPU readiness requires completed work and refuses an unavailable producer");
    IOLock lock, clientLock;
    IOMobileFramebuffer f{}; f.fSwapLock = &lock;
    IOMobileFramebufferUserClient client{}; client.fOwner = &f; client.fLock = &clientLock;
    for(unsigned i=0;i<8;i++)surface[i].id=i;
    assert(f.waitSwap(17,0,0,&open)==kIOReturnSuccess);
    assert(f.waitSwap(0,0x80000003u,0,&open)==kIOReturnSuccess);
    assert(f.waitSwap(0,4,0,&open)==kIOReturnBadArgument);
    assert(f.waitSwap(0,5,0,&open)==kIOReturnBadArgument);
    assert(sleeps==0);
    puts("PASS empty queue, poll, lifecycle and invalid modes");

    queue(f,17);
    expectBusy(f,17);
    assert(f.waitSwap(18,0x80000000u,0,&open)==kIOReturnSuccess);
    onDrop = [&] { expectBusy(f,17); };
    onKept = [&] { expectBusy(f,17); };
    runPresenter(f);
    assert(shown==1 && drops==1 && kept==1 && !surface[1].refs);
    assert(!f.fReadingSwap && !f.fSwapsWaiting && allWoken);
    assert(f.waitSwap(17,0,0,&open)==kIOReturnSuccess);
    onDrop = onKept = nullptr;
    puts("PASS actual presenter remains busy through dequeue, copy, Drop and ClearKept");

    queue(f,0xffffffffu); queue(f,0);
    expectBusy(f,0xffffffffu); expectBusy(f,0);
    assert(f.waitSwap(1,0x80000000u,0,&open)==kIOReturnSuccess);
    runPresenter(f);
    assert(f.waitSwap(0,0x80000002u,0,&open)==kIOReturnSuccess);
    puts("PASS wrapped IDs, wrapped queue and drain include every outstanding frame");

    f.fScanoutPending=true; f.fScanoutSwapID=19;
    expectBusy(f,19);
    assert(f.waitSwap(18,0x80000000u,0,&open)==kIOReturnSuccess);
    onSleep=[&] {f.fScanoutPending=false;return 0;};
    assert(f.waitSwap(19,0,9,&open)==kIOReturnSuccess);
    onSleep=nullptr;
    puts("PASS a copied frame remains pending until its owned display buffer finishes scanning");

    queue(f,22);
    unsigned oldSleeps=sleeps;
    onSleep=[&] { runPresenter(f); return 0; };
    assert(f.waitSwap(22,0,9,&open)==kIOReturnSuccess);
    assert(sleeps==oldSleeps+1 && !lock.held);
    puts("PASS blocking wait releases lock and observes real retirement");

    queue(f,23);
    uint64_t before=now; oldSleeps=sleeps;
    onSleep=[&] { now+=1000000; return 0; };
    assert(f.waitSwap(23,1,3,&open)==kIOReturnTimeout);
    assert(now==before+3000000 && sleeps==oldSleeps+3 && lastDeadline==now);
    onSleep=nullptr; before=now;
    assert(f.waitSwap(23,0,0,&open)==kIOReturnTimeout);
    assert(now==before+5000ull*1000000);
    onSleep=[&] { now=lastDeadline; runPresenter(f); return THREAD_TIMED_OUT; };
    assert(f.waitSwap(23,0,1,&open)==kIOReturnSuccess);
    puts("PASS millisecond deadline, bounded default, spurious wakes and completion at timeout");

    queue(f,24);
    onSleep=[] { return THREAD_INTERRUPTED; };
    assert(f.waitSwap(24,0,1,&open)==kIOReturnAborted);
    expectBusy(f,24);
    onSleep=[&] { client.clientClose(); return 0; };
    client.fVSyncCall=&client;
    assert(f.waitSwap(24,0,100,&client.fFenceClosed)==kIOReturnNotOpen);
    assert(client.fClosed && client.inactive && allWoken && cancelled==1 && unregistered==1);
    assert(!lock.held && !clientLock.held);
    expectBusy(f,24); // closing a client cannot falsely retire a source in use
    onSleep=nullptr;
    runPresenter(f);
    assert(copies==drops);
    puts("PASS interruption and actual clientClose wake waits without releasing active sources");

    {
        IOLock drainLock; IOMobileFramebuffer draining{}; draining.fSwapLock=&drainLock;
        queue(draining,51); queue(draining,52);
        unsigned oldCopies=copies;
        onSleep=[&] {
            assert(queue(draining,53)==kIOReturnNotReady && copies==oldCopies);
            runPresenter(draining); return 0;
        };
        assert(draining.stopForReturn() && draining.fStopping && !drainLock.held);
        assert(!draining.fReadingSwap && !draining.fSwapsWaiting && copies==drops);
        assert(queue(draining,54)==kIOReturnNotReady && copies==oldCopies);
        onSleep=nullptr;
    }
    puts("PASS shutdown rejects new frames and waits for actual presentation and source release");
    {
        IOLock drainLock; IOMobileFramebuffer draining{}; draining.fSwapLock=&drainLock;
        queue(draining,61); queue(draining,62);
        unsigned oldCopies=copies;
        bool insideStop=false;
        onSleep=[&] {
            if(insideStop) {runPresenter(draining);return 0;}
            insideStop=true;
            assert(draining.stopForReturn()); return 0;
        };
        assert(queue(draining,63)==kIOReturnNotReady && copies==oldCopies && copies==drops);
        assert(!drainLock.held); onSleep=nullptr;
    }
    puts("PASS producer already blocked on a full queue wakes into shutdown without adding a frame");
    {
        IOLock drainLock; IOMobileFramebuffer draining{}; draining.fSwapLock=&drainLock;
        queue(draining,71); unsigned oldDrops=drops; uint64_t began=now;
        assert(!draining.stopForReturn() && now-began==2000ull*1000000);
        assert(draining.fSwapsWaiting==1 && drops==oldDrops && surface[1].refs==1);
        expectBusy(draining,71);
        assert(queue(draining,72)==kIOReturnNotReady);
        runPresenter(draining);
        assert(draining.stopForReturn() && copies==drops);
    }
    puts("PASS bounded shutdown timeout keeps pending surfaces and never fabricates completion");
    showSafe=false;
    queue(f,25);
    unsigned oldDrops=drops, oldKept=kept;
    runPresenter(f);
    assert(f.fReadingSwap && f.fReadingSwapID==25 && surface[1].refs==1);
    assert(drops==oldDrops && kept==oldKept);
    expectBusy(f,25);
    assert(!f.stopForReturn() && f.fReadingSwap && surface[1].refs==1 && drops==oldDrops);
    puts("PASS failed hardware completion retains the actual source and withholds producer release");
    return 0;
}
'''


def main():
    source = (ROOT / 'xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp').read_text()
    names = ['swapWord', *(f'IOMobileFramebuffer::{n}' for n in
             ('swapPending', 'waitSwap', 'finishSwap', 'stopForReturn', 'present', 'presenter')),
             'IOMobileFramebufferUserClient::clientClose']
    gpu = (ROOT / 'xnu/files/iokit/Kernel/RedwoodGPU.cpp').read_text()
    generated = PRELUDE + '\n' + method(gpu, 'MiniIOSurfaceDrawn') + '\n' + '\n'.join(method(source, n) for n in names) + TESTS
    with tempfile.TemporaryDirectory(prefix='lemonade-display-fences-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(generated)
        subprocess.run(['clang++', '-std=c++17', '-g', '-O1', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=15)


if __name__ == '__main__':
    main()
