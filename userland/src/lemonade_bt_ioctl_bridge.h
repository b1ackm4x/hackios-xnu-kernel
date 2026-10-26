/* Darwin arm64 passes variadic arguments on the stack, unlike named x2.
 * Forward the raw optional slot without asking C va_arg to fetch a missing
 * argument. ioctl has at most one optional machine-word argument; the real
 * implementation determines whether the request uses it. Never log this slot.
 * The including file provides trace_ioctl_impl(fd, request, argument).
 */
#if !defined(__APPLE__) || !defined(__arm64__)
#error "The Bluetooth ioctl observation bridge requires Darwin arm64"
#endif
__attribute__((naked, noinline)) static int trace_ioctl(
    int fd __attribute__((unused)), unsigned long request __attribute__((unused)), ...)
{
    __asm__ volatile("ldr x2, [sp]\n\tb _trace_ioctl_impl");
}
