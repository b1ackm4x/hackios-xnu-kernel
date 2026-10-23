/*
 * OnePlus 9 / SM8350 PCIe Wi-Fi bring-up. Android identifies Qualcomm
 * 17cb:1103 (CNSS QCA6490), revision 1, on PCIe root complex 0.
 * Register locations: the device FDT and OnePlusOSS SM8350 sources at
 * 13ca08fdf0979fdd61d5e8991661874bb2d19150: gcc-lahaina.c, lahaina-pcie.dtsi,
 * pci-msm.c. No network identity, calibration or credentials belong here.
 * Stages 2..13 are bounded power/controller/register probes, no endpoint DMA.
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
static vm_offset_t gcc, pins, parf, dbi, phy, rsc, database, config, bar;
static bool phy_started, perst_saved, radio_started, link_started, bar_saved;
static unsigned iatu_count;
static uint32_t perst_cfg, perst_io;
static uint32_t radio_cfg, radio_io, clkreq_cfg, clkreq_io, iatu_values[2][7];
static uint32_t bar0_old, bar1_old, endpoint_command_old;
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);

static uint32_t rd(vm_offset_t base, unsigned offset) {
    return *(volatile uint32_t *)(base + offset);
}
static void wr(vm_offset_t address, uint32_t value) {
    *(volatile uint32_t *)address = value;
    __asm__ volatile("dsb sy" ::: "memory");
}
static void wr16(vm_offset_t address, uint16_t value) {
    *(volatile uint16_t *)address = value;
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
static struct { unsigned offset; uint32_t value; } saved_parf[12];
static unsigned parf_count;
static struct { unsigned offset; uint32_t value; bool half; } saved_dbi[8];
static unsigned dbi_count;
static void change_dbi(unsigned offset, uint32_t value, bool half) {
    unsigned i;
    for (i = 0; i < dbi_count; i++) { if (saved_dbi[i].offset == offset) { break; } }
    if (i == dbi_count) {
        if (dbi_count == sizeof(saved_dbi) / sizeof(saved_dbi[0])) { panic("Wi-Fi DBI journal full"); }
        saved_dbi[i].offset = offset; saved_dbi[i].value = rd(dbi, offset); saved_dbi[i].half = half; dbi_count++;
    }
    if (half) { wr16(dbi + offset, value); } else { wr(dbi + offset, value); }
}
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
    if (!saved_count && !parf_count && !perst_saved) { return; }
    if (bar_saved) {
        if (rd(dbi, 0x80) & (1u << 29)) {
            wr16(config + 4, endpoint_command_old & ~6u);
            wr(config + 0x14, bar1_old); wr(config + 0x10, bar0_old);
            wr16(config + 4, endpoint_command_old);
            redwood_tl("wifi-lemonade: endpoint BAR and memory decode restored\n");
        } else { redwood_tl("wifi-lemonade: endpoint link lost during cleanup; PERST reset required\n"); }
        bar_saved = false;
    }
    while (iatu_count) {
        unsigned n = --iatu_count;
        vm_offset_t at = dbi + 0x1000 + 0x200 * n;
        wr(at + 4, 0);
        for (unsigned i = 0; i < 7; i++) { if (i != 1) { wr(at + 4 * i, iatu_values[n][i]); } }
        wr(at + 4, iatu_values[n][1]);
    }
    if (link_started) {
        wr(parf + 0x1b0, rd(parf, 0x1b0) & ~(1u << 8));
        wr(pins + 94 * 0x1000 + 4, rd(pins, 94 * 0x1000 + 4) & ~2u);
        pause_us(1000);
        link_started = false;
    }
    while (dbi_count) {
        unsigned i = --dbi_count;
        if (saved_dbi[i].half) { wr16(dbi + saved_dbi[i].offset, saved_dbi[i].value); }
        else { wr(dbi + saved_dbi[i].offset, saved_dbi[i].value); }
    }
    if (radio_started) {
        wr(pins + 64 * 0x1000 + 4, rd(pins, 64 * 0x1000 + 4) & ~2u);
        pause_us(1000);
    }
    if (phy_started) {
        wr(phy + 0x200, 1); /* PCS software reset */
        wr(phy + 0x244, 0); /* PCS stop */
        wr(phy + 0x240, 0); /* vendor PHY power-down */
        change_gcc(0x52008, 1u << 4, 0);
        phy_started = false;
    }
    /* The vendor switches PIPE back to XO before collapsing this domain. */
    if (parf_count) { change_gcc(0x6b054, 3, 2); }
    while (parf_count) {
        unsigned i = --parf_count;
        wr(parf + saved_parf[i].offset, saved_parf[i].value);
    }
    while (saved_count) {
        unsigned i = --saved_count;
        wr(gcc + saved_gcc[i].offset,
            (rd(gcc, saved_gcc[i].offset) & ~saved_gcc[i].mask) |
            (saved_gcc[i].value & saved_gcc[i].mask));
        if (saved_gcc[i].offset == 0x6b05c) {
            wr(gcc + 0x6b058, rd(gcc, 0x6b058) | 1);
            (void)wait_mask(gcc, 0x6b058, 1, 0);
        }
    }
    if (perst_saved) {
        wr(pins + 94 * 0x1000 + 4, perst_io);
        wr(pins + 94 * 0x1000, perst_cfg);
        perst_saved = false;
    }
    if (radio_started) {
        wr(pins + 64 * 0x1000 + 4, radio_io); wr(pins + 64 * 0x1000, radio_cfg);
        wr(pins + 95 * 0x1000 + 4, clkreq_io); wr(pins + 95 * 0x1000, clkreq_cfg);
        radio_started = false;
        redwood_tl("wifi-lemonade: WLAN_EN/CLKREQ restored; endpoint held off\n");
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
        {"aux_src", 0x6b058}, {"aux_cfg", 0x6b05c}, {"pipe_src", 0x6b054},
        {"rchng_src", 0x6b03c}, {"rchng_cfg", 0x6b040}
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
/* SM8350 QMP PCIe v3: board DT revision 21101, not the generic 10100 table.
 * Only non-unique controller initialization constants; no radio calibration. */
static const struct { unsigned offset; uint32_t value; } lemonade_pcie_phy[] = {
    {0x0240, 0x03},
    {0x0094, 0x08},
    {0x0154, 0x34},
    {0x016c, 0x08},
    {0x0058, 0x0f},
    {0x00a4, 0x42},
    {0x0110, 0x24},
    {0x011c, 0x03},
    {0x0118, 0xb4},
    {0x010c, 0x02},
    {0x01bc, 0x11},
    {0x00bc, 0x82},
    {0x00d4, 0x03},
    {0x00d0, 0x55},
    {0x00cc, 0x55},
    {0x00b0, 0x1a},
    {0x00ac, 0x0a},
    {0x00c4, 0x68},
    {0x00e0, 0x02},
    {0x00dc, 0xaa},
    {0x00d8, 0xab},
    {0x00b8, 0x34},
    {0x00b4, 0x14},
    {0x0158, 0x01},
    {0x0074, 0x06},
    {0x007c, 0x16},
    {0x0084, 0x36},
    {0x0078, 0x06},
    {0x0080, 0x16},
    {0x0088, 0x36},
    {0x01b0, 0x1e},
    {0x01ac, 0xca},
    {0x01b8, 0x18},
    {0x01b4, 0xa2},
    {0x0050, 0x07},
    {0x0010, 0x01},
    {0x001c, 0x31},
    {0x0020, 0x01},
    {0x0024, 0xde},
    {0x0028, 0x07},
    {0x0030, 0x4c},
    {0x0034, 0x06},
    {0x0ee4, 0x20},
    {0x0e84, 0x75},
    {0x0e90, 0x3f},
    {0x115c, 0x7f},
    {0x1160, 0xff},
    {0x1164, 0xbf},
    {0x1168, 0x3f},
    {0x116c, 0xd8},
    {0x1170, 0xdc},
    {0x1174, 0xdc},
    {0x1178, 0x5c},
    {0x117c, 0x34},
    {0x1180, 0xa6},
    {0x1190, 0x34},
    {0x10d8, 0x07},
    {0x0e3c, 0x1d},
    {0x0e40, 0x0c},
    {0x10dc, 0x00},
    {0x104c, 0x08},
    {0x1050, 0x08},
    {0x1044, 0xf0},
    {0x11a4, 0x38},
    {0x10cc, 0xf0},
    {0x0694, 0x00},
    {0x0654, 0x00},
    {0x06a8, 0x0f},
    {0x0048, 0x90},
    {0x0620, 0xc1},
    {0x0388, 0x77},
    {0x0398, 0x0b},
    {0x02dc, 0x05},
    {0x0200, 0x00},
    {0x0244, 0x03},
};
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
    change_gcc(0x6b054, 3, 0); /* gcc_parent_map_4: external PHY PIPE */
    /* AUX = XO / 1, no M/N mode; preserve other RCG fields. */
    change_gcc(0x6b05c, 0x371f, 1);
    wr(gcc + 0x6b058, rd(gcc, 0x6b058) | 1);
    if (!wait_mask(gcc, 0x6b058, 1, 0)) { return false; }
    change_gcc(0x52000, (1u << 11) | (1u << 12) | (1u << 18) | (1u << 19) | (1u << 22),
        (1u << 11) | (1u << 12) | (1u << 18) | (1u << 19) | (1u << 22));
    change_gcc(0x8c004, 1, 1); /* root refclk, present in device clock list */
    change_gcc(0x52008, 0x2f, 0x2f); /* cfg, master, slave, aux, q2a; no PIPE */
    if (!wait_mask(gcc, 0x6b024, 1u << 31, 0) || !wait_mask(gcc, 0x6b014, 1u << 31, 0) ||
        !wait_mask(gcc, 0x6b028, 1u << 31, 0) || !wait_mask(gcc, 0x6b038, 1u << 31, 0)) { return false; }
    redwood_tl("wifi-lemonade: stage 4 clocks complete M/S/CFG %#x/%#x/%#x\n",
        rd(gcc, 0x6b01c), rd(gcc, 0x6b014), rd(gcc, 0x6b024));
    if (step == 4) { return true; }
    perst_cfg = rd(pins, 94 * 0x1000); perst_io = rd(pins, 94 * 0x1000 + 4);
    perst_saved = true;
    wr(pins + 94 * 0x1000 + 4, perst_io & ~2u);
    wr(pins + 94 * 0x1000, (perst_cfg & ~0x3fcu) | (1u << 9));
    pause_us(1000); /* hold the endpoint in reset throughout these probes */
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
    phy = ml_io_map(0x1c06000, 0x2000);
    if (!phy) { return false; }
    redwood_tl("wifi-lemonade: stage 7 programming board PHY sequence\n");
    phy_started = true;
    for (unsigned i = 0; i < sizeof(lemonade_pcie_phy) / sizeof(lemonade_pcie_phy[0]); i++) {
        wr(phy + lemonade_pcie_phy[i].offset, lemonade_pcie_phy[i].value);
    }
    pause_us(1000);
    change_gcc(0x52008, 1u << 4, 1u << 4);
    if (!wait_mask(phy, 0x214, 1u << 6, 0)) { return false; }
    redwood_tl("wifi-lemonade: stage 7 PHY ready status %#x PIPE %#x; PERST held low\n", rd(phy, 0x214), rd(gcc, 0x6b030));
    if (step == 7) { return true; }
    uint32_t id = rd(dbi, 0);
    redwood_tl("wifi-lemonade: powered root id %#x command %#x link %#x PARF %#x\n",
        id, rd(dbi, 4), rd(dbi, 0x80), rd(parf, 0));
    if (id != 0x010c17cb) { return false; }
    if (step == 8) { return true; }
    /* CNSS QCA6490: keep BT unchanged; refuse to disturb an enabled radio. */
    if ((rd(pins, 64 * 0x1000 + 4) | rd(pins, 65 * 0x1000 + 4)) & 1) {
        redwood_tl("wifi-lemonade: radio already enabled; refusing shared power experiment\n");
        return false;
    }
    /* Board supply phandles -> RPMh resource names, from its regulator nodes. */
    if (!supply_on("smpb10", 1800) || !supply_on("ldoe7", 2800) ||
        !supply_on("smpe2", 950) || !supply_on("smpb11", 950) ||
        !supply_on("smpc1", 1880) || !supply_on("smpb12", 1350)) { return false; }
    radio_cfg = rd(pins, 64 * 0x1000); radio_io = rd(pins, 64 * 0x1000 + 4);
    clkreq_cfg = rd(pins, 95 * 0x1000); clkreq_io = rd(pins, 95 * 0x1000 + 4);
    radio_started = true;
    wr(pins + 95 * 0x1000, (clkreq_cfg & ~0x3ffu) | 7); /* alt1, pull-up, 2 mA input */
    pause_us(100000); /* required after BT_EN is observed low */
    wr(pins + 64 * 0x1000 + 4, radio_io | 2);
    wr(pins + 64 * 0x1000, (radio_cfg & ~0x3ffu) | 0x303); /* GPIO, pull-up, 10 mA output */
    pause_us(1000);
    if (!(rd(pins, 64 * 0x1000 + 4) & 1)) { return false; }
    redwood_tl("wifi-lemonade: stage 9 radio rails and WLAN_EN ready; PERST held low\n");
    if (step == 9) { return true; }
    /* Gen1 bring-up first. Status bits use W1C: modify only the low control half. */
    change_dbi(0xa0, (rd(dbi, 0xa0) & ~0xfu) | 1, true);
    change_dbi(0x80, rd(dbi, 0x80) & ~3u, true); /* no ASPM until power management exists */
    pause_us(10000);
    wr(pins + 94 * 0x1000 + 4, rd(pins, 94 * 0x1000 + 4) | 2);
    pause_us(1000);
    link_started = true;
    change_parf(0x1b0, rd(parf, 0x1b0) | (1u << 8));
    bool linked = false;
    for (unsigned i = 0; i < 100; i++) {
        if ((rd(dbi, 0xf28) & (1u << 10)) && (rd(dbi, 0x80) & (1u << 29))) { linked = true; break; }
        pause_us(5000);
    }
    redwood_tl("wifi-lemonade: link %s ELBI %#x status %#x LTSSM %#x\n",
        linked ? "up" : "timeout", rd(dbi, 0xf28), rd(dbi, 0x80), rd(parf, 0x1b0));
    if (!linked) { return false; }
    redwood_tl("wifi-lemonade: stage 10 PCIe link ready; DMA disabled\n");
    if (step == 10) { return true; }
    config = ml_io_map(0x60100000, 0x1000);
    if (!config || (rd(dbi, 0x1004) & (1u << 31))) { return false; }
    change_dbi(0x18, (rd(dbi, 0x18) & 0xff000000u) | 0x010100, false);
    for (unsigned i = 0; i < 7; i++) { iatu_values[0][i] = rd(dbi, 0x1000 + 4 * i); }
    iatu_count = 1;
    wr(dbi + 0x1004, 0);
    wr(dbi + 0x1000, 4); /* outbound CFG0, bus 1/device 0/function 0 */
    wr(dbi + 0x1008, 0x60100000); wr(dbi + 0x100c, 0);
    wr(dbi + 0x1010, 0x60100fff); wr(dbi + 0x1014, 0x01000000); wr(dbi + 0x1018, 0);
    wr(dbi + 0x1004, 1u << 31);
    if (!wait_mask(dbi, 0x1004, 1u << 31, 1u << 31)) { return false; }
    pause_us(1000);
    uint32_t endpoint = rd(config, 0), command = rd(config, 4), revision = rd(config, 8);
    redwood_tl("wifi-lemonade: endpoint id %#x command %#x class/revision %#x\n", endpoint, command, revision);
    if (endpoint != 0x110317cb || (command & 4)) { return false; }
    if (step == 11) { return true; }
    if (rd(dbi, 0x1204) & (1u << 31)) { return false; }
    bar = ml_io_map(0x60400000, 0x200000);
    if (!bar) { return false; }
    bar0_old = rd(config, 0x10); bar1_old = rd(config, 0x14); endpoint_command_old = command & 0xffff;
    if ((bar0_old & 7) != 4) { /* Known board BAR0 is 64-bit memory. */
        redwood_tl("wifi-lemonade: unexpected BAR type %#x; mapping refused\n", bar0_old & 15);
        return false;
    }
    bar_saved = true;
    wr(config + 0x10, 0x60400000 | (bar0_old & 15)); wr(config + 0x14, 0);
    change_dbi(0x20, 0x60506040, false); /* bridge window 0x60400000..0x605fffff */
    change_dbi(4, (rd(dbi, 4) & ~4u) | 2, true); /* memory decode, no bus master */
    for (unsigned i = 0; i < 7; i++) { iatu_values[1][i] = rd(dbi, 0x1200 + 4 * i); }
    iatu_count = 2;
    wr(dbi + 0x1204, 0); wr(dbi + 0x1200, 0); /* outbound MEM */
    wr(dbi + 0x1208, 0x60400000); wr(dbi + 0x120c, 0); wr(dbi + 0x1210, 0x605fffff);
    wr(dbi + 0x1214, 0x60400000); wr(dbi + 0x1218, 0); wr(dbi + 0x1204, 1u << 31);
    if (!wait_mask(dbi, 0x1204, 1u << 31, 1u << 31)) { return false; }
    wr16(config + 4, (endpoint_command_old & ~4u) | 2);
    pause_us(1000);
    if (rd(config, 4) & 4) { return false; }
    uint32_t hw = rd(bar, 0x224), length = rd(bar, 0), version = rd(bar, 8);
    uint32_t bhi = rd(bar, 0x28), bhie = rd(bar, 0x2c), status = rd(bar, 0x48);
    redwood_tl("wifi-lemonade: BAR TCSR version %#x (major %u minor %#x) MHI len %#x version %#x status %#x BHI %#x BHIE %#x\n",
        hw, (hw >> 8) & 15, hw & 255, length, version, status, bhi, bhie);
    if (hw == 0 || hw == ~0u || !length || length == ~0u || !bhi || bhi > 0x1fff80 || (bhi & 3)) { return false; }
    /* Read only non-identifying boot state, never BHI serial/key-hash registers. */
    uint32_t ee = rd(bar, bhi + 0x28), boot_status = rd(bar, bhi + 0x2c);
    redwood_tl("wifi-lemonade: BHI execution environment %u status %#x; no firmware transfer\n", ee, boot_status);
    if (ee > 6) { return false; }
    redwood_tl("wifi-lemonade: stage 12 BAR/MHI registers readable; endpoint bus master disabled\n");
    if (step == 12) { return true; }
    vm_offset_t smmu = ml_io_map(0x15000000, 0x2000);
    if (!smmu) { return false; }
    uint32_t scr0 = rd(smmu, 0), idr0 = rd(smmu, 0x20), idr1 = rd(smmu, 0x24);
    unsigned matches = idr0 & 255, valid = 0, covering = 0;
    if (scr0 == ~0u || !matches || matches > 255) { return false; }
    redwood_tl("wifi-lemonade: Apps SMMU control %#x idr0 %#x idr1 %#x\n", scr0, idr0, idr1);
    for (unsigned i = 0; i < matches; i++) {
        uint32_t smr = rd(smmu, 0x800 + 4 * i), s2cr = rd(smmu, 0xc00 + 4 * i);
        bool wide = (scr0 & (1u << 3)) != 0;
        if (!(wide ? s2cr & (1u << 10) : smr & (1u << 31))) { continue; }
        valid++;
        uint32_t stream = smr & (wide ? 0xffff : 0x7fff), mask = (smr >> 16) & (wide ? 0xffff : 0x7fff);
        if (((stream ^ 0x1c01) & ~mask) == 0) {
            covering++;
            redwood_tl("wifi-lemonade: SMMU match %u stream %#x mask %#x s2cr %#x\n", i, stream, mask, s2cr);
        }
    }
    redwood_tl("wifi-lemonade: stage 13 SMMU survey complete; %u/%u matches used, %u cover endpoint; SMMU writes=0\n", valid, matches, covering);
    return true;
}
void lemonade_wifi_init(void) {
    unsigned mode = 0;
    if (!PE_parse_boot_argn("lemonade_wifi", &mode, sizeof(mode)) || !mode) { return; }
    if (!platform_is_lemonade()) {
        redwood_tl("wifi-lemonade: refused on another platform\n");
        return;
    }
    if (mode < 1 || mode > 13) {
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
        redwood_tl("wifi-lemonade: stage %u %s; DMA/firmware not enabled\n", mode, ok ? "passed" : "failed");
        lemonade_wifi_quiesce();
    }
}
