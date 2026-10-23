/*
 * OnePlus 9 (lemonade), Samsung S6SY792 on QUPv3 SE4 / I2C address 0x48.
 * Board wiring is from this LE2110's device tree; protocol references are
 * OnePlusOSS android_kernel_oneplus_sm8350 at 13ca08fdf0979fdd61d5e8991661874bb2d19150.
 * touchscreen=2 inspects only. All access is gated by the loader's platform.
 */
#include <kern/clock.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>
#include "lemonade_touch_protocol.h"

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);
extern void redwood_tl(const char *format, ...) __printflike(1, 2);

static vm_offset_t pins, gcc, se, rsc, database;
static uint32_t mode;
static const unsigned numbers[] = {20, 21, 22, 23, 74};
static uint32_t saved_cfg[5], saved_io[5];
static bool pins_changed;
static unsigned active, errors, reports, presses, releases, bad;
static uint32_t last_x[10], last_y[10];

static uint32_t rd(vm_offset_t address) { return *(volatile uint32_t *)address; }
static void wr(vm_offset_t address, uint32_t value) {
    *(volatile uint32_t *)address = value;
    __asm__ volatile("dsb sy" ::: "memory");
}
static void pause_us(unsigned us) { delay_for_interval(us, NSEC_PER_USEC); }
static void pause_ms(unsigned ms) { delay_for_interval(ms, NSEC_PER_MSEC); }
#define PIN_CFG(n) (pins + 0x1000 * (n))
#define PIN_IO(n)  (pins + 0x1000 * (n) + 4)
#define SDA 20
#define SCL 21
#define RESET 22
#define IRQ 23
#define ANALOG_ENABLE 74

static bool level(unsigned pin) { return (rd(PIN_IO(pin)) & 1) != 0; }
static void output(unsigned pin, bool high, unsigned pull, unsigned ma) {
    wr(PIN_IO(pin), high ? 2 : 0);
    wr(PIN_CFG(pin), pull | (ma / 2 - 1) << 6 | 1u << 9);
}
/* Open drain: high means input/released; neither I2C line is ever driven high. */
static void line(unsigned pin, bool high) {
    if (!high) { wr(PIN_IO(pin), 0); }
    wr(PIN_CFG(pin), 3 | (high ? 0 : 1u << 9));
}
static bool clock_high(void) {
    line(SCL, true);
    for (unsigned i = 0; i < 4000; i++) {
        if (level(SCL)) { return true; }
        pause_us(5);
    }
    return false;
}
static bool i2c_start(void) {
    line(SDA, true); pause_us(5);
    if (!clock_high() || !level(SDA)) { return false; }
    pause_us(5); line(SDA, false); pause_us(5); line(SCL, false);
    return true;
}
static void i2c_stop(void) {
    line(SCL, false); line(SDA, false); pause_us(5);
    (void)clock_high(); pause_us(5); line(SDA, true); pause_us(5);
}
static bool i2c_send(uint8_t byte) {
    for (int bit = 7; bit >= 0; bit--) {
        line(SDA, (byte >> bit) & 1); pause_us(5);
        if (!clock_high()) { return false; }
        pause_us(5); line(SCL, false);
    }
    line(SDA, true); pause_us(5);
    if (!clock_high()) { return false; }
    pause_us(5); bool ack = !level(SDA); line(SCL, false);
    return ack;
}
static bool i2c_receive(uint8_t *byte, bool ack) {
    unsigned value = 0;
    line(SDA, true);
    for (unsigned bit = 0; bit < 8; bit++) {
        pause_us(5);
        if (!clock_high()) { return false; }
        pause_us(5); value = (value << 1) | level(SDA); line(SCL, false);
    }
    line(SDA, !ack); pause_us(5);
    if (!clock_high()) { return false; }
    pause_us(5); line(SCL, false); line(SDA, true);
    *byte = value;
    return true;
}
static bool chip_read(uint8_t reg, uint8_t *bytes, unsigned count) {
    unsigned phase = 0;
    bool ok = i2c_start();
    if (ok) { phase = 1; ok = i2c_send(0x48 << 1); }
    if (ok) { phase = 2; ok = i2c_send(reg); }
    if (ok) { phase = 3; ok = i2c_start(); }
    if (ok) { phase = 4; ok = i2c_send((0x48 << 1) | 1); }
    if (ok) { phase = 5; }
    for (unsigned i = 0; ok && i < count; i++) { ok = i2c_receive(&bytes[i], i + 1 < count); }
    i2c_stop();
    if (!ok) {
        if (errors < 8) { redwood_tl("s6sy792: read %#x failed at phase %u; SDA%u=%u SCL%u=%u\n",
            reg, phase, SDA, level(SDA), SCL, level(SCL)); }
        errors++;
    }
    return ok;
}
/* Only volatile scan/event commands. No update, erase, calibration or flash writes. */
static bool chip_command(uint8_t reg) {
    if (reg != 0x10 && reg != 0x11 && reg != 0x62) { return false; }
    bool ok = i2c_start() && i2c_send(0x48 << 1) && i2c_send(reg);
    i2c_stop();
    if (!ok) { errors++; }
    return ok;
}

struct touch_db_slave { uint16_t id, header, data, count, version, reserved[3]; };
struct touch_db_header {
    uint32_t version;
    uint8_t magic[4];
    struct touch_db_slave slave[8];
    uint32_t checksum, reserved;
    uint8_t data[];
};
struct touch_db_entry { char id[8]; uint32_t priority[2], address; uint16_t length, offset; };

static uint32_t
supply_address(const char *name)
{
    const struct touch_db_header *db = (const void *)database;
    char id[8] = {0};
    strlcpy(id, name, sizeof(id));
    if (memcmp(db->magic, "\xdb\x30\x03\x0c", 4)) { return 0; }
    for (unsigned s = 0; s < 8; s++) {
        const struct touch_db_slave *slave = &db->slave[s];
        if (!slave->id || slave->count > 512 ||
            (uint32_t)slave->header + slave->count * sizeof(struct touch_db_entry) > 0x1f000) { continue; }
        const struct touch_db_entry *entry = (const void *)(db->data + slave->header);
        for (unsigned i = 0; i < slave->count; i++) {
            if (!memcmp(entry[i].id, id, sizeof(id))) { return entry[i].address; }
        }
    }
    return 0;
}

static bool supply_on(const char *name, unsigned millivolts) {
    uint32_t address = supply_address(name);
    vm_offset_t t = rsc + 0xd00;
    if (!address || ((address >> 16) & 15) != 4) { return false; }
    for (unsigned i = 0; !rd(t + 0x18); i++) {
        if (i == 1000) { return false; }
        pause_us(100);
    }
    if (rd(t + 4) & 1) {
        redwood_tl("s6sy792: RSC slot has a pending completion; supply request refused\n");
        return false;
    }
    uint32_t old_irq = rd(t), old_control = rd(t + 0x14), old_wait = rd(t + 0x10), old_enable = rd(t + 0x1c);
    uint32_t old_commands[3][3];
    const uint32_t offsets[] = {0, 8, 4}, values[] = {millivolts, 7, 1};
    for (unsigned i = 0; i < 3; i++) {
        for (unsigned j = 0; j < 3; j++) { old_commands[i][j] = rd(t + 0x30 + 20 * i + 4 * j); }
    }
    wr(t + 0x14, old_control & ~(1u << 24));
    wr(t + 0x14, old_control & ~((1u << 24) | (1u << 16)));
    wr(t + 8, 1);
    for (unsigned i = 0; i < 3; i++) {
        wr(t + 0x30 + 20 * i, 0x10108); /* four bytes, write, response */
        wr(t + 0x34 + 20 * i, address + offsets[i]);
        wr(t + 0x38 + 20 * i, values[i]);
    }
    wr(t + 0x10, 7); wr(t + 0x1c, 7); wr(t, old_irq | 1);
    wr(t + 0x14, 1u << 16); wr(t + 0x14, (1u << 16) | (1u << 24));
    bool done = false;
    for (unsigned i = 0; i < 2000; i++) {
        if (rd(t + 4) & 1) { done = true; break; }
        pause_us(100);
    }
    wr(t + 0x14, rd(t + 0x14) & ~(1u << 24));
    wr(t + 0x14, rd(t + 0x14) & ~(1u << 16));
    wr(t + 0x1c, 0); wr(t + 8, 1);
    for (unsigned i = 0; i < 3; i++) {
        for (unsigned j = 0; j < 3; j++) { wr(t + 0x30 + 20 * i + 4 * j, old_commands[i][j]); }
    }
    wr(t + 0x10, old_wait); wr(t + 0x1c, old_enable); wr(t, old_irq);
    /* Restoring an old trigger edge would replay the firmware's old requests. */
    wr(t + 0x14, old_control & ~(1u << 24));
    redwood_tl("s6sy792: %s %u mV request %s\n", name, millivolts, done ? "complete" : "timed out");
    return done;
}

static void release_all(void) {
    for (unsigned i = 0; i < 10; i++) {
        if (active & (1u << i)) {
            redwood_touch_post(REDWOOD_TOUCH_UP, i, last_x[i], last_y[i]); releases++;
        }
    }
    active = 0;
}
static void event_received(const uint8_t e[8]) {
    struct lemonade_contact c;
    if (e[0] == 0) { return; }
    reports++;
    if ((e[0] & 3) == 1) {
        unsigned type = (e[0] >> 2) & 15;
        if ((type == 1 && (e[1] == 1 || e[1] == 2)) || (type == 2 && e[1] == 0 && e[2] == 0x20)) {
            redwood_tl("s6sy792: controller status %02x %02x %02x; release contacts\n", e[0], e[1], e[2]);
            release_all(); (void)chip_command(0x62); (void)chip_command(0x10);
        }
        return;
    }
    if ((e[0] & 3) != 0) { return; }
    if (!lemonade_decode_contact(e, &c)) { bad++; return; }
    unsigned mask = 1u << c.finger;
    if (c.action == 3) {
        if (active & mask) {
            redwood_touch_post(REDWOOD_TOUCH_UP, c.finger, c.x, c.y);
            active &= ~mask; releases++;
        }
    } else {
        uint32_t type = (active & mask) ? REDWOOD_TOUCH_MOVE : REDWOOD_TOUCH_DOWN;
        last_x[c.finger] = c.x; last_y[c.finger] = c.y;
        active |= mask;
        if (type == REDWOOD_TOUCH_DOWN) { presses++; }
        redwood_touch_post(type, c.finger, c.x, c.y);
    }
}

/* Called only after the other XNU cores have stopped. Restore pin ownership. */
void lemonade_touch_quiesce(void) {
    if (!pins_changed) { return; }
    output(RESET, false, 3, 8);
    line(SDA, true); line(SCL, true);
    for (unsigned i = 0; i < sizeof(numbers) / sizeof(numbers[0]); i++) {
        wr(PIN_IO(numbers[i]), saved_io[i] & 2);
        wr(PIN_CFG(numbers[i]), saved_cfg[i]);
    }
    pins_changed = false;
    redwood_tl("s6sy792: stopped; pin configuration restored; reports %u down %u up %u active %#x I2C errors %u bad %u\n",
        reports, presses, releases, active, errors, bad);
}

static bool start_chip(void) {
    uint8_t id[3] = {0}, boot = 0, integrity = 0, version[4] = {0};
    for (unsigned i = 0; i < sizeof(numbers) / sizeof(numbers[0]); i++) {
        saved_cfg[i] = rd(PIN_CFG(numbers[i])); saved_io[i] = rd(PIN_IO(numbers[i]));
    }
    pins_changed = true;
    output(RESET, false, 3, 8);
    wr(PIN_CFG(IRQ), 3u << 6); /* input, no pull, 8 mA */
    line(SDA, true); line(SCL, true);
    if (!supply_on("ldoc8", 1800) || !supply_on("ldoc3", 3000)) { return false; }
    output(ANALOG_ENABLE, true, 0, 2);
    pause_ms(5); output(RESET, true, 3, 8); pause_ms(100);
    redwood_tl("s6sy792: open-drain GPIO I2C: SDA=%u SCL=%u IRQ=%u reset=%u\n",
        level(SDA), level(SCL), level(IRQ), level(RESET));
    bool identified = chip_read(0x22, id, sizeof(id));
    redwood_tl("s6sy792: device ID read %u: %02x %02x %02x\n", identified, id[0], id[1], id[2]);
    pause_ms(20);
    bool boot_ok = identified && chip_read(0x55, &boot, 1);
    redwood_tl("s6sy792: boot status read %u: %02x\n", boot_ok, boot);
    pause_ms(20);
    bool version_ok = boot_ok && chip_read(0xa5, version, sizeof(version));
    redwood_tl("s6sy792: firmware read %u: %02x%02x%02x%02x\n", version_ok, version[0], version[1], version[2], version[3]);
    pause_ms(20);
    bool integrity_ok = version_ok && chip_read(0x21, &integrity, 1);
    if (!identified || !boot_ok || !version_ok || !integrity_ok) {
        redwood_tl("s6sy792: chip identification read failed; scan not enabled\n");
        return false;
    }
    redwood_tl("s6sy792: ID %02x %02x %02x boot %02x integrity %02x firmware %02x%02x%02x%02x\n",
        id[0], id[1], id[2], boot, integrity, version[0], version[1], version[2], version[3]);
    const uint8_t expected[] = {0x28, 0x24, 0x01, 0x21}; /* exact Android baseline, read only */
    if (boot != 0x20 || !(integrity & 0x80) || memcmp(version, expected, 4)) {
        redwood_tl("s6sy792: identification differs from this device's baseline; no scan command\n");
        return false;
    }
    return chip_command(0x62) && chip_command(0x10);
}

static void poll_chip(void) {
    unsigned failures = 0;
    redwood_tl("s6sy792: physical touch ready, 1080x2400, 10 contacts; polling IRQ every 4 ms\n");
    for (unsigned tick = 0; ; tick++) {
        if (!level(IRQ)) {
            uint8_t events[32 * 8] = {0};
            if (!chip_read(0x60, events, 8)) {
                if (++failures >= 4) { release_all(); break; }
            } else {
                failures = 0;
                unsigned remaining = events[7] & 63;
                if (remaining > 31) {
                    bad++; release_all(); (void)chip_command(0x62);
                } else if (remaining && !chip_read(0x61, events + 8, remaining * 8)) {
                    release_all(); (void)chip_command(0x62);
                } else {
                    for (unsigned i = 0; i <= remaining; i++) { event_received(events + 8 * i); }
                }
            }
        }
        if (tick && tick % 2500 == 0) {
            redwood_tl("s6sy792: reports %u down %u up %u active %#x I2C errors %u bad %u IRQ %u\n",
                reports, presses, releases, active, errors, bad, level(IRQ));
        }
        pause_us(4000);
    }
    redwood_tl("s6sy792: repeated I2C failure; input stopped, contacts released\n");
}

static void
touch_thread(__unused void *arg, __unused wait_result_t result)
{
    pins = ml_io_map(0x0f100000, 0x80000);
    gcc = ml_io_map(0x00100000, 0x100000);
    rsc = ml_io_map(0x18220000, 0x10000);
    database = ml_io_map_wcomb(0x80860000, 0x20000);
    redwood_tl("s6sy792: lemonade mode %u; I2C SE4 at 0x990000 address 0x48\n", mode);
    for (unsigned i = 0; i < sizeof(numbers) / sizeof(numbers[0]); i++) {
        unsigned pin = numbers[i];
        redwood_tl("s6sy792: GPIO%u cfg %#x io %#x irq %#x\n", pin,
            rd(pins + pin * 0x1000), rd(pins + pin * 0x1000 + 4), rd(pins + pin * 0x1000 + 8));
    }
    uint32_t m = rd(gcc + 0x17004), s = rd(gcc + 0x17008), e = rd(gcc + 0x174cc);
    redwood_tl("s6sy792: GCC vote %#x clocks M/S/SE %#x %#x %#x source %#x %#x\n",
        rd(gcc + 0x52008), m, s, e, rd(gcc + 0x174d0), rd(gcc + 0x174d4));
    redwood_tl("s6sy792: supplies ldoc8 %#x ldoc3 %#x; RSC slot status %#x control %#x\n",
        supply_address("ldoc8"), supply_address("ldoc3"), rd(rsc + 0xd18), rd(rsc + 0xd14));
    redwood_tl("s6sy792: RSC IRQ enable %#x pending %#x\n", rd(rsc + 0xd00), rd(rsc + 0xd04));
    if (!((m | s | e) & 0x80000000)) {
        se = ml_io_map(0x990000, 0x4000);
        redwood_tl("s6sy792: SE firmware %#x interface %#x state %#x IO %#x\n",
            rd(se + 0x68), rd(se + 0x64), rd(se + 0x40), rd(se + 0x24));
    } else {
        redwood_tl("s6sy792: SE clock gated; no SE register access\n");
    }
    if (mode == 2) {
        redwood_tl("s6sy792: inspection complete; no hardware writes\n");
    } else if (start_chip()) {
        poll_chip();
    } else {
        redwood_tl("s6sy792: initialization failed; touch remains unavailable\n");
    }
    thread_terminate(current_thread());
}

void
lemonade_touch_init(void)
{
    DTEntry entry;
    const void *value;
    unsigned size;
    thread_t thread;
    if (!PE_parse_boot_argn("touchscreen", &mode, sizeof(mode)) || !mode) { return; }
    if (SecureDTLookupEntry(NULL, "/arm-io", &entry) != kSuccess ||
        SecureDTGetProperty(entry, "device_type", &value, &size) != kSuccess ||
        size != sizeof("lemonade") || memcmp(value, "lemonade", size)) { return; }
    if (kernel_thread_start(touch_thread, NULL, &thread) == KERN_SUCCESS) { thread_deallocate(thread); }
}
