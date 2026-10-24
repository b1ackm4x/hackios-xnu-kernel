/*
 * OnePlus 9 / SM8350 display survey. lemonade_display=1 is read-only; =2 may
 * temporarily vote for two register-bus clocks. Both votes are restored, including
 * on a panic return. =3 additionally measures a bounded, exclusively acquired TE
 * interrupt and restores its GIC configuration. =4 keeps hardware vsync active;
 * =5 surveys the display's Apps SMMU stream/context without writing the SMMU.
 * =6 temporarily identity-maps only GOP in the existing disabled context and
 * compares hardware mixer signatures; its journal restores the context.
 * =7 additionally tests two owned buffers by changing VIG0/1 source addresses
 * and CTL flush, then verifies the original pixels before restoring bypass.
 * =8 uses explicit command transfers. =9 additionally compares two uniform
 * or spatial reference images through QSEED from 1125x2436 to 1080x2400. Geometry and
 * scaler registers are journaled; no DSI, PLL or panel timing is changed.
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
extern uint64_t kvtophys(vm_offset_t);

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
static void display_map_restore(void);
/* Return can interrupt the probe's thread on the same CPU. Other CPUs have
 * already been stopped by loader_return(). Keep each register transaction and
 * its journal indivisible, and reject all resumed probe accesses after return
 * starts. Only the IRQ-masked restoration transaction may bypass this gate. */
static bool display_stopping, display_restoring;

static bool
display_transaction_begin(bool *enabled)
{
	*enabled = ml_set_interrupts_enabled(false);
	if (!os_atomic_load(&display_stopping, acquire)) return true;
	ml_set_interrupts_enabled(*enabled);
	return false;
}

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
	bool enabled = ml_set_interrupts_enabled(false);
	uint32_t value = 0;
	if (!os_atomic_load(&display_stopping, acquire) || display_restoring)
		value = *(volatile uint32_t *)(base + offset);
	ml_set_interrupts_enabled(enabled);
	return value;
}

static void
wr(vm_offset_t reg, uint32_t value)
{
	bool enabled = ml_set_interrupts_enabled(false);
	if (!os_atomic_load(&display_stopping, acquire) || display_restoring) {
		*(volatile uint32_t *)reg = value;
		__asm__ volatile("dsb sy" ::: "memory");
	}
	ml_set_interrupts_enabled(enabled);
}

static void
restore_bits(vm_offset_t reg, uint32_t mask, uint32_t saved)
{
	wr(reg, (rd(reg, 0) & ~mask) | (saved & mask));
}

static void
irq_stop(bool detach)
{
	bool enabled = ml_set_interrupts_enabled(false);
	if (!irq.acquired || (display_stopping && !display_restoring)) goto out;
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
out:
	ml_set_interrupts_enabled(enabled);
}

static void
display_interrupt(void *context, uint32_t intid)
{
	(void)context;
	if (os_atomic_load(&display_stopping, acquire)) return;
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
	bool interrupts;
	if (!display_transaction_begin(&interrupts)) return;
	irq.mdp = mdp; irq.mdss = mdss;
	irq.enable = rd(mdp, TEAR_ENABLE);
	const uint32_t bit = 1u << (DISPLAY_INTID % 32), word = DISPLAY_INTID / 32;
	uint32_t enabled = rd(irq.gic, 0x100 + 4 * word), pending = rd(irq.gic, 0x200 + 4 * word);
	uint32_t active = rd(irq.gic, 0x300 + 4 * word);
	tell("lemonade-display: irq preflight intid=%u tear_enable=%#x status=%#x mdss=%#x enabled=%u pending=%u active=%u\n",
	    DISPLAY_INTID, irq.enable, rd(mdp, TEAR_STATUS), rd(mdss, 0x10), !!(enabled & bit), !!(pending & bit), !!(active & bit));
	if (irq.enable || ((enabled | pending | active) & bit) || rd(mdss, 0x10)) {
		tell("lemonade-display: irq refused: source or shared line already in use\n"); goto attach_done;
	}
	irq.group = rd(irq.gic, 0x80 + 4 * word);
	irq.groupmod = rd(irq.gic, 0xd00 + 4 * word);
	irq.config = rd(irq.gic, 0xc00 + 4 * (DISPLAY_INTID / 16));
	irq.priority = *(volatile uint8_t *)(irq.gic + 0x400 + DISPLAY_INTID);
	irq.route = *(volatile uint64_t *)(irq.gic + 0x6000 + 8 * DISPLAY_INTID);
	irq.streaming = streaming;
	wr(mdp + TEAR_CLEAR, TEAR_READ_PTR);
	if (gic_v3_spi_attach(DISPLAY_INTID, false, display_interrupt, NULL) != KERN_SUCCESS) {
		tell("lemonade-display: irq attach refused\n"); goto attach_done;
	}
	irq.acquired = true;
	wr(mdp + TEAR_ENABLE, TEAR_READ_PTR);
attach_done:
	ml_set_interrupts_enabled(interrupts);
	if (!irq.acquired || os_atomic_load(&display_stopping, acquire)) return;
	uint64_t begin, now;
	absolutetime_to_nanoseconds(mach_absolute_time(), &begin);
	do {
		if (os_atomic_load(&display_stopping, acquire)) return;
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
	if (os_atomic_load(&display_stopping, acquire) || !os_atomic_load(&irq.streaming, acquire)) return 0;
	uint64_t start = mach_absolute_time(), tick, period, count, now, ns;
	uint64_t earliest, latest;
	nanoseconds_to_absolutetime(6 * NSEC_PER_MSEC, &earliest);
	nanoseconds_to_absolutetime(10500 * NSEC_PER_USEC, &latest);
	do {
		if (os_atomic_load(&display_stopping, acquire)) return 0;
		if (!lemonade_display_vsync_snapshot(&tick, &period, &count)) {
			if (os_atomic_load(&display_stopping, acquire)) return 0;
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
	if (os_atomic_load(&display_stopping, acquire)) return;
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
	bool enabled = ml_set_interrupts_enabled(false);
	if (os_atomic_load(&display_stopping, acquire)) {
		ml_set_interrupts_enabled(enabled);
		return;
	}
	os_atomic_store(&display_stopping, true, release);
	display_restoring = true;
	display_map_restore();
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
	display_restoring = false;
	tell("lemonade-display: quiesced; resumed probe access disabled\n");
	ml_set_interrupts_enabled(enabled);
}

static bool
bus_clock(unsigned index, vm_offset_t reg, uint32_t mode, bool hardware_gated)
{
	bool enabled;
	if (!display_transaction_begin(&enabled)) return false;
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
		ml_set_interrupts_enabled(enabled);
		return false;
	}
	ml_set_interrupts_enabled(enabled);
	return true;
}

static uint32_t
report(vm_offset_t base, const char *block, const char *name, uint32_t offset)
{
	uint32_t value = rd(base, offset);
	tell("lemonade-display: %s.%s[%#x]=%#x\n", block, name, offset, value);
	return value;
}

/* Linux v6.12 sm8350.dtsi: MDSS uses SID 0x820 with mask 0x402. Inspect
 * all four possible streams. Do not change a match, context, page table or
 * fault latch owned by the firmware. Qualcomm may emulate these registers,
 * so use plain 32-bit loads, including the two halves of a TTBR. */
static void
display_smmu_survey(void)
{
	vm_offset_t base = ml_io_map(0x15000000, 0x100000);
	if (!base) return;
	uint32_t control = rd(base, 0), id0 = rd(base, 0x20), id1 = rd(base, 0x24);
	uint32_t matches = id0 & 255, contexts = id1 & 255;
	uint32_t page = (id1 & (1u << 31)) ? 0x10000 : 0x1000;
	uint32_t cb_start = page << (((id1 >> 28) & 7) + 1);
	tell("lemonade-display: smmu control=%#x id0=%#x id1=%#x id2=%#x fault=%#x\n",
	    control, id0, id1, rd(base, 0x28), rd(base, 0x48));
	if (control == ~0u || !(id0 & (1u << 27)) || !matches || !contexts ||
	    contexts > 128 || cb_start + (uint64_t)contexts * page > 0x100000) {
		tell("lemonade-display: smmu unsupported geometry; no downstream reads\n"); return;
	}
	bool wide = (control & (1u << 3)) != 0;
	bool seen[128] = { false };
	unsigned covering[4] = { 0 }, used = 0;
	static const uint32_t streams[4] = { 0x820, 0x822, 0xc20, 0xc22 };
	for (unsigned i = 0; i < matches; i++) {
		uint32_t smr = rd(base, 0x800 + 4 * i), s2cr = rd(base, 0xc00 + 4 * i);
		if (!(wide ? s2cr & (1u << 10) : smr & (1u << 31))) continue;
		used++;
		uint32_t stream = smr & (wide ? 0xffff : 0x7fff);
		uint32_t mask = (smr >> 16) & (wide ? 0xffff : 0x7fff), covers = 0;
		for (unsigned n = 0; n < 4; n++) if (!((stream ^ streams[n]) & ~mask)) {
			covering[n]++; covers |= 1u << n;
		}
		if (!covers) continue;
		tell("lemonade-display: smmu match=%u sid=%#x mask=%#x s2cr=%#x covers=%#x\n",
		    i, stream, mask, s2cr, covers);
		uint32_t cb = s2cr & 255;
		if (((s2cr >> 16) & 3) || cb >= contexts || seen[cb]) continue;
		seen[cb] = true;
		vm_offset_t bank = base + cb_start + cb * page;
		tell("lemonade-display: smmu cb=%u offset=%#x cbar=%#x cba2r=%#x sctlr=%#x tcr=%#x tcr2=%#x\n",
		    cb, cb_start + cb * page, rd(base + page, 4 * cb), rd(base + page, 0x800 + 4 * cb),
		    rd(bank, 0), rd(bank, 0x30), rd(bank, 0x10));
		tell("lemonade-display: smmu cb=%u ttbr0=%#x:%08x ttbr1=%#x:%08x mair=%#x:%08x fault=%#x syn=%#x far=%#x:%08x\n",
		    cb, rd(bank, 0x24), rd(bank, 0x20), rd(bank, 0x2c), rd(bank, 0x28),
		    rd(bank, 0x3c), rd(bank, 0x38), rd(bank, 0x58), rd(bank, 0x68), rd(bank, 0x64), rd(bank, 0x60));
	}
	tell("lemonade-display: smmu survey used=%u/%u coverage=%u/%u/%u/%u writes=0\n",
	    used, matches, covering[0], covering[1], covering[2], covering[3]);
}

#define DISPLAY_GOP 0xe4d00000ull
#define DISPLAY_GOP_BYTES (4320u * 2400u)
#define DISPLAY_SCALE_PITCH 4544u
#define DISPLAY_SCALE_BYTES (DISPLAY_SCALE_PITCH * 2436u)
#define DISPLAY_TABLE_BYTES (32u * 4096u)
#define DISPLAY_PTE_ADDRESS 0x0000fffffffff000ull
#define DISPLAY_FSR_FAULT 0xc00001feu /* [10:9] describe the format, not faults */
#define DISPLAY_MISR_CONTROL 0x8f0001ffu /* excludes read-only status and W1C */
#define DISPLAY_BUFFER_IOVA(n) (0x10000000ull + (n) * 0x02000000ull)
/* INTF1 is bound to master PINGPONG0; the two DSC/mixers share its transfer. */
#define DISPLAY_PP_DONE (1u << 8)
static const unsigned display_cb_offsets[] = {0, 0x10, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x38, 0x3c};
/* Only writable source geometry, pixel extension and QSEED configuration.
 * Opmode is last so all parameters precede enable/disable on both paths. */
static const unsigned display_scale_offsets[] = {
	0, 8, 0x24, 0x100, 0x104, 0x108, 0x110, 0x114, 0x118, 0x120, 0x124, 0x128,
	0xa10, 0xa14, 0xa18, 0xa1c, 0xa20, 0xa40, 0xa44, 0xa48,
	0xa90, 0xa94, 0xa98, 0xa9c, 0xa04
};
#define DISPLAY_SCALE_REGS (sizeof(display_scale_offsets) / sizeof(display_scale_offsets[0]))
static struct {
	vm_offset_t smmu, cb, tables, mdp, buffers[2];
	unsigned table_pages;
	uint32_t saved[9], misr[2], baseline[2], source[2], flush;
	uint32_t sync_config, autorefresh;
	uint32_t scale_saved[2][DISPLAY_SCALE_REGS], scale_reference[2][2];
	uint32_t intf_misr, intf_reference[2];
	uint32_t scale_pattern;
	bool active, misr_active, flip_active, manual_active, scale_active, intf_misr_active;
} display_map;

static uint64_t *
display_table(uint64_t *entry, bool create)
{
	if (!*entry && create && display_map.table_pages < DISPLAY_TABLE_BYTES / 4096) {
		vm_offset_t child = display_map.tables + 4096 * display_map.table_pages++;
		uint64_t physical = kvtophys(child);
		if (!physical || physical >> 36 || physical & 4095) return NULL;
		*entry = physical | 3;
	}
	if ((*entry & 3) != 3) return NULL;
	for (unsigned p = 1; p < display_map.table_pages; p++) {
		vm_offset_t child = display_map.tables + 4096 * p;
		if (*entry == (kvtophys(child) | 3)) return (void *)child;
	}
	return NULL;
}

static uint64_t *
display_leaf(uint64_t iova, bool create)
{
	if (iova >> 32 || !display_map.tables) return NULL;
	uint64_t *root = (void *)display_map.tables;
	uint64_t *l2 = display_table(&root[(iova >> 30) & 511], create);
	if (!l2) return NULL;
	uint64_t *l3 = display_table(&l2[(iova >> 21) & 511], create);
	return l3 ? &l3[(iova >> 12) & 511] : NULL;
}

static bool
display_tlb_sync(void)
{
	wr(display_map.cb + 0x610, 5); /* only this context's temporary ASID */
	wr(display_map.cb + 0x7f0, 0);
	for (unsigned i = 0; i < 1000; i++) {
		if (!(rd(display_map.cb, 0x7f4) & 1)) return true;
		delay(10);
	}
	return false;
}

/* Also usable after other CPUs/interrupts have stopped on the return path.
 * Wait for two complete hardware transfers and the original mixer signatures. */
static bool
display_original_pixels(void)
{
	vm_offset_t mdp = display_map.mdp, intf = mdp + INTF1_OFFSET;
	uint32_t first = rd(intf, 0xac);
	for (unsigned i = 0; i < 2000; i++) {
		if (rd(intf, 0xac) - first >= 2 && rd(intf, 0xb0) == 1 && rd(intf, 0x2b0) >= 2400 &&
		    rd(mdp, 0x44314) == display_map.baseline[0] && rd(mdp, 0x45314) == display_map.baseline[1]) return true;
		delay(50);
	}
	tell("lemonade-display: original pixels wait failed frames=%u line=%u tear=%u MISR=%#x/%#x expected=%#x/%#x auto=%#x\n",
	    rd(intf, 0xac) - first, rd(intf, 0xb0), rd(intf, 0x2b0), rd(mdp, 0x44314), rd(mdp, 0x45314),
	    display_map.baseline[0], display_map.baseline[1], rd(intf, 0x2b4));
	return false;
}

static void
display_map_restore(void)
{
	bool enabled = ml_set_interrupts_enabled(false);
	if (display_stopping && !display_restoring) goto restored;
	if (display_map.active || display_map.flip_active) {
		/* Capture fault latches before restoring the context can clear them.
		 * An interrupted-run acceptance must also prove that an owned source
		 * was still being scanned when quiesce began. */
		tell("lemonade-display: mapping cleanup stopping=%u mapped=%u flipping=%u source=%#x/%#x fsr=%#x global=%#x underrun=%u\n",
		    display_stopping, display_map.active, display_map.flip_active,
		    rd(display_map.mdp, 0x4014), rd(display_map.mdp, 0x6014),
		    rd(display_map.cb, 0x58), rd(display_map.smmu, 0x48),
		    !!(rd(display_map.mdp, 0x14) & (1u << 26)));
	}
	if (display_map.flip_active) {
		bool manual = display_map.manual_active;
		if (display_map.scale_active) {
			bool exact = true;
			for (unsigned n = 0; n < 2; n++) {
				vm_offset_t pipe = display_map.mdp + 0x4000 + n * 0x2000;
				for (unsigned r = 0; r < DISPLAY_SCALE_REGS; r++)
					wr(pipe + display_scale_offsets[r], display_map.scale_saved[n][r]);
				for (unsigned r = 0; r < DISPLAY_SCALE_REGS; r++)
					exact &= rd(pipe, display_scale_offsets[r]) == display_map.scale_saved[n][r];
			}
			tell("lemonade-display: scaler restored registers=%u count=%u before_gop=1\n", exact, (unsigned)(2 * DISPLAY_SCALE_REGS));
			display_map.scale_active = false;
		}
		/* Restore the physical GOP source while its identity mapping is still
		 * live. Confirm actual output before turning translation off. */
		wr(display_map.mdp + 0x4014, display_map.source[0]);
		wr(display_map.mdp + 0x6014, display_map.source[1]);
		wr(display_map.mdp + 0x15018, 3); /* only VIG0 and VIG1 */
		if (manual) {
			/* Resume the original automatic transfer only after selecting GOP,
			 * while its identity mapping and both owned buffers are still valid. */
			vm_offset_t intf = display_map.mdp + INTF1_OFFSET;
			wr(intf + 0x288, display_map.sync_config & ~(1u << 20));
			wr(display_map.mdp + 0x18, DISPLAY_PP_DONE);
			/* The stock command encoder enables autorefresh instead of issuing
			 * CTL_START. Connect TE after arming it, avoiding a premature frame. */
			wr(intf + 0x2b4, display_map.autorefresh);
			wr(intf + 0x288, display_map.sync_config);
			tell("lemonade-display: manual restored sync=%u auto=%u\n",
			    rd(intf, 0x288) == display_map.sync_config, rd(intf, 0x2b4) == display_map.autorefresh);
			display_map.manual_active = false;
		}
		bool pixels = display_original_pixels();
		if (!pixels && !manual) {
			/* A stalled automatic refresh must still latch the original source. */
			wr(display_map.mdp + 0x1501c, 1);
			pixels = display_original_pixels();
		}
		tell("lemonade-display: source restore pixels=%u address=%u/%u flush=%#x\n", pixels,
		    rd(display_map.mdp, 0x4014) == display_map.source[0],
		    rd(display_map.mdp, 0x6014) == display_map.source[1], rd(display_map.mdp, 0x15018));
		wr(display_map.mdp + 0x15018, display_map.flush);
		display_map.flip_active = false;
	}
	if (display_map.active) {
		/* The source addresses are still physical GOP addresses. Disabling our
		 * translation immediately restores firmware bypass, even on panic. Never
		 * touch its stream match, CBAR, CBA2R, or pre-existing fault latches. */
		wr(display_map.cb, 0);
		bool synced = display_tlb_sync();
		uint32_t new_faults = rd(display_map.cb, 0x58) & DISPLAY_FSR_FAULT;
		if (new_faults) wr(display_map.cb + 0x58, new_faults); /* preflight required none */
		for (unsigned i = 1; i < 9; i++) wr(display_map.cb + display_cb_offsets[i], display_map.saved[i]);
		wr(display_map.cb, display_map.saved[0]);
		bool exact = true;
		for (unsigned i = 0; i < 9; i++) {
			uint32_t actual = rd(display_map.cb, display_cb_offsets[i]);
			if (actual != display_map.saved[i]) {
				exact = false;
				tell("lemonade-display: context restore offset=%#x expected=%#x actual=%#x\n",
				    display_cb_offsets[i], display_map.saved[i], actual);
			}
		}
		tell("lemonade-display: mapping restore registers=%u tlb=%u fsr=%#x global=%#x\n",
		    exact, synced, rd(display_map.cb, 0x58), rd(display_map.smmu, 0x48));
		display_map.active = false;
	}
	if (display_map.misr_active) {
		for (unsigned i = 0; i < 2; i++) {
			vm_offset_t reg = display_map.mdp + 0x44310 + 0x1000 * i;
			wr(reg, 1u << 10); /* clear the status raised by our own measurement */
			wr(reg, display_map.misr[i] & DISPLAY_MISR_CONTROL);
		}
		tell("lemonade-display: MISR control restored=%u/%u status=%#x/%#x prior_status=%#x/%#x\n",
		    (rd(display_map.mdp, 0x44310) & DISPLAY_MISR_CONTROL) == (display_map.misr[0] & DISPLAY_MISR_CONTROL),
		    (rd(display_map.mdp, 0x45310) & DISPLAY_MISR_CONTROL) == (display_map.misr[1] & DISPLAY_MISR_CONTROL),
		    rd(display_map.mdp, 0x44310) & 0x200, rd(display_map.mdp, 0x45310) & 0x200,
		    display_map.misr[0] & 0x200, display_map.misr[1] & 0x200);
		display_map.misr_active = false;
	}
	if (display_map.intf_misr_active) {
		vm_offset_t control = display_map.mdp + INTF1_OFFSET + 0x180;
		wr(control, 1u << 10);
		wr(control, display_map.intf_misr & DISPLAY_MISR_CONTROL);
		tell("lemonade-display: interface MISR restored=%u status=%#x prior_status=%#x\n",
		    (rd(control, 0) & DISPLAY_MISR_CONTROL) == (display_map.intf_misr & DISPLAY_MISR_CONTROL),
		    rd(control, 0) & 0x200, display_map.intf_misr & 0x200);
		display_map.intf_misr_active = false;
	}
	/* Keep the small table allocation until the timed kernel return. No live
	 * translation can ever reference freed kernel pages during this probe. */
restored:
	ml_set_interrupts_enabled(enabled);
}

static bool
display_map_configure(const uint32_t baseline[2])
{
	bool enabled, valid;
	if (!display_transaction_begin(&enabled)) return false;
	vm_offset_t cb = display_map.cb;
	display_map.baseline[0] = baseline[0]; display_map.baseline[1] = baseline[1];
	display_map.active = true; /* complete journal before the first context write */
	wr(cb, 0xe0);
	wr(cb + 0x10, 1u | (1u << 4) | (7u << 15));
	wr(cb + 0x30, 25u | (1u << 8) | (1u << 10) | (3u << 12) | (1u << 23));
	uint64_t root = kvtophys(display_map.tables) | (5ull << 48);
	wr(cb + 0x20, root); wr(cb + 0x24, root >> 32);
	wr(cb + 0x28, 0); wr(cb + 0x2c, 0);
	wr(cb + 0x38, 0xf404ff44); wr(cb + 0x3c, 0);
	valid = rd(cb, 0x20) == (uint32_t)root && rd(cb, 0x24) == (uint32_t)(root >> 32) &&
	    rd(cb, 0x30) == 0x803519 && (rd(cb, 0x10) & ~0x60u) == 0x38011 && rd(cb, 0x38) == 0xf404ff44;
	if (!valid)
		tell("lemonade-display: mapping register readback refused tcr=%#x tcr2=%#x\n", rd(cb, 0x30), rd(cb, 0x10));
	else if (!(valid = display_tlb_sync()))
		tell("lemonade-display: initial TLB sync failed\n");
	ml_set_interrupts_enabled(enabled);
	return valid && !os_atomic_load(&display_stopping, acquire);
}

static bool
display_flip_buffer(unsigned n)
{
	bool enabled;
	if (n > 1 || !display_transaction_begin(&enabled)) return false;
	wr(display_map.mdp + 0x4014, DISPLAY_BUFFER_IOVA(n));
	wr(display_map.mdp + 0x6014, DISPLAY_BUFFER_IOVA(n));
	wr(display_map.mdp + 0x15018, 3);
	if (display_map.manual_active) {
		wr(display_map.mdp + 0x18, DISPLAY_PP_DONE);
		wr(display_map.mdp + 0x1501c, 1);
	}
	ml_set_interrupts_enabled(enabled);
	return !os_atomic_load(&display_stopping, acquire);
}

/* Linux DPU 7 command-mode handover: disconnect external TE, disable
 * autorefresh, finish any active transfer, then reconnect TE. No panel/DSI
 * timing is changed. A complete journal precedes the first register write. */
static bool
display_manual_begin(void)
{
	bool enabled;
	if (!display_transaction_begin(&enabled)) return false;
	vm_offset_t mdp = display_map.mdp, intf = mdp + INTF1_OFFSET;
	uint32_t sync = rd(intf, 0x288), automatic = rd(intf, 0x2b4);
	/* INTF_MUX selects the pingpong in bits [3:0]; firmware retains 0xf
	 * in the unrelated [19:16] field. Preserve it, as the stock driver does. */
	if (!display_map.flip_active || sync != 0x180085 || automatic != 0x80000001 || (rd(intf, 0x25c) & 0xf) != 0 ||
	    (rd(mdp, 0x10) & DISPLAY_PP_DONE)) {
		tell("lemonade-display: manual refused sync=%#x auto=%#x pp=%#x done_enable=%#x\n", sync, automatic, rd(intf, 0x25c), rd(mdp, 0x10));
		ml_set_interrupts_enabled(enabled);
		return false;
	}
	display_map.sync_config = sync; display_map.autorefresh = automatic;
	display_map.manual_active = true;
	wr(intf + 0x288, sync & ~(1u << 20));
	wr(intf + 0x2b4, automatic & ~(1u << 31));
	bool idle = false;
	for (unsigned i = 0; i < 2000; i++) {
		uint32_t line = rd(intf, 0x2b0) & 0xffff;
		if (!line || line >= 2400) { idle = true; break; }
		delay(50);
	}
	wr(intf + 0x288, sync);
	wr(mdp + 0x18, DISPLAY_PP_DONE);
	bool configured = idle && rd(intf, 0x288) == sync && rd(intf, 0x2b4) == (automatic & ~(1u << 31));
	tell("lemonade-display: manual configured=%u idle=%u sync=%#x auto=%#x status=%#x\n",
	    configured, idle, rd(intf, 0x288), rd(intf, 0x2b4), rd(mdp, 0x14));
	ml_set_interrupts_enabled(enabled);
	return configured && !os_atomic_load(&display_stopping, acquire);
}

static bool
display_manual_idle(void)
{
	vm_offset_t mdp = display_map.mdp, intf = mdp + INTF1_OFFSET;
	uint32_t first = rd(intf, 0xac);
	delay(50000);
	if (os_atomic_load(&display_stopping, acquire)) return false;
	uint32_t frames = rd(intf, 0xac) - first, done = rd(mdp, 0x14) & DISPLAY_PP_DONE;
	tell("lemonade-display: manual idle frames=%u done=%#x line=%u tear=%u\n",
	    frames, done, rd(intf, 0xb0), rd(intf, 0x2b0));
	return !frames && !done;
}

static bool
display_manual_sample(uint32_t value[2], uint32_t first)
{
	vm_offset_t mdp = display_map.mdp, intf = mdp + INTF1_OFFSET;
	for (unsigned i = 0; i < 2000; i++) {
		if (os_atomic_load(&display_stopping, acquire)) return false;
		uint32_t done = rd(mdp, 0x14) & DISPLAY_PP_DONE;
		if (done == DISPLAY_PP_DONE && rd(intf, 0xb0) == 1 && rd(intf, 0x2b0) >= 2400) {
			uint32_t frames = rd(intf, 0xac) - first;
			for (unsigned n = 0; n < 2; n++) value[n] = rd(mdp, 0x44314 + 0x1000 * n);
			if (frames == 1) return true;
			tell("lemonade-display: manual frame count failed frames=%u\n", frames);
			return false;
		}
		delay(50);
	}
	tell("lemonade-display: manual transfer failed frame_delta=%u done=%#x line=%u tear=%u start=%#x\n",
	    rd(intf, 0xac) - first, rd(mdp, 0x14) & DISPLAY_PP_DONE, rd(intf, 0xb0), rd(intf, 0x2b0), rd(mdp, 0x1501c));
	return false;
}

static bool
display_scale_configure(void)
{
	vm_offset_t mdp = display_map.mdp;
	bool enabled;
	if (!display_transaction_begin(&enabled)) return false;
	for (unsigned n = 0; n < 2; n++)
		for (unsigned r = 0; r < DISPLAY_SCALE_REGS; r++)
			display_map.scale_saved[n][r] = rd(mdp + 0x4000 + n * 0x2000, display_scale_offsets[r]);
	display_map.scale_active = true; /* complete both journals before any write */
	uint32_t step_x = (1u << 21) * 25u / 24u, step_y = (1u << 21) * 203u / 200u;
	uint32_t center_x = (step_x - (1u << 21)) / 2, center_y = (step_y - (1u << 21)) / 2;
	for (unsigned n = 0; n < 2; n++) {
		vm_offset_t pipe = mdp + 0x4000 + n * 0x2000, scaler = pipe + 0xa00;
		uint32_t phase_x = (540u * n * step_x + center_x) & 0x1fffff;
		wr(pipe, (2436u << 16) | 563u); wr(pipe + 8, n * 562u); wr(pipe + 0x24, DISPLAY_SCALE_PITCH);
		for (unsigned c = 0; c < 3; c++) {
			wr(pipe + 0x100 + c * 0x10, 0); wr(pipe + 0x104 + c * 0x10, 0);
			wr(pipe + 0x108 + c * 0x10, (2436u << 16) | 563u);
		}
		wr(scaler + 0x10, step_x); wr(scaler + 0x14, step_y);
		wr(scaler + 0x18, step_x); wr(scaler + 0x1c, step_y);
		wr(scaler + 0x20, 0x03050305); /* Observed firmware 5/3: 4/2 passes uniform images but fails the spatial reference. */
		wr(scaler + 0x40, (2436u << 16) | 563u); wr(scaler + 0x44, (2436u << 16) | 563u);
		wr(scaler + 0x48, (2400u << 16) | 540u);
		wr(scaler + 0x90, phase_x); wr(scaler + 0x94, center_y);
		wr(scaler + 0x98, phase_x); wr(scaler + 0x9c, center_y);
		/* Bilinear RGB/alpha, 8-bit input; no LUT or detail enhancer.
		 * This 0x3000 block reads bit 31 back as zero (QSEED3 blend
		 * selection is not retained); use its observed writable mode. */
		wr(scaler + 4, 0x20034401);
	}
	bool valid = true;
	for (unsigned n = 0; n < 2; n++) {
		vm_offset_t pipe = mdp + 0x4000 + n * 0x2000;
		tell("lemonade-display: scaler readback pipe=%u size=%#x xy=%#x pitch=%#x op=%#x step=%#x/%#x preload=%#x phase=%#x\n",
		    n, rd(pipe, 0), rd(pipe, 8), rd(pipe, 0x24), rd(pipe, 0xa04),
		    rd(pipe, 0xa10), rd(pipe, 0xa14), rd(pipe, 0xa20), rd(pipe, 0xa90));
		valid &= rd(pipe, 0) == ((2436u << 16) | 563u) && rd(pipe, 8) == n * 562u &&
		    rd(pipe, 0x24) == DISPLAY_SCALE_PITCH && rd(pipe, 0xa04) == 0x20034401;
	}
	tell("lemonade-display: scaler configured=%u source=1125x2436 pitch=%u output=1080x2400 overlap=1 step=%u/%u phase=%u/%u/%u pattern=%u padding=poisoned\n",
	    valid, DISPLAY_SCALE_PITCH, step_x, step_y, center_x, (540u * step_x + center_x) & 0x1fffff, center_y, display_map.scale_pattern);
	ml_set_interrupts_enabled(enabled);
	return valid && !os_atomic_load(&display_stopping, acquire);
}

static void
display_scale_fill(unsigned n, bool scaled)
{
	uint32_t *pixels = (void *)display_map.buffers[n];
	unsigned width = scaled ? 1125 : 1080, height = scaled ? 2436 : 2400;
	unsigned stride = scaled ? DISPLAY_SCALE_PITCH / 4 : 1080;
	for (unsigned y = 0; y < height; y++) for (unsigned x = 0; x < stride; x++) {
		uint32_t color = n ? 0xff142024 : 0xff201018;
		if (display_map.scale_pattern) {
			/* The 25x203 input cells map exactly onto 24x200 output
			 * cells. Pixel-center sampling never straddles a color edge. */
			unsigned gx = display_map.scale_pattern == 3 ? 0 : x / (scaled ? 25 : 24);
			unsigned gy = display_map.scale_pattern == 2 ? 0 : y / (scaled ? 203 : 200);
			unsigned r = 16 + (gx * 3 + gy * 5 + n * 7) % 24;
			unsigned g = 16 + (gx * 7 + gy * 3 + n * 11) % 24;
			unsigned b = 16 + (gx * 5 + gy * 7 + n * 13) % 24;
			color = 0xff000000 | (r << 16) | (g << 8) | b;
		}
		pixels[y * stride + x] = x < width ? color : 0xff500050;
	}
}

static bool
display_scale_begin(void)
{
	/* The command engine must be idle. Capture actual native-output references
	 * before changing either geometry or buffer padding; these are not guessed
	 * CRCs. Padding is then poisoned to detect an incorrect stride or crop. */
	vm_offset_t mdp = display_map.mdp;
	if (!display_map.manual_active || display_map.scale_active) return false;
	bool enabled;
	if (!display_transaction_begin(&enabled)) return false;
	uint32_t intf_control = rd(mdp, INTF1_OFFSET + 0x180);
	if (intf_control & 0x100) {
		tell("lemonade-display: interface MISR already owned; refused\n");
		ml_set_interrupts_enabled(enabled); return false;
	}
	display_map.intf_misr = intf_control;
	display_map.intf_misr_active = true;
	wr(mdp + INTF1_OFFSET + 0x180, 1u << 10);
	wr(mdp + INTF1_OFFSET + 0x180, 0x81000101); /* Linux INTF encoder input select 1 */
	ml_set_interrupts_enabled(enabled);
	for (unsigned n = 0; n < 2; n++) {
		vm_offset_t pipe = mdp + 0x4000 + n * 0x2000;
		if (rd(pipe, 0xa00) != 0x3000 || rd(pipe, 0xa04) != 0 ||
		    rd(pipe, 0x30) != 0x237ff || rd(pipe, 0x34) != 0x3020001 ||
		    rd(pipe, 0x38) != 0x80000000 || rd(pipe, 8) != n * 540 ||
		    rd(pipe, 0xc) != 0x960021c || rd(pipe, 0x10) != n * 540) {
			tell("lemonade-display: scaler refused: firmware preflight differs\n"); return false;
		}
	}
	for (unsigned n = 0; n < 2; n++) {
		uint32_t first = rd(mdp, INTF1_OFFSET + 0xac);
		if (!display_flip_buffer(n) || !display_manual_sample(display_map.scale_reference[n], first)) return false;
		if ((rd(mdp, INTF1_OFFSET + 0x180) & 0x300) != 0x300) return false;
		display_map.intf_reference[n] = rd(mdp, INTF1_OFFSET + 0x184);
		tell("lemonade-display: scaler native buffer=%u MISR=%#x/%#x intf=%#x status=%#x\n",
		    n, display_map.scale_reference[n][0], display_map.scale_reference[n][1], display_map.intf_reference[n], rd(mdp, INTF1_OFFSET + 0x180));
	}
	for (unsigned n = 0; n < 2; n++) {
		display_scale_fill(n, true);
		for (vm_offset_t line = display_map.buffers[n]; line < display_map.buffers[n] + DISPLAY_SCALE_BYTES; line += 64)
			__asm__ volatile("dc civac, %0" : : "r"(line) : "memory");
	}
	__asm__ volatile("dsb sy" ::: "memory");
	return display_scale_configure();
}

static bool
display_misr_sample(uint32_t value[2])
{
	/* Free-running one-frame MISR, sampled after a complete new transfer. */
	if (os_atomic_load(&display_stopping, acquire)) return false;
	delay(20000);
	struct lemonade_present_window window;
	if (lemonade_display_present_begin(&window) != 1 || os_atomic_load(&display_stopping, acquire)) return false;
	for (unsigned i = 0; i < 2; i++) {
		vm_offset_t lm = display_map.mdp + 0x44000 + 0x1000 * i;
		if ((rd(lm, 0x310) & 0x300) != 0x300) return false;
		value[i] = rd(lm, 0x314);
	}
	return true;
}

static void
display_mapping_probe(vm_offset_t mdp, vm_offset_t mdss, bool flip, bool manual, bool scale)
{
	if (os_atomic_load(&display_stopping, acquire)) return;
	uint32_t grid = 0;
	PE_parse_boot_argn("lemonade_display_pattern", &grid, sizeof(grid));
	if (grid > 3 || (grid && !scale)) return;
	display_map.scale_pattern = grid;
	display_map.mdp = mdp;
	display_map.smmu = ml_io_map(0x15000000, 0x100000);
	if (!display_map.smmu) return;
	vm_offset_t smmu = display_map.smmu, cb = smmu + 0x85000;
	display_map.cb = cb;
	/* The exact observed firmware state is required: one MDSS match covering
	 * all four nonsecure streams, AArch64 stage-1 context, translation disabled.
	 * The UI and GPU have not started yet. Other contexts are never modified. */
	if ((rd(smmu, 0) & 9) || rd(smmu, 0x20) != 0x4c017e59 || rd(smmu, 0x24) != 0x6000004b ||
	    rd(smmu, 0x818) != 0x84020820 || rd(smmu, 0xc18) != 5 ||
	    rd(smmu, 0x1014) != 0x1f000 || rd(smmu, 0x1814) != 1 ||
	    (rd(cb, 0x58) & DISPLAY_FSR_FAULT) || rd(smmu, 0x48) ||
	    rd(mdp, 0x4014) != DISPLAY_GOP || rd(mdp, 0x6014) != DISPLAY_GOP ||
	    rd(mdp, 0x4024) != 4320 || rd(mdp, 0x6024) != 4320 ||
	    rd(mdp, 0x4000) != 0x960021c || rd(mdp, 0x6000) != 0x960021c ||
	    rd(mdp, 0x150fc) != 0x30000 || rd(mdp, 0x150e8) != 3 || rd(mdp, 0x150f4) != 2) {
		tell("lemonade-display: mapping refused: firmware preflight differs\n"); return;
	}
	static const uint32_t expected[9] = {0xe0, 0x60, 0, 0, 0, 0, 0, 0, 0};
	for (unsigned i = 0; i < 9; i++) {
		display_map.saved[i] = rd(cb, display_cb_offsets[i]);
		if (display_map.saved[i] != expected[i]) {
			tell("lemonade-display: mapping refused: context register %#x differs\n", display_cb_offsets[i]); return;
		}
	}
	for (unsigned i = 0; i < 2; i++) {
		display_map.misr[i] = rd(mdp, 0x44310 + 0x1000 * i);
		if (display_map.misr[i] & 0x100) { tell("lemonade-display: MISR already owned; refused\n"); return; }
	}
	if (kmem_alloc(kernel_map, &display_map.tables, round_page(DISPLAY_TABLE_BYTES),
	    KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) return;
	display_map.table_pages = 1;
	unsigned size = (DISPLAY_GOP_BYTES + 4095) & ~4095u;
	for (unsigned offset = 0; offset < size; offset += 4096) {
		uint64_t *entry = display_leaf(DISPLAY_GOP + offset, true);
		if (!entry || *entry) { tell("lemonade-display: GOP table allocation failed\n"); return; }
		/* Read-only for both privilege levels, normal noncached, outer shareable. */
		*entry = (DISPLAY_GOP + offset) | 3ull | (3ull << 6) | (2ull << 8) |
		    (1ull << 10) | (1ull << 11) | (1ull << 53) | (1ull << 54);
	}
	for (unsigned offset = 0; offset < size; offset += 4096) {
		uint64_t *entry = display_leaf(DISPLAY_GOP + offset, false);
		if (!entry || (*entry & DISPLAY_PTE_ADDRESS) != DISPLAY_GOP + offset) return;
	}
	uint64_t *before = display_leaf(DISPLAY_GOP - 4096, false), *after = display_leaf(DISPLAY_GOP + size, false);
	if ((before && *before) || (after && *after)) return;
	unsigned identity_size = size;
	if (scale) size = (DISPLAY_SCALE_BYTES + 4095) & ~4095u;
	if (flip) for (unsigned n = 0; n < 2; n++) {
		unsigned allocation = round_page(size);
		if (kmem_alloc(kernel_map, &display_map.buffers[n], allocation,
		    KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) return;
		uint32_t *pixels = (void *)display_map.buffers[n];
		/* Close dark greys keep the bounded alternating-pattern test unobtrusive. */
		for (unsigned p = 0; p < size / 4; p++) pixels[p] = n ? 0xff121212 : 0xff101010;
		if (scale) display_scale_fill(n, false);
		unsigned high = 0;
		for (unsigned offset = 0; offset < size; offset += 4096) {
			uint64_t physical = kvtophys(display_map.buffers[n] + offset);
			uint64_t *entry = display_leaf(DISPLAY_BUFFER_IOVA(n) + offset, true);
			if (!physical || physical >> 36 || physical & 4095 || !entry || *entry) return;
			*entry = physical | 3ull | (3ull << 6) | (2ull << 8) | (1ull << 10) | (1ull << 11) | (1ull << 53) | (1ull << 54);
			high += physical >> 32 != 0;
		}
		for (unsigned offset = 0; offset < size; offset += 4096) {
			uint64_t *entry = display_leaf(DISPLAY_BUFFER_IOVA(n) + offset, false);
			if (!entry || (*entry & DISPLAY_PTE_ADDRESS) != kvtophys(display_map.buffers[n] + offset)) return;
		}
		before = display_leaf(DISPLAY_BUFFER_IOVA(n) - 4096, false);
		after = display_leaf(DISPLAY_BUFFER_IOVA(n) + size, false);
		if ((before && *before) || (after && *after)) return;
		for (vm_offset_t line = display_map.buffers[n]; line < display_map.buffers[n] + allocation; line += 64)
			__asm__ volatile("dc civac, %0" : : "r"(line) : "memory");
		tell("lemonade-display: buffer %u pages=%u above4G=%u iova=%#llx guards=unmapped\n", n, size / 4096, high, DISPLAY_BUFFER_IOVA(n));
	}
	for (vm_offset_t line = display_map.tables; line < display_map.tables + DISPLAY_TABLE_BYTES; line += 64)
		__asm__ volatile("dc civac, %0" : : "r"(line) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
	tell("lemonade-display: identity table verified pages=%u tables=%u guards=unmapped\n", identity_size / 4096, display_map.table_pages);
	irq_probe(mdp, mdss, true);
	if (!irq.acquired || !irq.streaming) return;
	bool enabled;
	if (!display_transaction_begin(&enabled)) return;
	display_map.misr_active = true;
	for (unsigned i = 0; i < 2; i++) {
		wr(mdp + 0x44310 + 0x1000 * i, 1u << 10);
		wr(mdp + 0x44310 + 0x1000 * i, (1u << 31) | (1u << 8) | 1);
	}
	ml_set_interrupts_enabled(enabled);
	uint32_t baseline[2], sample[2];
	if (!display_misr_sample(baseline) || !display_misr_sample(sample) ||
	    baseline[0] != sample[0] || baseline[1] != sample[1]) {
		tell("lemonade-display: mapping refused: MISR missing or initial image unstable\n"); goto done;
	}
	tell("lemonade-display: initial MISR=%#x/%#x\n", baseline[0], baseline[1]);
	if (!display_map_configure(baseline)) goto done;
	struct lemonade_present_window window;
	if (lemonade_display_present_begin(&window) != 1) goto done;
	if (!display_transaction_begin(&enabled)) return;
	wr(cb, 0x1127);
	ml_set_interrupts_enabled(enabled);
	unsigned good = 0, first_frame = rd(mdp, INTF1_OFFSET + 0xac);
	for (; good < 32; good++) {
		if (!display_misr_sample(sample) || sample[0] != baseline[0] || sample[1] != baseline[1] ||
		    (rd(cb, 0x58) & DISPLAY_FSR_FAULT) || rd(smmu, 0x48) || !(rd(cb, 0) & 1)) break;
	}
	if (os_atomic_load(&display_stopping, acquire)) return;
	tell("lemonade-display: mapping samples=%u/32 frames=%u MISR=%#x/%#x fsr=%#x global=%#x sctlr=%#x\n",
	    good, rd(mdp, INTF1_OFFSET + 0xac) - first_frame, sample[0], sample[1], rd(cb, 0x58), rd(smmu, 0x48), rd(cb, 0));
	if (flip && good == 32) {
		uint32_t checksums[2][2] = {{0}}, loops = 64, completed = 0;
		PE_parse_boot_argn("lemonade_display_flips", &loops, sizeof(loops));
		if (loops != 64 && loops != 1000) goto done;
		if (!display_transaction_begin(&enabled)) return;
		display_map.source[0] = rd(mdp, 0x4014); display_map.source[1] = rd(mdp, 0x6014);
		display_map.flush = rd(mdp, 0x15018);
		uint32_t initial_underrun = rd(mdp, 0x14) & (1u << 26);
		if (!initial_underrun) display_map.flip_active = true;
		first_frame = rd(mdp, INTF1_OFFSET + 0xac);
		ml_set_interrupts_enabled(enabled);
		if (initial_underrun) { tell("lemonade-display: flip refused: pre-existing underrun\n"); goto done; }
		if (manual) {
			if (!display_manual_begin() || !display_manual_idle()) goto done;
			if (scale && !display_scale_begin()) goto done;
			first_frame = rd(mdp, INTF1_OFFSET + 0xac);
		}
		for (; completed < loops; completed++) {
			unsigned n = completed & 1;
			if (lemonade_display_present_begin(&window) != 1) break;
			uint32_t before = rd(mdp, INTF1_OFFSET + 0xac);
			if (!display_flip_buffer(n)) return;
			if (!(manual ? display_manual_sample(sample, before) : display_misr_sample(sample)) ||
			    (rd(cb, 0x58) & DISPLAY_FSR_FAULT) || rd(smmu, 0x48) ||
			    (rd(mdp, 0x14) & (1u << 26)) || rd(mdp, 0x4014) != DISPLAY_BUFFER_IOVA(n) || rd(mdp, 0x6014) != DISPLAY_BUFFER_IOVA(n)) break;
			if (scale && ((rd(mdp, INTF1_OFFSET + 0x180) & 0x300) != 0x300 ||
			    rd(mdp, INTF1_OFFSET + 0x184) != display_map.intf_reference[n])) {
				tell("lemonade-display: scaler interface mismatch buffer=%u actual=%#x expected=%#x status=%#x\n",
				    n, rd(mdp, INTF1_OFFSET + 0x184), display_map.intf_reference[n], rd(mdp, INTF1_OFFSET + 0x180));
				break;
			}
			if (manual && (completed < 2 || completed + 1 == loops))
				tell("lemonade-display: manual transfer n=%u frame_delta=%u done=%#x MISR=%#x/%#x\n",
				    completed + 1, rd(mdp, INTF1_OFFSET + 0xac) - before, rd(mdp, 0x14) & DISPLAY_PP_DONE, sample[0], sample[1]);
			if (completed < 2) {
				if (scale) {
					tell("lemonade-display: scaler interface buffer=%u actual=%#x expected=%#x status=%#x\n",
					    n, rd(mdp, INTF1_OFFSET + 0x184), display_map.intf_reference[n], rd(mdp, INTF1_OFFSET + 0x180));
					bool mixer_same = sample[0] == display_map.scale_reference[n][0] && sample[1] == display_map.scale_reference[n][1];
					tell("lemonade-display: scaler compare buffer=%u native_mixer_match=%u actual=%#x/%#x expected=%#x/%#x\n",
					    n, mixer_same, sample[0], sample[1], display_map.scale_reference[n][0], display_map.scale_reference[n][1]);
				}
				checksums[n][0] = sample[0]; checksums[n][1] = sample[1];
				tell("lemonade-display: buffer %u MISR=%#x/%#x flush=%#x\n", n, sample[0], sample[1], rd(mdp, 0x15018));
				if ((sample[0] == baseline[0] && sample[1] == baseline[1]) ||
				    (n && sample[0] == checksums[0][0] && sample[1] == checksums[0][1])) break;
			} else if (sample[0] != checksums[n][0] || sample[1] != checksums[n][1]) break;
		}
		if (os_atomic_load(&display_stopping, acquire)) return;
		tell("lemonade-display: flip completed=%u/%u frames=%u MISR=%#x/%#x fsr=%#x global=%#x underrun=%u\n",
		    completed, loops, rd(mdp, INTF1_OFFSET + 0xac) - first_frame, sample[0], sample[1],
		    rd(cb, 0x58), rd(smmu, 0x48), !!(rd(mdp, 0x14) & (1u << 26)));
	}
done:
	if (os_atomic_load(&display_stopping, acquire)) return;
	display_map_restore();
	irq_stop(true);
}

void
lemonade_display_init(void)
{
	uint32_t mode = 0;
	DTEntry entry;
	const void *value;
	unsigned length;
	if (!PE_parse_boot_argn("lemonade_display", &mode, sizeof(mode)) || !mode) return;
	if (mode > 9 || SecureDTLookupEntry(NULL, "/arm-io", &entry) != kSuccess ||
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
		report(mdp + 0x44000 + n * 0x1000, block, "misr", 0x310);
	}
	static const uint32_t pipes[] = { 0x4000, 0x6000, 0x8000, 0xa000, 0x24000, 0x26000 };
	static const uint32_t pipe_regs[] = { 0, 8, 0xc, 0x10, 0x14, 0x24, 0x30, 0x34, 0x38, 0x70 };
	for (unsigned n = 0; n < sizeof(pipes) / sizeof(pipes[0]); n++) {
		char block[16];
		snprintf(block, sizeof(block), "sspp%u", n);
		for (unsigned i = 0; i < sizeof(pipe_regs) / sizeof(pipe_regs[0]); i++)
			report(mdp + pipes[n], block, "state", pipe_regs[i]);
	}
	if (mode == 2) {
		/* Read-only scaler inventory before any future scaled-scanout probe.
		 * Linux v6.12 dpu_hw_catalog.c places SM8350's QSEED3 at VIG + 0xa00;
		 * dpu_hw_util.c and dpu_hw_sspp.c define these readable registers.
		 * Only the two pipes already scanning the firmware GOP are inspected. */
		static const uint32_t scaler_regs[] = {0, 4, 8, 0xc, 0x10, 0x14, 0x18,
		    0x1c, 0x20, 0x40, 0x44, 0x48, 0x4c, 0x50, 0x54, 0x58, 0x5c,
		    0x60, 0x90, 0x94, 0x98, 0x9c};
		static const uint32_t ext_regs[] = {0x100, 0x104, 0x108, 0x110, 0x114,
		    0x118, 0x120, 0x124, 0x128};
		for (unsigned n = 0; n < 2; n++) {
			char block[16];
			snprintf(block, sizeof(block), "vig%u_scaler", n);
			for (unsigned i = 0; i < sizeof(scaler_regs) / sizeof(scaler_regs[0]); i++)
				report(mdp + pipes[n] + 0xa00, block, "state", scaler_regs[i]);
			snprintf(block, sizeof(block), "vig%u_ext", n);
			for (unsigned i = 0; i < sizeof(ext_regs) / sizeof(ext_regs[0]); i++)
				report(mdp + pipes[n], block, "state", ext_regs[i]);
		}
	}
	if (mode == 5) { display_smmu_survey(); goto out; }
	if (mode >= 6 && mode <= 9) { display_mapping_probe(mdp, mdss, mode != 6, mode >= 8, mode == 9); goto out; }
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
