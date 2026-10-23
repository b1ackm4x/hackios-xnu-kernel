/*
 * OnePlus 9 / SM8350 display survey. lemonade_display=1 is read-only; =2 may
 * temporarily vote for two register-bus clocks. Both votes are restored, including
 * on a panic return. =3 additionally measures a bounded, exclusively acquired TE
 * interrupt and restores its GIC configuration. No DSI, PLL, timing, layer or
 * framebuffer-address writes.
 *
 * Register definitions checked against Linux v6.12: dispcc-sm8250.c (SM8350
 * adjustments), gcc-sm8350.c, sm8350.dtsi, dpu_7_0_sm8350.h and dpu_hw_intf.c.
 * SM8350 DISPCC is not SC7280: AHB=0x207c, MDP=0x200c, GDSC=0x3000.
 */
#include <kern/clock.h>
#include <libkern/libkern.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <sys/systm.h>
#include <sys/redwood_input.h>
#include <vm/vm_kern.h>
#include <os/atomic_private.h>
#include <kern/simple_lock.h>
#include <sys/lemonade_display.h>

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern void loader_log_putc(char);
extern vm_offset_t pe_arm_get_soc_base_phys(void);
extern kern_return_t gic_v3_spi_attach(uint32_t, bool, void (*)(void *, uint32_t), void *);
extern void gic_v3_spi_mask(uint32_t);
extern void gic_v3_spi_detach(uint32_t);

#define GCC_BASE       0x00100000UL
#define GCC_DISP_AHB   0x27004
#define DISPCC_BASE    0x0af00000UL
#define DISPCC_AHB     0x207c
#define DISPCC_GDSC    0x3000
#define MDSS_BASE      0x0ae00000UL
#define MDP_BASE       0x0ae01000UL
#define INTF1_OFFSET  0x35000
#define CLOCK_OFF      (1u << 31)
#define DISPLAY_INTID  115u       /* SM8350 DT: GIC_SPI 83 */
#define TEAR_ENABLE    0x35800
#define TEAR_STATUS    0x35804
#define TEAR_CLEAR     0x35808
#define TEAR_READ_PTR  (1u << 2)

static struct {
	vm_offset_t mdp, mdss, gic;
	uint32_t enable, group, groupmod, config;
	uint8_t priority;
	uint64_t route;
	bool acquired;
	bool streaming;
	uint32_t count, unexpected;
	uint64_t last_tick, period;
	struct { uint64_t ns; uint32_t frame, line, tear, status; } samples[64];
} irq;
static SIMPLE_LOCK_DECLARE(vsync_lock, 0);
static thread_call_t vsync_clients[16];
static uint32_t present_count, present_late, present_dropped;

static struct display_vote {
	vm_offset_t reg;
	uint32_t original;
	bool changed;
} votes[2];

static void tell(const char *, ...) __printflike(1, 2);
static void
tell(const char *format, ...)
{
	char line[320];
	va_list list;
	va_start(list, format);
	vsnprintf(line, sizeof(line), format, list);
	va_end(list);
	for (const char *c = line; *c; c++) loader_log_putc(*c);
	if (ml_get_interrupts_enabled()) printf("%s", line);
}

static uint32_t
rd(vm_offset_t base, uint32_t offset)
{
	return *(volatile uint32_t *)(base + offset);
}

static void
wr(vm_offset_t reg, uint32_t value)
{
	*(volatile uint32_t *)reg = value;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
restore_bits(vm_offset_t reg, uint32_t mask, uint32_t saved)
{
	wr(reg, (rd(reg, 0) & ~mask) | (saved & mask));
}

static void
irq_stop(bool detach)
{
	if (!irq.acquired) return;
	tell("lemonade-display: final irq_count=%u unexpected=%#x presents=%u late=%u dropped=%u\n",
	    irq.count, irq.unexpected, present_count, present_late, present_dropped);
	os_atomic_store(&irq.streaming, false, release);
	const uint32_t bit = 1u << (DISPLAY_INTID % 32);
	/* Disable the source before the GIC line. Detach waits only in ordinary
	 * thread context: a panic may have parked the CPU of an in-flight handler. */
	wr(irq.mdp + TEAR_ENABLE, irq.enable);
	gic_v3_spi_mask(DISPLAY_INTID);
	if (detach) gic_v3_spi_detach(DISPLAY_INTID);
	wr(irq.mdp + TEAR_CLEAR, TEAR_READ_PTR);
	wr(irq.gic + 0x280 + 4 * (DISPLAY_INTID / 32), bit);
	restore_bits(irq.gic + 0x80 + 4 * (DISPLAY_INTID / 32), bit, irq.group);
	restore_bits(irq.gic + 0xd00 + 4 * (DISPLAY_INTID / 32), bit, irq.groupmod);
	restore_bits(irq.gic + 0xc00 + 4 * (DISPLAY_INTID / 16), 3u << (2 * (DISPLAY_INTID % 16)), irq.config);
	*(volatile uint8_t *)(irq.gic + 0x400 + DISPLAY_INTID) = irq.priority;
	*(volatile uint64_t *)(irq.gic + 0x6000 + 8 * DISPLAY_INTID) = irq.route;
	__asm__ volatile("dsb sy" ::: "memory");
	tell("lemonade-display: irq restored tear_enable=%#x gic_enable=%u route=%#llx\n",
	    rd(irq.mdp, TEAR_ENABLE), (rd(irq.gic, 0x100 + 4 * (DISPLAY_INTID / 32)) & bit) != 0,
	    *(volatile uint64_t *)(irq.gic + 0x6000 + 8 * DISPLAY_INTID));
	irq.acquired = false;
}

static void
display_interrupt(void *context, uint32_t intid)
{
	(void)context;
	uint32_t status = rd(irq.mdp, TEAR_STATUS);
	uint32_t n = os_atomic_load(&irq.count, relaxed);
	if (!(status & TEAR_READ_PTR) || (rd(irq.mdss, 0x10) & ~1u) || (!irq.streaming && n >= 64)) {
		os_atomic_store(&irq.unexpected, status | 0x80000000u, release);
		os_atomic_store(&irq.streaming, false, release);
		gic_v3_spi_mask(intid);
		return;
	}
	uint64_t tick = mach_absolute_time();
	if (n < 64) {
		absolutetime_to_nanoseconds(tick, &irq.samples[n].ns);
		irq.samples[n].frame = rd(irq.mdp + INTF1_OFFSET, 0xac);
		irq.samples[n].line = rd(irq.mdp + INTF1_OFFSET, 0xb0);
		irq.samples[n].tear = rd(irq.mdp + INTF1_OFFSET, 0x2b0);
		irq.samples[n].status = status;
	}
	wr(irq.mdp + TEAR_CLEAR, TEAR_READ_PTR);
	simple_lock(&vsync_lock, LCK_GRP_NULL);
	if (irq.last_tick) {
		uint64_t elapsed = tick - irq.last_tick, ns;
		absolutetime_to_nanoseconds(elapsed, &ns);
		if (ns >= 12 * NSEC_PER_MSEC && ns <= 20 * NSEC_PER_MSEC)
			irq.period = irq.period ? (irq.period * 7 + elapsed) / 8 : elapsed;
		else if (ns < NSEC_PER_MSEC) {
			os_atomic_store(&irq.unexpected, 0x40000000, release);
			os_atomic_store(&irq.streaming, false, release);
			gic_v3_spi_mask(intid);
		}
	}
	irq.last_tick = tick;
	os_atomic_store(&irq.count, n + 1, release);
	if (irq.streaming) for (unsigned i = 0; i < 16; i++)
		if (vsync_clients[i]) thread_call_enter(vsync_clients[i]);
	simple_unlock(&vsync_lock);
}

static void
irq_probe(vm_offset_t mdp, vm_offset_t mdss, bool streaming)
{
	DTEntry entry;
	const void *property;
	unsigned length;
	if (SecureDTLookupEntry(NULL, "/arm-io/gic", &entry) != kSuccess ||
	    SecureDTGetProperty(entry, "reg", &property, &length) != kSuccess || length < 32) return;
	const uint64_t *reg = property;
	if (reg[1] < 0x6400) return;
	irq.gic = ml_io_map(pe_arm_get_soc_base_phys() + reg[0], reg[1]);
	if (!irq.gic) return;
	irq.mdp = mdp; irq.mdss = mdss;
	irq.enable = rd(mdp, TEAR_ENABLE);
	const uint32_t bit = 1u << (DISPLAY_INTID % 32), word = DISPLAY_INTID / 32;
	uint32_t enabled = rd(irq.gic, 0x100 + 4 * word), pending = rd(irq.gic, 0x200 + 4 * word);
	uint32_t active = rd(irq.gic, 0x300 + 4 * word);
	tell("lemonade-display: irq preflight intid=%u tear_enable=%#x status=%#x mdss=%#x enabled=%u pending=%u active=%u\n",
	    DISPLAY_INTID, irq.enable, rd(mdp, TEAR_STATUS), rd(mdss, 0x10), !!(enabled & bit), !!(pending & bit), !!(active & bit));
	if (irq.enable || ((enabled | pending | active) & bit) || rd(mdss, 0x10)) {
		tell("lemonade-display: irq refused: source or shared line already in use\n"); return;
	}
	irq.group = rd(irq.gic, 0x80 + 4 * word);
	irq.groupmod = rd(irq.gic, 0xd00 + 4 * word);
	irq.config = rd(irq.gic, 0xc00 + 4 * (DISPLAY_INTID / 16));
	irq.priority = *(volatile uint8_t *)(irq.gic + 0x400 + DISPLAY_INTID);
	irq.route = *(volatile uint64_t *)(irq.gic + 0x6000 + 8 * DISPLAY_INTID);
	irq.streaming = streaming;
	wr(mdp + TEAR_CLEAR, TEAR_READ_PTR);
	if (gic_v3_spi_attach(DISPLAY_INTID, false, display_interrupt, NULL) != KERN_SUCCESS) {
		tell("lemonade-display: irq attach refused\n"); return;
	}
	irq.acquired = true;
	wr(mdp + TEAR_ENABLE, TEAR_READ_PTR);
	uint64_t begin, now;
	absolutetime_to_nanoseconds(mach_absolute_time(), &begin);
	do {
		delay(1000);
		absolutetime_to_nanoseconds(mach_absolute_time(), &now);
	} while (os_atomic_load(&irq.count, acquire) < 32 && !os_atomic_load(&irq.unexpected, acquire) && now - begin < NSEC_PER_SEC);
	if (!streaming || irq.unexpected || irq.count < 32) irq_stop(true);
	unsigned n = os_atomic_load(&irq.count, acquire);
	for (unsigned i = 0; i < n && i < 64; i++)
		tell("lemonade-display: irq edge %u us=%llu frame=%u line=%u tear=%u status=%#x\n",
		    i, irq.samples[i].ns / 1000, irq.samples[i].frame, irq.samples[i].line, irq.samples[i].tear, irq.samples[i].status);
	tell("lemonade-display: irq summary count=%u unexpected=%#x\n", n, irq.unexpected);
	if (irq.acquired && irq.streaming)
		tell("lemonade-display: hardware vsync active; callbacks follow TE; present checks the idle scan window\n");
}

bool
lemonade_display_vsync_register(thread_call_t call)
{
	uint64_t tick, period, count;
	if (!call || !lemonade_display_vsync_snapshot(&tick, &period, &count)) return false;
	bool enabled = ml_set_interrupts_enabled(false), found = false;
	simple_lock(&vsync_lock, LCK_GRP_NULL);
	for (unsigned i = 0; i < 16; i++) if (vsync_clients[i] == call) found = true;
	if (!found) for (unsigned i = 0; i < 16; i++) if (!vsync_clients[i]) {
		vsync_clients[i] = call; found = true; break;
	}
	simple_unlock(&vsync_lock);
	ml_set_interrupts_enabled(enabled);
	return found;
}

void
lemonade_display_vsync_unregister(thread_call_t call)
{
	if (!call) return;
	bool enabled = ml_set_interrupts_enabled(false);
	simple_lock(&vsync_lock, LCK_GRP_NULL);
	for (unsigned i = 0; i < 16; i++) if (vsync_clients[i] == call) vsync_clients[i] = NULL;
	simple_unlock(&vsync_lock);
	ml_set_interrupts_enabled(enabled);
}

bool
lemonade_display_vsync_snapshot(uint64_t *tick, uint64_t *period, uint64_t *count)
{
	if (!os_atomic_load(&irq.streaming, acquire) || !irq.acquired) return false;
	bool enabled = ml_set_interrupts_enabled(false);
	simple_lock(&vsync_lock, LCK_GRP_NULL);
	*tick = irq.last_tick; *period = irq.period; *count = irq.count;
	simple_unlock(&vsync_lock);
	ml_set_interrupts_enabled(enabled);
	return *tick && *period && mach_absolute_time() - *tick < *period * 4;
}

int
lemonade_display_present_begin(struct lemonade_present_window *window)
{
	if (!os_atomic_load(&irq.streaming, acquire)) return 0;
	uint64_t start = mach_absolute_time(), tick, period, count, now, ns;
	uint64_t earliest, latest;
	nanoseconds_to_absolutetime(6 * NSEC_PER_MSEC, &earliest);
	nanoseconds_to_absolutetime(10500 * NSEC_PER_USEC, &latest);
	do {
		if (!lemonade_display_vsync_snapshot(&tick, &period, &count)) {
			os_atomic_store(&irq.streaming, false, release);
			gic_v3_spi_mask(DISPLAY_INTID);
			tell("lemonade-display: TE source stale; hardware synchronization disabled\n");
			return 0;
		}
		now = mach_absolute_time();
		uint64_t phase = now - tick;
		vm_offset_t intf = irq.mdp + INTF1_OFFSET;
		uint32_t line = rd(intf, 0xb0), tear = rd(intf, 0x2b0);
		if (phase >= earliest && phase <= latest && line == 1 && tear >= 2400) {
			*window = (struct lemonade_present_window){now, tick, rd(intf, 0xac), line, tear};
			return 1;
		}
		uint64_t target = phase < earliest ? tick + earliest : tick + period + earliest;
		clock_delay_until(target);
		absolutetime_to_nanoseconds(mach_absolute_time() - start, &ns);
	} while (ns < 40 * NSEC_PER_MSEC);
	present_dropped++;
	if (present_dropped <= 4 || !(present_dropped % 120))
		tell("lemonade-display: idle window unavailable; dropped=%u\n", present_dropped);
	return -1;
}

void
lemonade_display_present_end(const struct lemonade_present_window *window, uint32_t swap)
{
	__asm__ volatile("dsb sy" ::: "memory");
	vm_offset_t intf = irq.mdp + INTF1_OFFSET;
	uint64_t ns, phase;
	uint32_t frame = rd(intf, 0xac), line = rd(intf, 0xb0), tear = rd(intf, 0x2b0);
	absolutetime_to_nanoseconds(mach_absolute_time() - window->began, &ns);
	absolutetime_to_nanoseconds(mach_absolute_time() - window->te, &phase);
	bool safe = frame == window->frame && line == 1 && tear >= 2400 && phase < 15 * NSEC_PER_MSEC;
	present_count++; present_late += !safe;
	if (!safe || present_count <= 4 || !(present_count % 120))
		tell("lemonade-display: present swap=%u count=%u frame=%u/%u line=%u tear=%u copy_us=%llu phase_us=%llu late=%u dropped=%u\n",
		    swap, present_count, window->frame, frame, line, tear, ns / 1000, phase / 1000, present_late, present_dropped);
}

void
lemonade_display_quiesce(void)
{
	irq_stop(false);
	for (int i = 1; i >= 0; i--) {
		struct display_vote *v = &votes[i];
		if (!v->changed) continue;
		/* Only our enable vote changes. Hardware owns status and clock gating. */
		wr(v->reg, (rd(v->reg, 0) & ~1u) | (v->original & 1));
		uint32_t now = rd(v->reg, 0);
		tell("lemonade-display: bus vote %d restored enable=%u expected=%u\n",
		    i, now & 1, v->original & 1);
		v->changed = false;
	}
}

static bool
bus_clock(unsigned index, vm_offset_t reg, uint32_t mode, bool hardware_gated)
{
	struct display_vote *v = &votes[index];
	v->reg = reg;
	v->original = rd(reg, 0);
	tell("lemonade-display: bus %u original=%#x mode=%u\n", index, v->original, mode);
	if (!(v->original & 1) && mode >= 2) {
		/* Record before writing so the panic-return path can undo it too. */
		v->changed = true;
		wr(reg, v->original | 1);
		delay(1000);
	}
	uint32_t now = rd(reg, 0);
	if (!(now & 1) || (!hardware_gated && (now & CLOCK_OFF))) {
		tell("lemonade-display: bus %u unavailable (%#x); downstream not read\n", index, now);
		return false;
	}
	return true;
}

static uint32_t
report(vm_offset_t base, const char *block, const char *name, uint32_t offset)
{
	uint32_t value = rd(base, offset);
	tell("lemonade-display: %s.%s[%#x]=%#x\n", block, name, offset, value);
	return value;
}

void
lemonade_display_init(void)
{
	uint32_t mode = 0;
	DTEntry entry;
	const void *value;
	unsigned length;
	if (!PE_parse_boot_argn("lemonade_display", &mode, sizeof(mode)) || !mode) return;
	if (mode > 4 || SecureDTLookupEntry(NULL, "/arm-io", &entry) != kSuccess ||
	    SecureDTGetProperty(entry, "device_type", &value, &length) != kSuccess ||
	    length != sizeof("lemonade") || memcmp(value, "lemonade", length)) {
		tell("lemonade-display: refused: unsupported platform or mode\n");
		return;
	}
	tell("lemonade-display: SM8350 survey begin; mode=%u; panel programming unchanged\n", mode);
	vm_offset_t gcc = ml_io_map(GCC_BASE, 0x28000);
	if (!gcc || !bus_clock(0, gcc + GCC_DISP_AHB, mode, true)) goto out;
	vm_offset_t cc = ml_io_map(DISPCC_BASE, 0x4000);
	if (!cc) goto out;
	uint32_t power = report(cc, "dispcc", "gdsc", DISPCC_GDSC);
	report(cc, "dispcc", "mdp", 0x200c);
	report(cc, "dispcc", "pclk0", 0x2004);
	if (!(power & (1u << 31))) {
		tell("lemonade-display: display power domain is off; downstream not read\n");
		goto out;
	}
	if (!bus_clock(1, cc + DISPCC_AHB, mode, false)) goto out;
	vm_offset_t mdss = ml_io_map(MDSS_BASE, 0x1000);
	vm_offset_t mdp = ml_io_map(MDP_BASE, 0x8f000);
	if (!mdss || !mdp) goto out;
	uint32_t revision = report(mdss, "mdss", "version", 0);
	if ((revision >> 28) != 7) {
		tell("lemonade-display: unexpected DPU revision; downstream not read\n");
		goto out;
	}
	report(mdp, "mdp", "interface_select", 0x4);
	static const uint32_t ctl_regs[] = { 0, 4, 0x14, 0x18, 0x40, 0x44, 0xe8, 0xf4, 0xfc };
	for (unsigned n = 0; n < 2; n++) {
		char block[16];
		snprintf(block, sizeof(block), "ctl%u", n);
		for (unsigned i = 0; i < sizeof(ctl_regs) / sizeof(ctl_regs[0]); i++)
			report(mdp + 0x15000 + n * 0x1000, block, "state", ctl_regs[i]);
		snprintf(block, sizeof(block), "lm%u", n);
		report(mdp + 0x44000 + n * 0x1000, block, "mode", 0);
		report(mdp + 0x44000 + n * 0x1000, block, "size", 4);
	}
	static const uint32_t pipes[] = { 0x4000, 0x6000, 0x8000, 0xa000, 0x24000, 0x26000 };
	static const uint32_t pipe_regs[] = { 0, 8, 0xc, 0x10, 0x14, 0x24, 0x30, 0x34, 0x38 };
	for (unsigned n = 0; n < sizeof(pipes) / sizeof(pipes[0]); n++) {
		char block[16];
		snprintf(block, sizeof(block), "sspp%u", n);
		for (unsigned i = 0; i < sizeof(pipe_regs) / sizeof(pipe_regs[0]); i++)
			report(mdp + pipes[n], block, "state", pipe_regs[i]);
	}
	vm_offset_t intf = mdp + INTF1_OFFSET;
	static const uint32_t intf_regs[] = { 0, 4, 8, 0xc, 0x1c, 0x24, 0x3c, 0x60,
	    0x84, 0xa8, 0x26c, 0x280, 0x284, 0x288, 0x28c, 0x294, 0x29c, 0x2a0, 0x2b4 };
	for (unsigned i = 0; i < sizeof(intf_regs) / sizeof(intf_regs[0]); i++)
		report(intf, "intf1", "state", intf_regs[i]);
	for (unsigned i = 0; i < 11; i++) {
		uint64_t ns;
		absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
		tell("lemonade-display: sample %u us=%llu frame=%u line=%u tear=%u out=%u\n",
		    i, ns / 1000, rd(intf, 0xac), rd(intf, 0xb0), rd(intf, 0x2b0), rd(intf, 0x2ac));
		if (i < 10) delay(100000);
	}
	/* Capture before logging: console output must not distort frame-edge timing.
	 * This bounded survey measures the hardware counter; it drives no IRQ or timer. */
	uint64_t edge_time[32], started, now;
	uint32_t edge_count[32], edge_line[32], edge_tear[32], edges = 0, previous = rd(intf, 0xac);
	absolutetime_to_nanoseconds(mach_absolute_time(), &started);
	do {
		uint32_t frame = rd(intf, 0xac);
		absolutetime_to_nanoseconds(mach_absolute_time(), &now);
		if (frame != previous) {
			edge_time[edges] = now / 1000;
			edge_line[edges] = rd(intf, 0xb0);
			edge_tear[edges] = rd(intf, 0x2b0);
			edge_count[edges++] = frame;
			previous = frame;
		}
		if (edges < 32) delay(100);
	} while (edges < 32 && now - started < NSEC_PER_SEC);
	for (unsigned i = 0; i < edges; i++)
		tell("lemonade-display: edge %u us=%llu frame=%u line=%u tear=%u\n",
		    i, edge_time[i], edge_count[i], edge_line[i], edge_tear[i]);
	static struct { uint64_t us; uint32_t frame, line, tear; } trace[256];
	for (unsigned i = 0; i < 256; i++) {
		absolutetime_to_nanoseconds(mach_absolute_time(), &now);
		trace[i].us = now / 1000;
		trace[i].frame = rd(intf, 0xac);
		trace[i].line = rd(intf, 0xb0);
		trace[i].tear = rd(intf, 0x2b0);
		delay(100);
	}
	for (unsigned i = 0; i < 256; i++)
		tell("lemonade-display: trace %u us=%llu frame=%u line=%u tear=%u\n",
		    i, trace[i].us, trace[i].frame, trace[i].line, trace[i].tear);
	if (mode >= 3) irq_probe(mdp, mdss, mode == 4);
out:
	if (!irq.streaming || !irq.acquired) lemonade_display_quiesce();
	tell("lemonade-display: survey end\n");
}
