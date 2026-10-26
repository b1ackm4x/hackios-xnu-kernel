/* Native Apple Silicon execution of the exact Darwin arm64 variadic bridge. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <errno.h>
static uintptr_t received;
static int original(int fd, unsigned long request, ...) {
    assert(fd == 17);
    if (request == 1) {
        va_list args; va_start(args, request); received = (uintptr_t)va_arg(args, void *); va_end(args);
    }
    errno = EIO; return -1;
}
__attribute__((used, noinline)) static int trace_ioctl_impl(int fd, unsigned long request, void *argument) {
    return original(fd, request, argument);
}
#include "../userland/src/lemonade_bt_ioctl_bridge.h"
int main(void) {
    volatile uintptr_t marker = UINT64_C(0x123456789abcdef0);
    assert(trace_ioctl(17, 1ul, (void *)marker) == -1);
    assert(received == marker && errno == EIO);
    assert(trace_ioctl(17, 0ul) == -1 && errno == EIO);
    int (*volatile indirect)(int, unsigned long, ...) = trace_ioctl;
    assert(indirect(17, 1ul, (void *)&marker) == -1);
    assert(received == (uintptr_t)&marker && errno == EIO);
    puts("PASS: Darwin arm64 ioctl variadic pointer, full-width argument, no-argument call and errno");
}
