/*
 * OnePlus 9 / SM8350 PCIe Wi-Fi bring-up. Android identifies Qualcomm
 * 17cb:1103 (CNSS QCA6490), revision 1, on PCIe root complex 0.
 * Register locations: the device FDT and OnePlusOSS SM8350 sources at
 * 13ca08fdf0979fdd61d5e8991661874bb2d19150: gcc-lahaina.c, lahaina-pcie.dtsi,
 * pci-msm.c. No network identity, calibration or credentials belong here.
 * Stages 2..7 are experimental bounded power/controller probes, no endpoint DMA.
 * lemonade_wifi=1 only reads; it never starts the redwood AHB/WPSS path.
 */
#include <kern/clock.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern void redwood_tl(const char *format, ...) __printflike(1, 2);
static vm_offset_t gcc, pins, parf, dbi, rsc, database;
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);

static uint32_t rd(vm_offset_t base, unsigned offset) {
    return *(volatile uint32_t *)(base + offset);
}
static void wr(vm_offset_t address, uint32_t value) {
    *(volatile uint32_t *)address = value;
    __asm__ volatile("dsb sy" ::: "memory");
}
static void pause_us(unsigned us) { delay_for_interval(us, NSEC_PER_USEC); }
static bool wait_mask(vm_offset_t base, unsigned offset, uint32_t mask, uint32_t value) {
    for (unsigned i = 0; i < 1000; i++) {
        if ((rd(base, offset) & mask) == value) { return true; }
        pause_us(10);
    }
    redwood_tl("wifi-lemonade: wait failed offset %#x mask %#x value %#x actual %#x\n",
        offset, mask, value, rd(base, offset));
    return false;
}
/* Only our masked GCC bits are restored; unrelated USB/display votes survive. */
static struct { unsigned offset; uint32_t mask, value; } saved_gcc[16];
static unsigned saved_count;
static struct { unsigned offset; uint32_t value; } saved_parf[8];
static unsigned parf_count;
static void change_parf(unsigned offset, uint32_t value) {
    unsigned i;
    for (i = 0; i < parf_count; i++) { if (saved_parf[i].offset == offset) { break; } }
    if (i == parf_count) {
        if (parf_count == sizeof(saved_parf) / sizeof(saved_parf[0])) { panic("Wi-Fi PARF journal full"); }
        saved_parf[i].offset = offset; saved_parf[i].value = rd(parf, offset); parf_count++;
    }
    wr(parf + offset, value);
}
static void change_gcc(unsigned offset, uint32_t mask, uint32_t value) {
    unsigned i;
    for (i = 0; i < saved_count; i++) { if (saved_gcc[i].offset == offset) { break; } }
    if (i == saved_count) {
        if (saved_count == sizeof(saved_gcc) / sizeof(saved_gcc[0])) { panic("Wi-Fi register journal full"); }
        saved_gcc[i].offset = offset; saved_gcc[i].mask = 0; saved_gcc[i].value = rd(gcc, offset);
        saved_count++;
    }
    saved_gcc[i].mask |= mask;
    wr(gcc + offset, (rd(gcc, offset) & ~mask) | (value & mask));
}
void lemonade_wifi_quiesce(void) {
    if (!saved_count && !parf_count) { return; }
    while (parf_count) {
        unsigned i = --parf_count;
        wr(parf + saved_parf[i].offset, saved_parf[i].value);
    }
    while (saved_count) {
        unsigned i = --saved_count;
        wr(gcc + saved_gcc[i].offset,
            (rd(gcc, saved_gcc[i].offset) & ~saved_gcc[i].mask) |
            (saved_gcc[i].value & saved_gcc[i].mask));
    }
    redwood_tl("wifi-lemonade: GCC votes restored; RPMh supply votes retained until reboot; DMA never enabled\n");
}
static bool platform_is_lemonade(void) {
    DTEntry entry;
    const void *value;
    unsigned size;
    return SecureDTLookupEntry(NULL, "/arm-io", &entry) == kSuccess &&
        SecureDTGetProperty(entry, "device_type", &value, &size) == kSuccess &&
        size == sizeof("lemonade") && !memcmp(value, "lemonade", size);
}
struct wifi_db_slave { uint16_t id, header, data, count, version, reserved[3]; };
struct wifi_db_header {
    uint32_t version;
    uint8_t magic[4];
    struct wifi_db_slave slave[8];
    uint32_t checksum, reserved;
    uint8_t data[];
};
struct wifi_db_entry { char id[8]; uint32_t priority[2], address; uint16_t length, offset; };

static uint32_t
supply_address(const char *name)
{
    const struct wifi_db_header *db = (const void *)database;
    char id[8] = {0};
    strlcpy(id, name, sizeof(id));
    if (memcmp(db->magic, "\xdb\x30\x03\x0c", 4)) { return 0; }
    for (unsigned s = 0; s < 8; s++) {
        const struct wifi_db_slave *slave = &db->slave[s];
        if (!slave->id || slave->count > 512 ||
            (uint32_t)slave->header + slave->count * sizeof(struct wifi_db_entry) > 0x1f000) { continue; }
        const struct wifi_db_entry *entry = (const void *)(db->data + slave->header);
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
    for (unsigned i = 0; !rd(t + 0x18, 0); i++) {
        if (i == 1000) { return false; }
        pause_us(100);
    }
    if (rd(t + 4, 0) & 1) {
        redwood_tl("wifi-lemonade: RSC slot has a pending completion; supply request refused\n");
        return false;
    }
    uint32_t old_irq = rd(t, 0), old_control = rd(t + 0x14, 0), old_wait = rd(t + 0x10, 0), old_enable = rd(t + 0x1c, 0);
    uint32_t old_commands[3][3];
    const uint32_t offsets[] = {0, 8, 4}, values[] = {millivolts, 7, 1};
    for (unsigned i = 0; i < 3; i++) {
        for (unsigned j = 0; j < 3; j++) { old_commands[i][j] = rd(t + 0x30 + 20 * i + 4 * j, 0); }
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
        if (rd(t + 4, 0) & 1) { done = true; break; }
        pause_us(100);
    }
    wr(t + 0x14, rd(t + 0x14, 0) & ~(1u << 24));
    wr(t + 0x14, rd(t + 0x14, 0) & ~(1u << 16));
    wr(t + 0x1c, 0); wr(t + 8, 1);
    for (unsigned i = 0; i < 3; i++) {
        for (unsigned j = 0; j < 3; j++) { wr(t + 0x30 + 20 * i + 4 * j, old_commands[i][j]); }
    }
    wr(t + 0x10, old_wait); wr(t + 0x1c, old_enable); wr(t, old_irq);
    /* Restoring an old trigger edge would replay the firmware's old requests. */
    wr(t + 0x14, old_control & ~(1u << 24));
    redwood_tl("wifi-lemonade: %s %u mV request %s\n", name, millivolts, done ? "complete" : "timed out");
    return done;
}

static bool inspect(void) {
    static const struct { const char *name; unsigned offset; } clocks[] = {
        {"gdsc", 0x6b004}, {"core_reset", 0x6b000}, {"phy_reset", 0x6c01c},
        {"vote0", 0x52000}, {"vote1", 0x52008}, {"collapse_vote", 0x52128},
        {"cfg_ahb", 0x6b024}, {"aux", 0x6b028}, {"mstr_axi", 0x6b01c},
        {"slv_axi", 0x6b014}, {"q2a_axi", 0x6b010}, {"pipe", 0x6b030},
        {"clkref", 0x8c004}, {"rchng", 0x6b038}, {"tbu", 0x9000c},
        {"ddrss", 0x8d080}, {"aggre0", 0x6b080}, {"aggre1", 0x8d084},
        {"aux_src", 0x6b058}, {"aux_cfg", 0x6b05c}, {"pipe_src", 0x6b070}
    };
    static const unsigned gpio[] = {64, 65, 94, 95, 96, 153};
    gcc = ml_io_map(0x100000, 0x100000);
    pins = ml_io_map(0xf100000, 0x100000);
    if (!gcc || !pins) { return false; }
    for (unsigned i = 0; i < sizeof(clocks) / sizeof(clocks[0]); i++) {
        redwood_tl("wifi-lemonade: GCC %s %#x = %#x\n", clocks[i].name,
            clocks[i].offset, rd(gcc, clocks[i].offset));
    }
    for (unsigned i = 0; i < sizeof(gpio) / sizeof(gpio[0]); i++) {
        redwood_tl("wifi-lemonade: GPIO%u cfg %#x io %#x irq %#x\n", gpio[i],
            rd(pins, gpio[i] * 0x1000), rd(pins, gpio[i] * 0x1000 + 4),
            rd(pins, gpio[i] * 0x1000 + 8));
    }
    /* A read of an unpowered interconnect can abort the CPU. */
    if (!(rd(gcc, 0x6b004) & (1u << 31)) || (rd(gcc, 0x6b000) & 1) ||
        ((rd(gcc, 0x6b024) | rd(gcc, 0x6b01c) | rd(gcc, 0x6b014)) & (1u << 31))) {
        redwood_tl("wifi-lemonade: PCIe power/clocks not ready; PARF/DBI/endpoint not accessed\n");
        return true;
    }
    parf = ml_io_map(0x1c00000, 0x3000);
    if (!parf) { return false; }
    redwood_tl("wifi-lemonade: PARF sys %#x pm %#x phy %#x ltssm %#x device_type %#x\n",
        rd(parf, 0), rd(parf, 0x20), rd(parf, 0x40), rd(parf, 0x1b0), rd(parf, 0x1000));
    redwood_tl("wifi-lemonade: inspection leaves DBI/endpoint untouched\n");
    return true;
}
static bool power_root(unsigned step) {
    /* Do not reset a root complex inherited in use. */
    if (!(rd(gcc, 0x6b024) & (1u << 31))) {
        redwood_tl("wifi-lemonade: root already clocked; refusing reset/power experiment\n");
        return false;
    }
    rsc = ml_io_map(0x18220000, 0x10000);
    database = ml_io_map_wcomb(0x80860000, 0x20000);
    if (!rsc || !database || !supply_on("ldob6", 1200) || !supply_on("ldob5", 880)) { return false; }
    redwood_tl("wifi-lemonade: stage 2 rails complete\n");
    if (step == 2) { return true; }
    change_gcc(0x52128, 1, 0); /* PCIe0's votable power domain */
    if (!wait_mask(gcc, 0x6b004, 1u << 31, 1u << 31)) { return false; }
    change_gcc(0x6b004, 1u << 11, 1u << 11); /* retain flip-flops */
    redwood_tl("wifi-lemonade: stage 3 power-domain complete\n");
    if (step == 3) { return true; }
    change_gcc(0x6b070, 3, 2); /* XO while PHY is stopped */
    change_gcc(0x52000, (1u << 11) | (1u << 12) | (1u << 18) | (1u << 19),
        (1u << 11) | (1u << 12) | (1u << 18) | (1u << 19));
    change_gcc(0x8c004, 1, 1); /* root refclk, present in device clock list */
    change_gcc(0x52008, 0x2f, 0x2f); /* cfg, master, slave, aux, q2a; no PIPE */
    if (!wait_mask(gcc, 0x6b024, 1u << 31, 0) || !wait_mask(gcc, 0x6b014, 1u << 31, 0)) { return false; }
    redwood_tl("wifi-lemonade: stage 4 clocks complete M/S/CFG %#x/%#x/%#x\n",
        rd(gcc, 0x6b01c), rd(gcc, 0x6b014), rd(gcc, 0x6b024));
    if (step == 4) { return true; }
    change_gcc(0x6b000, 1, 1); pause_us(1000); change_gcc(0x6b000, 1, 0);
    change_gcc(0x6c01c, 1, 1); pause_us(1000); change_gcc(0x6c01c, 1, 0);
    redwood_tl("wifi-lemonade: stage 5 resets complete\n");
    if (step == 5) { return true; }
    parf = ml_io_map(0x1c00000, 0x3000);
    dbi = ml_io_map(0x60000000, 0x2000);
    if (!parf || !dbi) { return false; }
    redwood_tl("wifi-lemonade: stage 6 reading PARF first %#x\n", rd(parf, 0));
    /* pci-msm.c initializes PARF routing BEFORE any DesignWare DBI access. */
    change_parf(0x1000, 4); /* root complex mode */
    change_parf(0x40, rd(parf, 0x40) & ~1u); /* PCIe clocks/reset path */
    change_parf(0x350, 0); /* DBI base */
    change_parf(0, 0x365e);
    change_parf(0x174, rd(parf, 0x174) | (1u << 4));
    change_parf(0x358, 0x4000000);
    change_parf(0x1a8, rd(parf, 0x1a8) | (1u << 31));
    redwood_tl("wifi-lemonade: stage 6 controller routing complete; DBI not yet read\n");
    if (step == 6) { return true; }
    uint32_t id = rd(dbi, 0);
    redwood_tl("wifi-lemonade: powered root id %#x command %#x link %#x PARF %#x\n",
        id, rd(dbi, 4), rd(dbi, 0x80), rd(parf, 0));
    return id == 0x010c17cb;
}
void lemonade_wifi_init(void) {
    unsigned mode = 0;
    if (!PE_parse_boot_argn("lemonade_wifi", &mode, sizeof(mode)) || !mode) { return; }
    if (!platform_is_lemonade()) {
        redwood_tl("wifi-lemonade: refused on another platform\n");
        return;
    }
    if (mode < 1 || mode > 7) {
        redwood_tl("wifi-lemonade: mode %u unsupported; no hardware access\n", mode);
        return;
    }
    if (mode >= 2) {
        unsigned touch = 0, gpu = 0, cpu = 0;
        PE_parse_boot_argn("touchscreen", &touch, sizeof(touch));
        PE_parse_boot_argn("gpu", &gpu, sizeof(gpu));
        PE_parse_boot_argn("cpufreq", &cpu, sizeof(cpu));
        if (touch || gpu || cpu) {
            redwood_tl("wifi-lemonade: power probe refused alongside touch/GPU/CPU-policy work\n");
            return;
        }
    }
    redwood_tl("wifi-lemonade: begin PCIe/CNSS survey mode %u\n", mode);
    bool ok = inspect();
    redwood_tl("wifi-lemonade: inspection %s; hardware writes=0; network unavailable\n", ok ? "complete" : "failed");
    if (ok && mode >= 2) {
        ok = power_root(mode);
        redwood_tl("wifi-lemonade: stage %u %s; endpoint/DMA/firmware not enabled\n", mode, ok ? "passed" : "failed");
        lemonade_wifi_quiesce();
    }
}
