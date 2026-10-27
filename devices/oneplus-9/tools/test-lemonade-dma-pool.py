#!/usr/bin/env python3
"""Exercise the actual DMA suballocator's address/overflow contract on the host."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[3]
source = (root / "wifi/lx/src/lx_base.c").read_text()
a = source.index("void *\nlinux_dma_alloc_coherent(")
b = source.index("\nvoid\nkmem_free(", a)
function = source[a:b]
prefix = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define roundup2(x, y) (((x) + ((y) - 1)) & ~((y) - 1))
#define bzero(p,n) memset(p,0,n)
typedef uint64_t dma_addr_t;
typedef int gfp_t;
struct device { int unused; };
static struct { uint8_t *memory; uint64_t address; uint32_t rings_used, rings_limit; } pool;
static int platform = 1;
static int lx_os_radio_platform(void) { return platform; }
static void lx_os_lock(void) {}
static void lx_os_unlock(void) {}
"""
suffix = r"""
int main(void) {
    pool.rings_limit = 32u << 20;
    pool.memory = malloc(pool.rings_limit);
    assert(pool.memory);
    memset(pool.memory, 0xa5, pool.rings_limit);
    pool.address = 0xa1000000;
    dma_addr_t a, b;
    void *small = linux_dma_alloc_coherent(NULL, 64, &a, 0);
    assert(small && a == pool.address && pool.rings_used == 16384);
    void *firmware = linux_dma_alloc_coherent(NULL, 5111808, &a, 0);
    assert(firmware && a % (8u << 20) == 0);
    uint64_t firmware_end = a + 5111808;
    void *calibration = linux_dma_alloc_coherent(NULL, 3735552, &b, 0);
    assert(calibration && b % (4u << 20) == 0 && b >= firmware_end);
    for (unsigned i = 0; i < 100; i++) {
        uint32_t size = 1 + i * 701;
        uint32_t order = 16384;
        while (order < size) order <<= 1;
        void *v = linux_dma_alloc_coherent(NULL, size, &a, 0);
        assert(v && a % order == 0 && a >= b);
        assert(a - pool.address + size <= pool.rings_limit);
        for (unsigned j = 0; j < size; j++) assert(((uint8_t *)v)[j] == 0);
        b = a + size;
    }
    uint32_t used = pool.rings_used;
    assert(!linux_dma_alloc_coherent(NULL, SIZE_MAX, &a, 0));
    assert(!linux_dma_alloc_coherent(NULL, 0, &a, 0));
    assert(!linux_dma_alloc_coherent(NULL, pool.rings_limit, &a, 0));
    assert(pool.rings_used == used);
    pool.address = UINT64_MAX - 1023; pool.rings_used = 0;
    assert(!linux_dma_alloc_coherent(NULL, 4096, &a, 0));
    platform = 0; pool.address = 0x98000000; pool.rings_used = 0;
    assert(linux_dma_alloc_coherent(NULL, 65, &a, 0) && pool.rings_used == 128);
    free(pool.memory);
    puts("DMA allocator: natural alignment, non-overlap, zeroing, capacity, overflow and legacy profile passed");
}
"""
with tempfile.TemporaryDirectory(prefix="lemonade-dma-test-") as tmp:
    src, binary = Path(tmp) / "test.c", Path(tmp) / "test"
    src.write_text(prefix + function + suffix)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-parameter",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                    str(src), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
