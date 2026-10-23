/*
 * OnePlus 9 / SM8350 PCIe Wi-Fi bring-up. Android identifies Qualcomm
 * 17cb:1103 (CNSS QCA6490), revision 1, on PCIe root complex 0.
 * Register locations: the device FDT and OnePlusOSS SM8350 sources at
 * 13ca08fdf0979fdd61d5e8991661874bb2d19150: gcc-lahaina.c, lahaina-pcie.dtsi,
 * pci-msm.c. No network identity, calibration or credentials belong here.
 * Stages 2..15 keep endpoint DMA off; 16 verifies an unmapped DMA guard;
 * 17 transfers only the original firmware's verified BHI prefix into RAM.
 * lemonade_wifi=1 only reads; it never starts the redwood AHB/WPSS path.
 */
#include <kern/clock.h>
#include <kern/locks.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <mach/vm_param.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>
#include <vm/memory_types.h>
#include <vm/vm_kern.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern void redwood_tl(const char *format, ...) __printflike(1, 2);
static vm_offset_t gcc, pins, parf, dbi, phy, rsc, database, config, bar;
static bool phy_started, perst_saved, radio_started, link_started, bar_saved;
static unsigned iatu_count;
static uint32_t perst_cfg, perst_io;
static uint32_t radio_cfg, radio_io, clkreq_cfg, clkreq_io, iatu_values[2][7];
static uint32_t bar0_old, bar1_old, endpoint_command_old;
static bool sid_saved, bus_master_was_enabled;
static uint32_t sid_table_old[256], sid_cfg_old;
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern uint64_t kvtophys(vm_offset_t va);
extern lck_mtx_t redwood_smmu_lock;
static void dma_restore(void);

/* Qualcomm's hypervisor emulates some SMMU registers. Use simple register
 * operands, as the existing Adreno driver does; never STR WZR or indexed STR. */
static uint32_t __attribute__((noinline)) rd(vm_offset_t base, unsigned offset) {
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(base + offset) : "memory");
    return value;
}
static void __attribute__((noinline)) wr(vm_offset_t address, uint32_t value) {
    __asm__ volatile("str %w0, [%1]" : : "r"(value), "r"(address) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
}
static void __attribute__((noinline)) wr16(vm_offset_t address, uint16_t value) {
    __asm__ volatile("strh %w0, [%1]" : : "r"(value), "r"(address) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
}
static void __attribute__((noinline)) wr64(vm_offset_t address, uint64_t value) {
    __asm__ volatile("str %0, [%1]" : : "r"(value), "r"(address) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
}
static uint64_t __attribute__((noinline)) rd64(vm_offset_t address) {
    uint64_t value;
    __asm__ volatile("ldr %0, [%1]" : "=r"(value) : "r"(address) : "memory");
    return value;
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
            if (bus_master_was_enabled) {
                redwood_tl("wifi-lemonade: endpoint bus master clear %s\n", (rd(config, 4) & 4) ? "failed" : "verified");
            }
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
    dma_restore(); /* endpoint is in reset before any translation is removed */
    if (sid_saved) {
        for (unsigned i = 0; i < 256; i++) { wr(parf + 0x2000 + 4 * i, sid_table_old[i]); }
        wr(parf + 0x2c00, sid_cfg_old);
        sid_saved = false;
        redwood_tl("wifi-lemonade: BDF/SID table restored after endpoint reset\n");
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
    redwood_tl("wifi-lemonade: GCC votes restored; RPMh supply votes retained until reboot; %s\n",
        bus_master_was_enabled ? "endpoint DMA stopped before unmap" : "DMA never enabled");
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
/* CRC-8, polynomial 0x07, over the requester ID in network byte order. */
static uint8_t requester_hash(uint16_t requester) {
    uint8_t crc = 0;
    for (unsigned byte = 0; byte < 2; byte++) {
        crc ^= (requester >> (byte == 0 ? 8 : 0)) & 255;
        for (unsigned bit = 0; bit < 8; bit++) {
            crc = (crc << 1) ^ ((crc & 0x80) ? 0x07 : 0);
        }
    }
    return crc;
}
static bool route_endpoint(void) {
    if ((rd(config, 4) | rd(dbi, 4)) & 4) { return false; }
    unsigned hash = requester_hash(0x0100);
    if (!hash) { return false; } /* root BDF zero occupies hash zero */
    sid_cfg_old = rd(parf, 0x2c00);
    for (unsigned i = 0; i < 256; i++) { sid_table_old[i] = rd(parf, 0x2000 + 4 * i); }
    sid_saved = true;
    /* This root was reset and has just one endpoint, still unable to master. */
    for (unsigned i = 0; i < 256; i++) { wr(parf + 0x2000 + 4 * i, 0); }
    wr(parf + 0x2000 + 4 * hash, 0x01000100); /* BDF 01:00.0 -> relative SID 1 */
    wr(parf + 0x2c00, sid_cfg_old & ~1u);
    for (unsigned i = 0; i < 256; i++) {
        if (rd(parf, 0x2000 + 4 * i) != (i == hash ? 0x01000100u : 0)) { return false; }
    }
    if (rd(parf, 0x2c00) & 1) { return false; }
    redwood_tl("wifi-lemonade: stage 14 BDF 0x100 -> SID 0x1c01 hash %#x table verified; bus master disabled\n", hash);
    return true;
}

/* A small, privately owned stage-1 address space: no identity mapping, no MSI
 * page and no firmware-owned context. The first probe leaves PCI bus mastering
 * off. IDR0.ATOSNS means standard translation requests are NOT supported;
 * software table validation is kept distinct from a real endpoint DMA test. */
#define WIFI_DMA_IOVA 0xa0004000ull
#define WIFI_DMA_SIZE 0x80000u
#define WIFI_PHYS_MASK 0x0000fffffffff000ull
#define WIFI_FSR_FAULT 0xc00001feu /* excludes read-only translation-format bits [10:9] */
static const unsigned dma_cb_offsets[] = {0, 0x10, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x38, 0x3c};
static struct {
    vm_offset_t mmio, cb, tables, data;
    unsigned match, context;
    uint32_t smr, s2cr, cbar, cba2r, cb_old[9];
    bool owned, retain_pages;
} dma_space;

static void dma_flush(vm_offset_t start, unsigned size) {
    for (vm_offset_t line = start; line < start + size; line += 64) {
        __asm__ volatile("dc civac, %0" : : "r"(line) : "memory");
    }
    __asm__ volatile("dsb sy" ::: "memory");
}
static bool dma_tlb_sync(void) {
    wr(dma_space.cb + 0x610, dma_space.context);
    wr(dma_space.cb + 0x7f0, 0);
    return wait_mask(dma_space.cb, 0x7f4, 1, 0);
}
static void dma_restore(void) {
    if (dma_space.retain_pages) { return; }
    if (dma_space.owned) {
        lck_mtx_lock(&redwood_smmu_lock);
        /* Keep the stream faulting while its context is dismantled. */
        wr(dma_space.mmio + 0xc00 + 4 * dma_space.match, 2u << 16);
        wr(dma_space.cb, 0);
        if (!dma_tlb_sync()) {
            dma_space.retain_pages = true;
            lck_mtx_unlock(&redwood_smmu_lock);
            redwood_tl("wifi-lemonade: DMA cleanup failed; tables retained with endpoint reset\n");
            return;
        }
        if (rd(dma_space.cb, 0x58) & WIFI_FSR_FAULT) { wr(dma_space.cb + 0x58, rd(dma_space.cb, 0x58) & WIFI_FSR_FAULT); }
        for (unsigned i = 1; i < 9; i++) { wr(dma_space.cb + dma_cb_offsets[i], dma_space.cb_old[i]); }
        wr(dma_space.mmio + 0x1000 + 4 * dma_space.context, dma_space.cbar);
        wr(dma_space.mmio + 0x1800 + 4 * dma_space.context, dma_space.cba2r);
        wr(dma_space.cb, dma_space.cb_old[0]);
        wr(dma_space.mmio + 0x800 + 4 * dma_space.match, dma_space.smr);
        wr(dma_space.mmio + 0xc00 + 4 * dma_space.match, dma_space.s2cr);
        uint32_t cbar_after = rd(dma_space.mmio, 0x1000 + 4 * dma_space.context);
        bool cbar_ok = cbar_after == dma_space.cbar;
        /* The hypervisor refuses a return to the unused stage-2 reset type.
         * Linux teardown also leaves CBAR: with no stream, SCTLR.M=0 and the
         * old TTBRs restored, this inactive stage-1 type owns no memory. */
        if (!cbar_ok && dma_space.cbar == 0 && cbar_after == 0x1f300 &&
            !(rd(dma_space.cb, 0) & 1) && !(dma_space.smr & (1u << 31))) {
            cbar_ok = true;
            redwood_tl("wifi-lemonade: inactive CBAR stage-1 type retained until reboot; stream invalid and context disabled\n");
        }
        bool restored = cbar_ok && rd(dma_space.mmio, 0x800 + 4 * dma_space.match) == dma_space.smr &&
            rd(dma_space.mmio, 0xc00 + 4 * dma_space.match) == dma_space.s2cr &&
            rd(dma_space.mmio, 0x1800 + 4 * dma_space.context) == dma_space.cba2r;
        for (unsigned i = 0; i < 9; i++) {
            uint32_t actual = rd(dma_space.cb, dma_cb_offsets[i]);
            if (actual != dma_space.cb_old[i]) {
                redwood_tl("wifi-lemonade: restore CB offset %#x expected %#x actual %#x\n", dma_cb_offsets[i], dma_space.cb_old[i], actual);
                restored = false;
            }
        }
        if (!restored) {
            redwood_tl("wifi-lemonade: restore globals SMR %#x/%#x S2CR %#x/%#x CBAR %#x/%#x CBA2R %#x/%#x (actual/old)\n",
                rd(dma_space.mmio, 0x800 + 4 * dma_space.match), dma_space.smr,
                rd(dma_space.mmio, 0xc00 + 4 * dma_space.match), dma_space.s2cr,
                rd(dma_space.mmio, 0x1000 + 4 * dma_space.context), dma_space.cbar,
                rd(dma_space.mmio, 0x1800 + 4 * dma_space.context), dma_space.cba2r);
        }
        dma_space.owned = false;
        lck_mtx_unlock(&redwood_smmu_lock);
        redwood_tl("wifi-lemonade: DMA context/stream release %s; translation registers restored after endpoint reset\n", restored ? "verified" : "failed");
        if (!restored) { dma_space.retain_pages = true; return; }
    }
    if (dma_space.tables) { kmem_free(kernel_map, dma_space.tables, round_page(3 * 4096)); dma_space.tables = 0; }
    if (dma_space.data) { kmem_free(kernel_map, dma_space.data, round_page(WIFI_DMA_SIZE)); dma_space.data = 0; }
}
static bool dma_translate(uint64_t address, uint64_t *result) {
    wr64(dma_space.cb + 0x800, address);
    if (!wait_mask(dma_space.cb, 0x8f0, 1, 0)) { return false; }
    *result = rd64(dma_space.cb + 0x50);
    return true;
}
static bool dma_walk(uint64_t address, uint64_t *physical) {
    if (address >> 39) { return false; }
    uint64_t *l1 = (void *)dma_space.tables, *l2 = (void *)(dma_space.tables + 4096), *l3 = (void *)(dma_space.tables + 8192);
    if ((l1[(address >> 30) & 511] & (WIFI_PHYS_MASK | 3)) != (kvtophys((vm_offset_t)l2) | 3) ||
        (l2[(address >> 21) & 511] & (WIFI_PHYS_MASK | 3)) != (kvtophys((vm_offset_t)l3) | 3)) { return false; }
    uint64_t pte = l3[(address >> 12) & 511];
    if ((pte & 3) != 3) { return false; }
    *physical = (pte & WIFI_PHYS_MASK) | (address & 4095);
    return true;
}
static bool dma_prepare(void) {
    if ((rd(config, 4) | rd(dbi, 4)) & 4) { return false; }
    dma_space.mmio = ml_io_map(0x15000000, 0x100000);
    if (!dma_space.mmio) { return false; }
    uint32_t scr = rd(dma_space.mmio, 0), id0 = rd(dma_space.mmio, 0x20), id1 = rd(dma_space.mmio, 0x24);
    unsigned matches = id0 & 255, contexts = id1 & 255, s2contexts = (id1 >> 16) & 255;
    /* This probe supports only the exact MMU-500 register geometry observed. */
    if ((scr & 9) || !(scr & (1u << 10)) || !(id0 & (1u << 30)) ||
        (id1 & 0xf0000000u) != 0x60000000u || matches > 255 || !matches || contexts > 128 || !contexts ||
        rd(dma_space.mmio, 0x48)) { return false; }
    if (kmem_alloc(kernel_map, &dma_space.tables, round_page(3 * 4096), KMA_KOBJECT | KMA_ZERO | KMA_DATA,
        VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS ||
        kmem_alloc(kernel_map, &dma_space.data, round_page(WIFI_DMA_SIZE), KMA_KOBJECT | KMA_ZERO | KMA_DATA,
        VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) { return false; }
    uint64_t *l1 = (void *)dma_space.tables, *l2 = (void *)(dma_space.tables + 4096), *l3 = (void *)(dma_space.tables + 8192);
    l1[(WIFI_DMA_IOVA >> 30) & 511] = kvtophys((vm_offset_t)l2) | 3;
    l2[(WIFI_DMA_IOVA >> 21) & 511] = kvtophys((vm_offset_t)l3) | 3;
    for (unsigned offset = 0; offset < WIFI_DMA_SIZE; offset += 4096) {
        uint64_t physical = kvtophys(dma_space.data + offset);
        if (!physical || physical >> 36 || physical & 4095) { return false; }
        l3[((WIFI_DMA_IOVA + offset) >> 12) & 511] = physical |
            3ull | (1ull << 6) | (2ull << 8) | (1ull << 10) | (1ull << 11) | (1ull << 53) | (1ull << 54);
    }
    dma_flush(dma_space.tables, 3 * 4096);
    dma_flush(dma_space.data, WIFI_DMA_SIZE);
    lck_mtx_lock(&redwood_smmu_lock);
    bool used[128] = {false}, collision = false;
    dma_space.match = matches; dma_space.context = contexts;
    for (unsigned i = 0; i < matches; i++) {
        uint32_t smr = rd(dma_space.mmio, 0x800 + 4 * i), s2cr = rd(dma_space.mmio, 0xc00 + 4 * i);
        if (!(smr & (1u << 31))) { if (dma_space.match == matches) { dma_space.match = i; } continue; }
        if (((smr ^ 0x1c01) & ~((smr >> 16) & 0x7fff) & 0x7fff) == 0) { collision = true; }
        if (((s2cr >> 16) & 3) == 0 && (s2cr & 255) < 128) { used[s2cr & 255] = true; }
    }
    for (unsigned i = s2contexts; i < contexts; i++) {
        vm_offset_t cb = dma_space.mmio + 0x80000 + 4096 * i;
        if (!used[i] && !(rd(cb, 0) & 1) && !(rd(cb, 0x58) & WIFI_FSR_FAULT)) { dma_space.context = i; dma_space.cb = cb; break; }
    }
    if (collision || dma_space.match == matches || dma_space.context == contexts) {
        lck_mtx_unlock(&redwood_smmu_lock); return false;
    }
    unsigned match = dma_space.match, context = dma_space.context;
    dma_space.smr = rd(dma_space.mmio, 0x800 + 4 * match); dma_space.s2cr = rd(dma_space.mmio, 0xc00 + 4 * match);
    dma_space.cbar = rd(dma_space.mmio, 0x1000 + 4 * context); dma_space.cba2r = rd(dma_space.mmio, 0x1800 + 4 * context);
    for (unsigned i = 0; i < 9; i++) { dma_space.cb_old[i] = rd(dma_space.cb, dma_cb_offsets[i]); }
    dma_space.owned = true; /* journal is complete before the first write */
    redwood_tl("wifi-lemonade: claiming SMMU match %u context %u for SID 0x1c01 only\n", match, context);
    wr(dma_space.cb, 0);
    wr(dma_space.mmio + 0x1800 + 4 * context, 1);
    wr(dma_space.mmio + 0x1000 + 4 * context, (1u << 16) | (3u << 8) | (15u << 12));
    wr(dma_space.cb + 0x10, 1u | (1u << 4) | (7u << 15));
    wr(dma_space.cb + 0x30, 25u | (1u << 8) | (1u << 10) | (3u << 12) | (1u << 23));
    uint64_t root = kvtophys(dma_space.tables) | ((uint64_t)context << 48);
    wr(dma_space.cb + 0x20, root); wr(dma_space.cb + 0x24, root >> 32);
    wr(dma_space.cb + 0x28, 0); wr(dma_space.cb + 0x2c, 0);
    wr(dma_space.cb + 0x38, 0xf404ff44); wr(dma_space.cb + 0x3c, 0);
    bool ok = dma_tlb_sync();
    if (ok) {
        wr(dma_space.cb, 0x1127);
        wr(dma_space.mmio + 0xc00 + 4 * match, context);
        wr(dma_space.mmio + 0x800 + 4 * match, 0x80001c01);
        ok = rd(dma_space.mmio, 0x800 + 4 * match) == 0x80001c01 &&
            (rd(dma_space.mmio, 0xc00 + 4 * match) & 0x300ff) == context &&
            (rd(dma_space.cb, 0) & 1) && rd(dma_space.cb, 0x20) == (uint32_t)root &&
            rd(dma_space.cb, 0x24) == (uint32_t)(root >> 32);
    }
    lck_mtx_unlock(&redwood_smmu_lock);
    redwood_tl("wifi-lemonade: SMMU readback SMR %#x S2CR %#x CBAR %#x CBA2R %#x SCTLR %#x TCR %#x TCR2 %#x TTBR %#x:%#x expected %#llx\n",
        rd(dma_space.mmio, 0x800 + 4 * match), rd(dma_space.mmio, 0xc00 + 4 * match),
        rd(dma_space.mmio, 0x1000 + 4 * context), rd(dma_space.mmio, 0x1800 + 4 * context),
        rd(dma_space.cb, 0), rd(dma_space.cb, 0x30), rd(dma_space.cb, 0x10),
        rd(dma_space.cb, 0x24), rd(dma_space.cb, 0x20), root);
    if (!ok) { return false; }
    unsigned verified = 0;
    for (unsigned offset = 0; offset < WIFI_DMA_SIZE; offset += 4096) {
        uint64_t translated = 0;
        if (!dma_walk(WIFI_DMA_IOVA + offset, &translated) || translated != kvtophys(dma_space.data + offset)) {
            redwood_tl("wifi-lemonade: table page %u failed\n", offset / 4096);
            return false;
        }
        verified++;
    }
    uint64_t before = 0, after = 0;
    if (dma_walk(WIFI_DMA_IOVA - 4096, &before) || dma_walk(WIFI_DMA_IOVA + WIFI_DMA_SIZE, &after)) { return false; }
    if (!(id0 & (1u << 26))) {
        for (unsigned offset = 0; offset < WIFI_DMA_SIZE; offset += 4096) {
            uint64_t translated = 0;
            if (!dma_translate(WIFI_DMA_IOVA + offset, &translated) || (translated & 1) ||
                (translated & WIFI_PHYS_MASK) != kvtophys(dma_space.data + offset)) { return false; }
        }
        if (!dma_translate(WIFI_DMA_IOVA - 4096, &before) || !dma_translate(WIFI_DMA_IOVA + WIFI_DMA_SIZE, &after) ||
            !(before & 1) || !(after & 1)) { return false; }
        redwood_tl("wifi-lemonade: optional ATOS hardware translation checks passed\n");
    } else {
        redwood_tl("wifi-lemonade: ATOS unsupported (IDR0.ATOSNS=1); table walk is software validation only\n");
    }
    redwood_tl("wifi-lemonade: SMMU pre-DMA raw FSR %#x global %#x\n", rd(dma_space.cb, 0x58), rd(dma_space.mmio, 0x48));
    if ((rd(dma_space.cb, 0x58) & WIFI_FSR_FAULT) || rd(dma_space.mmio, 0x48)) { return false; }
    redwood_tl("wifi-lemonade: stage 15 SMMU configured; table walk verified %u pages and unmapped guards; bus master disabled\n", verified);
    return true;
}
static bool bhi_request(uint64_t address, unsigned size) {
    if (!dma_space.owned || rd(bar, 0x224) != 0x400c0110 || rd(bar, 0x28) != 0x100 ||
        rd(bar, 0x128) != 0 || (rd(dma_space.cb, 0x58) & WIFI_FSR_FAULT) || rd(dma_space.mmio, 0x48)) { return false; }
    bus_master_was_enabled = true;
    change_dbi(4, rd(dbi, 4) | 6, true);
    wr16(config + 4, (rd(config, 4) & 0xffff) | 6);
    if (!(rd(config, 4) & 4) || !(rd(dbi, 4) & 4)) { return false; }
    wr(bar + 0x12c, 0);
    wr(bar + 0x10c, address >> 32); wr(bar + 0x108, address); wr(bar + 0x110, size);
    wr(bar + 0x118, 1);
    return true;
}
static bool dma_fault_probe(void) {
    /* No firmware is supplied: ask PBL to read an unmapped guard page, then
     * require a translation fault from this exact SID before any real image. */
    uint64_t guard = WIFI_DMA_IOVA + WIFI_DMA_SIZE;
    redwood_tl("wifi-lemonade: stage 16 requesting PBL read of unmapped guard %#llx, 4096 bytes; no firmware supplied\n", guard);
    if (!bhi_request(guard, 4096)) { return false; }
    for (unsigned i = 0; i < 2000; i++) {
        if ((rd(dma_space.cb, 0x58) & WIFI_FSR_FAULT) || rd(dma_space.mmio, 0x48)) { break; }
        pause_us(1000);
    }
    uint32_t fsr = rd(dma_space.cb, 0x58), global = rd(dma_space.mmio, 0x48);
    uint64_t far = rd64(dma_space.cb + 0x60);
    uint32_t sid = rd(dma_space.mmio, 0x1400 + 4 * dma_space.context) & 0xffff;
    redwood_tl("wifi-lemonade: DMA guard result FSR %#x FAR %#llx SID %#x global %#x BHI %#x EE %u\n",
        fsr, far, sid, global, rd(bar, 0x12c), rd(bar, 0x128));
    if (!(fsr & 2) || (far & ~4095ull) != guard || sid != 0x1c01 || global) { return false; }
    redwood_tl("wifi-lemonade: stage 16 endpoint DMA guard blocked by owned SMMU context; SID 0x1c01 confirmed\n");
    return true;
}
static uint32_t wifi_crc32(const uint8_t *data, unsigned count) {
    uint32_t crc = ~0u;
    for (unsigned i = 0; i < count; i++) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; bit++) { crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1))); }
    }
    return ~crc;
}
extern const uint8_t *redwood_image_file(const char *, const char *, uint64_t *, int *);
static bool firmware_probe(void) {
    uint64_t size = 0;
    const uint8_t *firmware = redwood_image_file("fw", "amss.bin", &size, NULL);
    /* The host verifies the full SHA-256 and requires a passed guard test on
     * this exact kernel. Here also verify the bytes extracted from APFS. */
    if (!firmware || size != 5178848 || memcmp(firmware, "\177ELF", 4) ||
        wifi_crc32(firmware, WIFI_DMA_SIZE) != 0xaf54a935u) {
        redwood_tl("wifi-lemonade: original-device firmware missing or BHI prefix checksum mismatch\n");
        return false;
    }
    memcpy((void *)dma_space.data, firmware, WIFI_DMA_SIZE);
    if (wifi_crc32((const uint8_t *)dma_space.data, WIFI_DMA_SIZE) != 0xaf54a935u) { return false; }
    dma_flush(dma_space.data, WIFI_DMA_SIZE);
    redwood_tl("wifi-lemonade: stage 17 transferring verified original-device BHI prefix, 524288 bytes\n");
    if (!bhi_request(WIFI_DMA_IOVA, WIFI_DMA_SIZE)) { return false; }
    uint32_t status = 0, ee = 0, fsr = 0, global = 0;
    bool ready = false;
    for (unsigned i = 0; i < 1000; i++) {
        status = rd(bar, 0x12c); ee = rd(bar, 0x128);
        fsr = rd(dma_space.cb, 0x58); global = rd(dma_space.mmio, 0x48);
        if ((fsr & WIFI_FSR_FAULT) || global || (status >> 30) == 3) { break; }
        if ((status >> 30) == 2 && ee == 1) { ready = true; break; }
        pause_us(10000);
    }
    redwood_tl("wifi-lemonade: BHI result status %#x EE %u MHI %#x FSR %#x global %#x error %#x\n",
        status, ee, rd(bar, 0x48), fsr, global, rd(bar, 0x130));
    if (!ready) { return false; }
    redwood_tl("wifi-lemonade: stage 17 BHI transfer complete; execution environment SBL; no SMMU fault\n");
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
    if (step == 13) { return true; }
    if (covering || !route_endpoint()) { return false; }
    if (step == 14) { return true; }
    if (!dma_prepare()) { return false; }
    if (step == 15) { return true; }
    return step == 16 ? dma_fault_probe() : firmware_probe();
}
void lemonade_wifi_init(void) {
    unsigned mode = 0;
    if (!PE_parse_boot_argn("lemonade_wifi", &mode, sizeof(mode)) || !mode) { return; }
    if (!platform_is_lemonade()) {
        redwood_tl("wifi-lemonade: refused on another platform\n");
        return;
    }
    if (mode < 1 || mode > 17) {
        redwood_tl("wifi-lemonade: mode %u unsupported; no hardware access\n", mode);
        return;
    }
    if (mode == 17) {
        unsigned guard_verified = 0;
        PE_parse_boot_argn("lemonade_dma_verified", &guard_verified, sizeof(guard_verified));
        if (guard_verified != 1) {
            redwood_tl("wifi-lemonade: firmware probe requires verified endpoint guard evidence\n");
            return;
        }
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
        redwood_tl("wifi-lemonade: stage %u %s; network unavailable\n", mode, ok ? "passed" : "failed");
        lemonade_wifi_quiesce();
    }
}
