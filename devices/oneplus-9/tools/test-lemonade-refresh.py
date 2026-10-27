#!/usr/bin/env python3
"""Compile the real refresh driver with a W1C DSI/FIFO model and return injection.

This validates command ordering and rollback, not physical panel compatibility.
"""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('stop_tests', Path(__file__).with_name('test-lemonade-display-stop.py'))
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)

EXTRA = r'''
static uint32_t host_dsi[1024], host_commands[32], host_command_count;
static unsigned host_fail_command, host_panel_guard;
static void host_write(uintptr_t reg, uint32_t value) {
    if (reg >= (uintptr_t)host_dsi && reg < (uintptr_t)host_dsi + sizeof(host_dsi)) {
        unsigned off = reg - (uintptr_t)host_dsi;
        if (off == 0x110) {
            host_dsi[off/4] = (host_dsi[off/4] & ~(value & 0x55555555)) | (value & 0xaaaaaaaa);
            return;
        }
        if (off == 0x90 && value == 1) {
            assert(host_command_count < 32);
            host_commands[host_command_count++] = host_dsi[0x17c/4];
            assert(host_dsi[0x4c/4] == 8);
            assert((host_dsi[0x3c/4] & 0xd4000000) == 0x10000000);
            if (host_command_count != host_fail_command) host_dsi[0x110/4] |= 1;
        }
    }
    *(uint32_t *)reg = value;
}
'''

CASES = r'''
static void fixture(void) {
    memset(&display_map, 0, sizeof(display_map)); memset(&refresh, 0, sizeof(refresh));
    memset(&irq, 0, sizeof(irq)); memset(votes, 0, sizeof(votes));
    memset(host_mdp, 0, sizeof(host_mdp)); memset(host_dsi, 0, sizeof(host_dsi));
    memset(host_log, 0, sizeof(host_log)); host_log_used=0;
    host_interrupts=true; host_pending=NULL; host_callbacks=0; host_now=0;
    host_command_count=host_fail_command=0; host_panel_guard=655;
    display_stopping=display_restoring=false; display_hz=60;
    display_map.mdp=(uintptr_t)host_mdp; display_map.manual_active=true;
    host_mdp[0x35288/4]=0x180085; host_mdp[0x352b4/4]=1;
    host_dsi[0]=0x20050000; host_dsi[1]=0x1f5;
    host_dsi[0x58/4]=0x21d0039; host_dsi[0x5c/4]=0x96000b4; host_dsi[0x2a8/4]=0x3941;
    host_dsi[0xb4/4]=0x88888;
    host_dsi[0x3c/4]=0x14000000; host_dsi[0x4c/4]=128; host_dsi[0x84/4]=0x104;
    refresh.dsi=(uintptr_t)host_dsi;
    for (unsigned i=0;i<4;i++) refresh.saved[i]=host_dsi[refresh_offsets[i]/4];
}
static void return_during_switch(void) {
    assert(refresh.owned && display_hz==120 && host_command_count==3);
    lemonade_display_quiesce();
}
int main(void) {
    fixture();
    assert(display_panel_rate(120));
    assert(host_command_count==3 && host_commands[0]==0xff5a5af0 &&
           host_commands[1]==0xffff1060 && host_commands[2]==0xffa5a5f0);
    for(unsigned i=0;i<4;i++) assert(host_dsi[refresh_offsets[i]/4]==refresh.saved[i]);
    puts("PASS: exact HS long-write sequence, FIFO padding and host register restoration");

    fixture();host_fail_command=1;
    assert(!display_panel_rate(120));
    assert(host_command_count==2 && host_commands[1]==0xffa5a5f0);
    assert(host_now>=20000000 && host_now<21000000);
    puts("PASS: lost completion is bounded and still relocks the panel");

    fixture();host_panel_guard=0;
    assert(!display_refresh_begin() && !host_command_count && !refresh.owned);
    fixture();host_dsi[0x110/4]=2;
    assert(!display_refresh_begin() && !host_command_count && !refresh.owned);
    fixture();host_dsi[0x68/4]=1;
    assert(!display_refresh_begin() && !host_command_count && !refresh.owned);
    puts("PASS: wrong panel, owned interrupts and existing errors reject before panel writes");

    fixture();host_pending=return_during_switch;
    assert(!display_refresh_begin());
    assert(host_callbacks==1 && display_stopping && !refresh.owned && display_hz==60);
    assert(host_command_count==6 && host_commands[4]==0xffff0060 && host_commands[5]==0xffa5a5f0);
    assert(host_mdp[0x35288/4]==0x180085);
    for(unsigned i=0;i<4;i++) assert(host_dsi[refresh_offsets[i]/4]==refresh.saved[i]);
    unsigned commands=host_command_count;
    display_refresh_restore();assert(host_command_count==commands);
    puts("PASS: pending return sees the complete journal and restores 60 Hz before resumption");

    fixture();display_hz=120;irq.streaming=true;
    struct lemonade_present_window window;
    assert(lemonade_display_present_begin(&window)==-1);
    assert(!host_command_count);
    puts("PASS: 120 Hz cannot use the 60-Hz CPU-copy window");
    return 0;
}
'''

def main():
    stubs = stop.STUBS.replace('static bool PE_parse_boot_argn(const char *n, void *p, int s) { return false; }',
        'static bool PE_parse_boot_argn(const char *n, void *p, int s) { *(uint32_t *)p=host_panel_guard; return true; }')
    stubs = stubs.replace('vm_offset_t ml_io_map(vm_offset_t p, vm_size_t n) { return 0; }',
        'vm_offset_t ml_io_map(vm_offset_t p, vm_size_t n) { return p==0x0ae94000 ? (uintptr_t)host_dsi : 0; }')
    stubs = stubs.replace('static int SecureDTLookupEntry', EXTRA + '\nstatic int SecureDTLookupEntry')
    source = (ROOT / 'xnu/files/bsd/dev/lemonade_display.c').read_text()
    header = (ROOT / 'xnu/files/bsd/sys/lemonade_display.h').read_text()
    source = source.replace('*(volatile uint32_t *)reg = value;', 'host_write(reg, value);')
    source = re.sub(r'__asm__ volatile\([^;]+;', '(void)0;', source)
    code = stubs + re.sub(r'^#include [^\n]+\n', '', header + source, flags=re.M) + CASES
    with tempfile.TemporaryDirectory(prefix='lemonade-refresh-') as directory:
        folder = Path(directory); (folder / 'test.c').write_text(code)
        subprocess.run([shutil.which('clang') or 'cc', '-std=gnu11', '-O1', '-g', '-Wno-format',
                        '-fsanitize=address,undefined', str(folder / 'test.c'), '-o', str(folder / 'test')], check=True)
        subprocess.run([str(folder / 'test')], check=True)

if __name__ == '__main__':
    main()
