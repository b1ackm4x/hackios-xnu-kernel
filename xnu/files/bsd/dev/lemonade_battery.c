/* OnePlus 9 external-gauge telemetry. The resident loader owns the I2C bus. */
#include <kern/clock.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_time.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <sys/lemonade_battery.h>
#include <sys/sysctl.h>
#include <sys/systm.h>

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern void LemonadeBatteryReport(const void *, unsigned, int);
extern void redwood_tl(const char *, ...) __printflike(1, 2);

static volatile struct lemonade_battery_cache *shared;
static struct lemonade_battery_tracker tracker;
static int soc = -1, voltage_mv = -1, current_ma, temperature = -27315;
static int valid, external = -1, failures, last_error;
static int updates, stale = 1;
static int charge_state, battery_status = -1;

SYSCTL_NODE(_kern, OID_AUTO, lemonade_battery, CTLFLAG_RD | CTLFLAG_LOCKED, 0, "OnePlus 9 gauge telemetry");
#define BAT_INT(name, storage, description) \
    SYSCTL_INT(_kern_lemonade_battery, OID_AUTO, name, CTLFLAG_RD | CTLFLAG_LOCKED, &storage, 0, description)
BAT_INT(soc, soc, "real gauge percentage; -1 when unknown or stale");
BAT_INT(voltage_mv, voltage_mv, "series-pack voltage; -1 when stale");
BAT_INT(current_ma, current_ma, "signed average current, positive charging; check valid bit 2");
BAT_INT(temperature, temperature, "temperature in hundredths Celsius; -27315 when stale");
BAT_INT(valid, valid, "per-field validity mask; zero when stale");
BAT_INT(external, external, "external power present; -1 if unknown");
BAT_INT(updates, updates, "accepted measurements");
BAT_INT(failures, failures, "producer read failures");
BAT_INT(last_error, last_error, "producer last error");
BAT_INT(stale, stale, "no measurement within 15 seconds");
BAT_INT(charge_state, charge_state, "0 unknown, 1 charging, 2 discharging, 3 connected/not charging, 4 full");
BAT_INT(battery_status, battery_status, "raw gauge BatteryStatus; -1 when unavailable");

static int snapshot(struct lemonade_battery_cache *out)
{
    for (unsigned retry = 0; retry < 4; retry++) {
        uint32_t before = shared->sequence;
        if (before & 1) continue;
        __asm__ volatile("dmb ishld" ::: "memory");
        memcpy(out, (const void *)(uintptr_t)shared, sizeof(*out));
        __asm__ volatile("dmb ishld" ::: "memory");
        if (before == shared->sequence && before == out->sequence) return 1;
    }
    return 0;
}

static void battery_thread(__unused void *argument, __unused wait_result_t result)
{
    uint64_t frequency;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r" (frequency));
    for (;;) {
        struct lemonade_battery_cache cache;
        uint64_t ns, ticks;
        int accepted = 0;
        absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
        __asm__ volatile("mrs %0, cntvct_el0" : "=r" (ticks));
        if (snapshot(&cache)) {
            accepted = lemonade_battery_accept(&tracker, &cache, ns, ticks, frequency);
            if (cache.magic == LEMONADE_BATTERY_MAGIC && cache.version == LEMONADE_BATTERY_VERSION &&
                cache.size == sizeof(cache)) {
                failures = cache.failures; last_error = cache.last_error;
            }
        }
        if (accepted == 1) {
            const struct lemonade_battery_sample *s = &cache.sample;
            soc = (s->percent_hundredths + 50) / 100;
            voltage_mv = s->voltage_uv / 1000;
            current_ma = s->current_ua / 1000;
            temperature = s->temperature_hundredths;
            external = (s->valid & LEMONADE_BATTERY_EXTERNAL) ? s->external : -1;
            valid = s->valid; stale = 0; updates++;
            charge_state = lemonade_battery_charge_state(s);
            battery_status = (s->valid & LEMONADE_BATTERY_STATUS) ? s->battery_status : -1;
            LemonadeBatteryReport(s, sizeof(*s), 1);
            if (updates == 1 || updates % 3 == 0) {
                redwood_tl("lemonade-battery: sample=%llu SOC=%d%% V=%d mV I=%d mA T=%d/100C valid=%x failures=%d external=%d status=%x charge_state=%d\n",
                    cache.sample_id, soc, voltage_mv, current_ma, temperature, valid, failures,
                    external, battery_status, charge_state);
            }
        } else if (!stale && (accepted == -1 || !lemonade_battery_fresh(&tracker, ns))) {
            soc = -1; voltage_mv = -1; current_ma = 0; temperature = -27315;
            external = -1; valid = 0; stale = 1;
            charge_state = LEMONADE_CHARGE_UNKNOWN; battery_status = -1;
            LemonadeBatteryReport(NULL, 0, 0);
            redwood_tl("lemonade-battery: telemetry stale; withdrawing capacity\n");
        }
        delay_for_interval(1000, NSEC_PER_MSEC);
    }
}

void lemonade_battery_init(void)
{
    DTEntry entry;
    const void *value;
    unsigned size;
    int enabled = 0;
    uint64_t physical = 0;
    vm_offset_t mapping;
    thread_t thread;
    if (!PE_parse_boot_argn("lemonade_battery", &enabled, sizeof(enabled)) || enabled != 1) return;
    if (SecureDTLookupEntry(NULL, "/arm-io", &entry) != kSuccess ||
        SecureDTGetProperty(entry, "device_type", &value, &size) != kSuccess ||
        size != sizeof("lemonade") || memcmp(value, "lemonade", size)) return;
    /* UEFI can relocate the image at 4 KiB alignment. The cache fits in one 16 KiB page.
     * Only accept a physical RAM address in the platform's addressable range. */
    if (!PE_parse_boot_argn("lemonade_battery_pa", &physical, sizeof(physical)) ||
        !lemonade_battery_address_valid(physical)) {
        redwood_tl("lemonade-battery: absent or invalid loader cache address\n");
        return;
    }
    mapping = ml_io_map((vm_offset_t)(physical & ~UINT64_C(0x3fff)), 0x4000);
    if (!mapping) return;
    shared = (volatile struct lemonade_battery_cache *)(mapping + (physical & 0x3fff));
    redwood_tl("lemonade-battery: consuming external gauge telemetry, expiry 15 seconds\n");
    if (kernel_thread_start(battery_thread, NULL, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
