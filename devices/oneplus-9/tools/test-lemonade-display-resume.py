#!/usr/bin/env python3
"""Exercise the real direct-resume decision with controlled hardware results."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('fences', Path(__file__).with_name('test-lemonade-display-fences.py'))
fences = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fences)
source = (ROOT/'xnu/files/bsd/dev/lemonade_display.c').read_text()
code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
static struct { bool direct_active; } display_map;
static bool desktop_refresh_requested, display_stopping, acquire_ok, refresh_ok, stop_during;
static uint32_t display_hz, acquisitions, switches, notifications;
#define os_atomic_load(p, order) (*(p))
static bool display_direct_begin(uint32_t pitch) {
    assert(pitch == 4544); ++acquisitions;
    display_map.direct_active=acquire_ok; return acquire_ok;
}
static bool display_refresh_begin() {
    ++switches;
    if (refresh_ok) display_hz=120;
    if (stop_during) display_stopping=true;
    return refresh_ok && !display_stopping;
}
static void MiniIOMobileFramebufferRefreshChanged() { ++notifications; assert(display_hz==120); }
static void tell(const char *, ...) {}
''' + fences.method(source, 'display_direct_resume') + r'''
static void reset() {
    display_map.direct_active=false; desktop_refresh_requested=true; display_stopping=false;
    acquire_ok=refresh_ok=true; stop_during=false; display_hz=60;
    acquisitions=switches=notifications=0;
}
int main() {
    reset(); assert(display_direct_resume(4544));
    assert(acquisitions==1 && switches==1 && notifications==1 && display_hz==120);
    assert(display_direct_resume(4544) && acquisitions==1 && switches==1);
    puts("PASS: compatibility return reacquires and measures 120 Hz exactly once");
    reset(); desktop_refresh_requested=false; assert(display_direct_resume(4544));
    assert(display_hz==60 && switches==0 && notifications==0);
    puts("PASS: explicit 60 Hz remains unchanged");
    reset(); acquire_ok=false; assert(!display_direct_resume(4544));
    assert(switches==0 && notifications==0);
    reset(); refresh_ok=false; assert(!display_direct_resume(4544));
    assert(display_hz==60 && notifications==0);
    reset(); stop_during=true; assert(!display_direct_resume(4544));
    assert(notifications==0);
    puts("PASS: mapping, panel and stop failures never publish recovery");
}
'''
with tempfile.TemporaryDirectory(prefix='lemonade-display-resume-') as temp:
    p=Path(temp); (p/'test.cpp').write_text(code)
    subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
