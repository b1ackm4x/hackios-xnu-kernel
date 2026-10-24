#!/usr/bin/env python3
"""Execute the display driver's return transactions with native host mocks.

The actual C driver is compiled. Only kernel services and ARM barrier/cache
instructions are substituted. Pending return callbacks run at the first IRQ
reenable; inaccessible memory detects any resumed MMIO access after quiesce.
This checks CPU sequencing, not SMMU hardware, panel timing, or multiple CPUs.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <sys/mman.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef int kern_return_t;
typedef void *DTEntry;
typedef void *thread_call_t;
#ifndef __printflike
#define __printflike(a,b)
#endif
#define SIMPLE_LOCK_DECLARE(n,x) int n
#define LCK_GRP_NULL 0
#define KERN_SUCCESS 0
#define kSuccess 0
#define KMA_KOBJECT 1
#define KMA_ZERO 2
#define KMA_DATA 4
#define VM_KERN_MEMORY_IOKIT 0
#define kernel_map 0
#define round_page(n) (((n) + 16383) & ~16383u)
#define NSEC_PER_MSEC 1000000ull
#define NSEC_PER_USEC 1000ull
#define NSEC_PER_SEC 1000000000ull
#define os_atomic_load(p,o) __atomic_load_n(p,__ATOMIC_SEQ_CST)
#define os_atomic_store(p,v,o) __atomic_store_n(p,v,__ATOMIC_SEQ_CST)
static bool host_interrupts = true;
static void (*host_pending)(void);
static unsigned host_callbacks;
static uint64_t host_now;
static bool host_wrong_pixels;
static vm_offset_t host_tables;
static uint32_t host_mdp[0x90000/4], host_smmu[0x100000/4], host_clocks[2];
static char host_log[32768];
static unsigned host_log_used;
static bool ml_set_interrupts_enabled(bool value) {
    bool previous = host_interrupts;
    host_interrupts = value;
    if (!previous && value && host_pending) {
        void (*callback)(void) = host_pending;
        host_pending = NULL;
        host_callbacks++;
        callback();
    }
    return previous;
}
static bool ml_get_interrupts_enabled(void) { return host_interrupts; }
static void simple_lock(int *lock, int group) { (void)lock; (void)group; }
static void simple_unlock(int *lock) { (void)lock; }
static void thread_call_enter(thread_call_t call) { (void)call; }
static uint64_t mach_absolute_time(void) { return host_now; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *n) { *n=t; }
static void nanoseconds_to_absolutetime(uint64_t n, uint64_t *t) { *t=n; }
static void clock_delay_until(uint64_t t) { host_now=t; }
static void delay(unsigned us) {
    host_now += (uint64_t)us*1000;
    host_mdp[0x350ac/4]++;
    host_mdp[0x350b0/4]=1;
    host_mdp[0x352b0/4]=2400;
    if (!host_wrong_pixels && host_mdp[0x4014/4]==0xe4d00000 && host_mdp[0x6014/4]==0xe4d00000) {
        host_mdp[0x44314/4]=0x12345678;
        host_mdp[0x45314/4]=0x87654321;
    }
}
static int SecureDTLookupEntry(void *a, const char *b, DTEntry *c) { return -1; }
static int SecureDTGetProperty(DTEntry a, const char *b, const void **c, unsigned *d) { return -1; }
static bool PE_parse_boot_argn(const char *n, void *p, int s) { return false; }
static int kmem_alloc(int m, vm_offset_t *p, unsigned n, int flags, int tag) { return 1; }
vm_offset_t ml_io_map(vm_offset_t p, vm_size_t n) { return 0; }
vm_offset_t pe_arm_get_soc_base_phys(void) { return 0; }
uint64_t kvtophys(vm_offset_t p) {
    if (host_tables && p>=host_tables && p<host_tables+131072) return 0x93000000+p-host_tables;
    return 0x92345000;
}
void loader_log_line(const char *s) { while (*s) { assert(host_log_used+1<sizeof(host_log)); host_log[host_log_used++]=*s++; } }
kern_return_t gic_v3_spi_attach(uint32_t n, bool edge, void (*cb)(void*,uint32_t), void *p) { return 0; }
void gic_v3_spi_mask(uint32_t n) {}
void gic_v3_spi_detach(uint32_t n) {}
'''

CASES = r'''
static void reset_fixture(void) {
    memset(&display_map,0,sizeof(display_map));
    memset(&irq,0,sizeof(irq));
    memset(votes,0,sizeof(votes));
    memset(host_mdp,0,sizeof(host_mdp));
    memset(host_smmu,0,sizeof(host_smmu));
    memset(host_log,0,sizeof(host_log)); host_log_used=0;
    host_clocks[0]=host_clocks[1]=0;
    display_stopping=display_restoring=false;
    host_interrupts=true; host_pending=NULL; host_callbacks=0; host_now=0;
    host_wrong_pixels=false;host_tables=0;
    direct_requested=direct_failed=false; direct_count=direct_fallbacks=direct_pages=direct_pitch=0;
    display_map.mdp=(uintptr_t)host_mdp;
    display_map.smmu=(uintptr_t)host_smmu;
    display_map.cb=(uintptr_t)host_smmu+0x85000;
    display_map.saved[0]=0xe0; display_map.saved[1]=0x60;
    for (unsigned i=0;i<9;i++)
        *(uint32_t *)(display_map.cb+display_cb_offsets[i])=display_map.saved[i];
    *(uint32_t *)(display_map.cb+0x58)=0x400;
    display_map.baseline[0]=0x12345678; display_map.baseline[1]=0x87654321;
    display_map.source[0]=display_map.source[1]=0xe4d00000;
    display_map.flush=0x20000000;
    host_mdp[0x4014/4]=host_mdp[0x6014/4]=0xe4d00000;
    host_mdp[0x15018/4]=display_map.flush;
}
static void assert_context_restored(void) {
    for (unsigned i=0;i<9;i++)
        assert(*(uint32_t *)(display_map.cb+display_cb_offsets[i])==display_map.saved[i]);
    assert(!display_map.active && !display_map.flip_active && !display_map.misr_active && !display_map.manual_active && !display_map.scale_active && !display_map.intf_misr_active);
    assert(!display_restoring);
}
static void return_after_complete_flip(void) {
    assert(host_mdp[0x4014/4]==0x12000000);
    assert(host_mdp[0x6014/4]==0x12000000);
    assert(host_mdp[0x15018/4]==3);
    assert(display_map.flip_active);
    lemonade_display_quiesce();
}
static void return_after_complete_context(void) {
    assert(display_map.active);
    assert(*(uint32_t *)(display_map.cb+0x20)==0x92345000);
    assert(*(uint32_t *)(display_map.cb+0x30)==0x803519);
    assert(*(uint32_t *)(display_map.cb+0x38)==0xf404ff44);
    lemonade_display_quiesce();
}
static void return_after_complete_vote(void) {
    assert(votes[0].changed && host_clocks[0]==1);
    lemonade_display_quiesce();
}
static void return_after_complete_manual_setup(void) {
    assert(display_map.manual_active);
    assert(display_map.sync_config==0x180085 && display_map.autorefresh==0x80000001);
    assert(host_mdp[0x35288/4]==0x180085 && host_mdp[0x352b4/4]==1);
    lemonade_display_quiesce();
}
static void return_after_complete_restore(void) {
    assert_context_restored();
    lemonade_display_quiesce();
}
static void return_after_complete_scaler_setup(void) {
    assert(display_map.scale_active);
    assert(host_mdp[0x4a04/4]==0x20034401 && host_mdp[0x6a04/4]==0x20034401);
    assert(host_mdp[0x6008/4]==562 && host_mdp[0x6a90/4]==1092086);
    for (unsigned n=0;n<2;n++) for (unsigned r=0;r<DISPLAY_SCALE_REGS;r++)
        assert(display_map.scale_saved[n][r]==0x40000000u+n*256+r);
    /* Check the actual emitted split/phase registers against an independent
     * color-cell reference, including both bilinear taps and the center seam. */
    const uint64_t unit=1u<<21;
    for (unsigned x=0;x<1080;x++) {
        unsigned pipe=0x4000+(x/540)*0x2000;
        uint64_t at=(uint64_t)host_mdp[(pipe+8)/4]*unit+host_mdp[(pipe+0xa90)/4]+
                    (uint64_t)(x%540)*host_mdp[(pipe+0xa10)/4];
        unsigned lo=at/unit,hi=(at+unit-1)/unit;
        assert(hi<1125 && lo/25==x/24 && hi/25==x/24);
    }
    for (unsigned y=0;y<2400;y++) {
        uint64_t at=host_mdp[0x4a94/4]+(uint64_t)y*host_mdp[0x4a14/4];
        unsigned lo=at/unit,hi=(at+unit-1)/unit;
        assert(hi<2436 && lo/203==y/200 && hi/203==y/200);
    }
    lemonade_display_quiesce();
}
int main(void) {
    reset_fixture();
    display_map.active=display_map.flip_active=display_map.misr_active=true;
    *(uint32_t *)(display_map.cb)=0x1127;
    host_pending=return_after_complete_flip;
    assert(!display_flip_buffer(1));
    assert(host_callbacks==1 && host_interrupts && display_stopping);
    assert_context_restored();
    assert(host_mdp[0x4014/4]==0xe4d00000 && host_mdp[0x6014/4]==0xe4d00000);
    assert(host_mdp[0x15018/4]==0x20000000);
    char *pixels=strstr(host_log,"source restore pixels=1");
    char *unmap=strstr(host_log,"mapping restore registers=1");
    char *cleanup=strstr(host_log,"mapping cleanup stopping=1 mapped=1 flipping=1 source=0x12000000/0x12000000 fsr=0x400 global=0 underrun=0");
    assert(cleanup && pixels && unmap && cleanup<pixels && pixels<unmap);
    puts("PASS: pending return observes a complete source pair and restores pixels before unmapping");

    reset_fixture();
    uint32_t baseline[2]={0x12345678,0x87654321};
    host_pending=return_after_complete_context;
    assert(!display_map_configure(baseline));
    assert(host_callbacks==1 && display_stopping);
    assert_context_restored();
    puts("PASS: context setup and journal finish before pending return can restore them");

    reset_fixture();
    host_pending=return_after_complete_vote;
    (void)bus_clock(0,(uintptr_t)&host_clocks[0],2,true);
    assert(host_callbacks==1 && host_clocks[0]==0 && !votes[0].changed);
    puts("PASS: pending return cannot leave an unjournaled clock vote");

    reset_fixture();
    display_map.active=true;
    *(uint32_t *)display_map.cb=0x1127;
    *(uint32_t *)(display_map.cb+0x20)=0x92345000;
    host_pending=return_after_complete_restore;
    display_map_restore();
    assert(host_callbacks==1 && display_stopping);
    assert_context_restored();
    puts("PASS: normal restoration cannot be interrupted halfway by a second restoration");

    reset_fixture();
    lemonade_display_quiesce();
    void *forbidden=mmap(NULL,4096,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(forbidden!=MAP_FAILED);
    assert(rd((uintptr_t)forbidden,0)==0);
    wr((uintptr_t)forbidden,1);
    display_map.mdp=(uintptr_t)forbidden;
    assert(!display_flip_buffer(0));
    assert(!bus_clock(0,(uintptr_t)forbidden,2,true));
    assert(!display_map_configure(baseline));
    assert(!display_misr_sample(baseline));
    display_map.active=true; display_map.cb=(uintptr_t)forbidden;
    display_map_restore();
    lemonade_display_quiesce();
    assert(host_interrupts);
    munmap(forbidden,4096);
    puts("PASS: resumed reads, writes, configuration and repeated cleanup never touch inaccessible MMIO");

    reset_fixture();
    host_interrupts=false;
    assert(bus_clock(0,(uintptr_t)&host_clocks[0],2,true));
    assert(!host_interrupts);
    lemonade_display_quiesce();
    assert(!host_interrupts && host_clocks[0]==0 && display_stopping);
    puts("PASS: return preserves an already disabled interrupt state");

    reset_fixture();
    display_map.active=display_map.flip_active=display_map.misr_active=true;
    host_mdp[0x35288/4]=0x180085;host_mdp[0x352b4/4]=0x80000001;
    host_mdp[0x3525c/4]=0xf0000; /* observed firmware mux; PP0 is the low nibble */
    host_pending=return_after_complete_manual_setup;
    assert(!display_manual_begin());
    assert(host_callbacks==1 && host_interrupts && display_stopping);
    assert_context_restored();
    assert(host_mdp[0x35288/4]==0x180085 && host_mdp[0x352b4/4]==0x80000001);
    assert(host_mdp[0x3525c/4]==0xf0000);
    char *manual_restore=strstr(host_log,"manual restored sync=1 auto=1");
    pixels=strstr(host_log,"source restore pixels=1");
    assert(manual_restore && pixels && manual_restore<pixels);
    puts("PASS: interrupted manual handover restores automatic GOP transfer before unmapping");

    reset_fixture();
    display_map.active=display_map.flip_active=display_map.misr_active=display_map.manual_active=true;
    display_map.sync_config=0x180085;display_map.autorefresh=0x80000001;
    host_mdp[0x35288/4]=0x180085;host_mdp[0x352b4/4]=1;
    host_pending=return_after_complete_flip;
    assert(!display_flip_buffer(1));
    assert_context_restored();
    assert(host_mdp[0x1501c/4]==1 && host_mdp[0x352b4/4]==0x80000001);
    assert(!display_manual_begin());
    puts("PASS: interrupted manual kickoff restores source and automatic refresh with complete journals");

    reset_fixture();
    display_map.flip_active=true;
    host_mdp[0x35288/4]=0x180085;host_mdp[0x352b4/4]=0x80000001;
    host_mdp[0x3525c/4]=0xf0001; /* PP1 must not use the PP0 completion bit */
    assert(!display_manual_begin());
    assert(!display_map.manual_active && host_interrupts);
    assert(host_mdp[0x35288/4]==0x180085 && host_mdp[0x352b4/4]==0x80000001);
    assert(host_mdp[0x3525c/4]==0xf0001);
    puts("PASS: non-master pingpong is refused without altering the display configuration");

    reset_fixture();
    display_map.active=display_map.flip_active=display_map.misr_active=display_map.manual_active=true;
    display_map.sync_config=0x180085;display_map.autorefresh=0x80000001;
    host_mdp[0x35288/4]=0x180085;host_mdp[0x352b4/4]=1;
    for (unsigned n=0;n<2;n++) for (unsigned r=0;r<DISPLAY_SCALE_REGS;r++)
        host_mdp[(0x4000+n*0x2000+display_scale_offsets[r])/4]=0x40000000u+n*256+r;
    host_pending=return_after_complete_scaler_setup;
    display_map.intf_misr_active=true; display_map.intf_misr=0;
    host_mdp[0x35180/4]=0x81000101;
    assert(!display_scale_configure(DISPLAY_SCALE_PITCH));
    assert(host_callbacks==1 && host_interrupts && display_stopping);
    assert_context_restored();
    for (unsigned n=0;n<2;n++) for (unsigned r=0;r<DISPLAY_SCALE_REGS;r++)
        assert(host_mdp[(0x4000+n*0x2000+display_scale_offsets[r])/4]==0x40000000u+n*256+r);
    char *geometry=strstr(host_log,"scaler restored registers=1 count=50 before_gop=1");
    pixels=strstr(host_log,"source restore pixels=1");
    unmap=strstr(host_log,"mapping restore registers=1");
    assert(geometry && pixels && unmap && geometry<pixels && pixels<unmap);
    assert(host_mdp[0x35180/4]==0);
    assert(strstr(host_log,"interface MISR restored=1 status=0 prior_status=0"));
    puts("PASS: interrupted scaler setup restores all 50 saved registers before GOP output and unmapping");
    reset_fixture();
    assert(display_scale_configure(4608));
    assert(host_mdp[0x4024/4]==4608 && host_mdp[0x6024/4]==4608);
    reset_fixture();
    assert(!display_scale_configure(4500) && !display_map.scale_active);
    puts("PASS: desktop pitch 4608 is configured; unproved pitches cannot alter geometry");

    reset_fixture();
    display_map.active=display_map.flip_active=display_map.direct_active=true;
    display_map.restore_proven=false;host_wrong_pixels=true;
    assert(lemonade_display_direct_fallback()==-1);
    assert(!display_map.restore_proven && !display_map.active);
    puts("PASS: unproven output restoration refuses producer release");

    reset_fixture();
    display_map.misr_active=true;display_map.restore_proven=true;
    host_mdp[0x44310/4]=host_mdp[0x45310/4]=0x80000301;
    assert(lemonade_display_direct_fallback()==0);
    assert(host_mdp[0x44310/4]==0 && host_mdp[0x45310/4]==0 && !display_map.misr_active);
    puts("PASS: initialization failure before translation restores its measurement controls");

    reset_fixture();
    static uint64_t tables[DISPLAY_TABLE_BYTES/8] __attribute__((aligned(16384)));
    memset(tables,0,sizeof(tables)); host_tables=(uintptr_t)tables;
    display_map.tables=host_tables; display_map.table_pages=1;
    uint64_t *leaf=display_leaf(DISPLAY_BUFFER_IOVA(0),true);assert(leaf);
    *leaf=0x923454c3; direct_pages=1;
    host_mdp[0x352b4/4]=0x80000001;
    assert(!display_direct_unmap() && *leaf && direct_pages==1);
    host_mdp[0x352b4/4]=1;
    assert(display_direct_unmap() && !*leaf && direct_pages==0);
    *leaf=0x923454c3;direct_pages=1;
    *(uint32_t *)(display_map.cb+0x7f4)=1;
    assert(!display_direct_unmap() && direct_pages==1);
    puts("PASS: page retirement requires automatic transfers disabled and completed TLB invalidation");

    reset_fixture();
    direct_requested=true;
    assert(lemonade_display_direct_present((void *)4096,32,4608,1,1)==0);
    assert(!display_map.active && !display_map.flip_active && !direct_count);
    lemonade_display_quiesce();
    assert(lemonade_display_direct_fallback()==-1);
    puts("PASS: undersized surfaces never reach DMA and stopped presenters cannot release an unproven read");
    return 0;
}
'''


def main():
    compiler = shutil.which('clang') or shutil.which('cc')
    if not compiler:
        raise SystemExit('A native C compiler is required')
    source = (ROOT / 'xnu/files/bsd/dev/lemonade_display.c').read_text()
    header = (ROOT / 'xnu/files/bsd/sys/lemonade_display.h').read_text()
    source = re.sub(r'^#include [^\n]+\n', '', source, flags=re.M)
    header = re.sub(r'^#include [^\n]+\n', '', header, flags=re.M)
    source = re.sub(r'__asm__ volatile\([^;]+;', '(void)0;', source)
    with tempfile.TemporaryDirectory(prefix='lemonade-display-stop-') as directory:
        folder = Path(directory)
        (folder / 'test.c').write_text(STUBS + header + source + CASES)
        subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wno-format',
                        '-fsanitize=address,undefined', str(folder / 'test.c'),
                        '-o', str(folder / 'test')], check=True)
        subprocess.run([str(folder / 'test')], check=True)


if __name__ == '__main__':
    main()
