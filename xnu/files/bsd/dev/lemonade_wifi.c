/*
 * OnePlus 9 / SM8350 PCIe Wi-Fi bring-up. Android identifies Qualcomm
 * 17cb:1103 (CNSS QCA6490), revision 1, on PCIe root complex 0.
 * Register locations: the device FDT and OnePlusOSS SM8350 sources at
 * 13ca08fdf0979fdd61d5e8991661874bb2d19150: gcc-lahaina.c, lahaina-pcie.dtsi,
 * pci-msm.c. No network identity, calibration or credentials belong here.
 * Stages 2..15 keep endpoint DMA off; 16 verifies an unmapped DMA guard;
 * 17 transfers the original BHI prefix; 18 adds MSI/MHI events; 19 loads the
 * complete image; 20/21 test IPCR/QRTR and QMI, without RF or networking.
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
#include "lemonade_mhi_protocol.h"

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern void redwood_tl(const char *format, ...) __printflike(1, 2);
static vm_offset_t gcc, pins, parf, dbi, phy, rsc, database, config, bar;
static bool phy_started, perst_saved, radio_started, link_started, bar_saved;
static unsigned iatu_count;
static uint32_t perst_cfg, perst_io;
static uint32_t radio_cfg, radio_io, clkreq_cfg, clkreq_io, iatu_values[2][7];
static uint32_t bar0_old, bar1_old, endpoint_command_old;
static bool sid_saved, bus_master_was_enabled, msi_saved;
static unsigned msi_offset, msi_data_offset, msi_mask_offset;
static uint32_t msi_old_control, msi_old_low, msi_old_high, msi_old_data, msi_old_mask;
static uint32_t sid_table_old[256], sid_cfg_old;
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern uint64_t kvtophys(vm_offset_t va);
extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned);
extern void ml_io_unmap(vm_offset_t, vm_size_t);
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
            if (msi_saved) {
                wr16(config + msi_offset + 2, msi_old_control & ~1u);
                if (msi_old_control & 0x100) { wr(config + msi_mask_offset, msi_old_mask); }
                wr(config + msi_offset + 4, msi_old_low);
                if (msi_old_control & 0x80) { wr(config + msi_offset + 8, msi_old_high); }
                wr16(config + msi_data_offset, msi_old_data); wr16(config + msi_offset + 2, msi_old_control);
                bool restored = (rd(config, msi_offset) >> 16) == msi_old_control &&
                    rd(config, msi_offset + 4) == msi_old_low && (rd(config, msi_data_offset) & 0xffff) == msi_old_data &&
                    (!(msi_old_control & 0x80) || rd(config, msi_offset + 8) == msi_old_high) &&
                    (!(msi_old_control & 0x100) || rd(config, msi_mask_offset) == msi_old_mask);
                redwood_tl("wifi-lemonade: PCI MSI disabled and original configuration restored\n");
                redwood_tl("wifi-lemonade: PCI MSI restore readback %s\n", restored ? "verified" : "failed");
                msi_saved = false;
            }
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

/* A privately owned stage-1 address space, with no identity mapping or
 * firmware-owned context. The first probe leaves PCI bus mastering
 * off. IDR0.ATOSNS means standard translation requests are NOT supported;
 * software table validation is kept distinct from a real endpoint DMA test. */
#define WIFI_DMA_IOVA 0xa0004000ull
#define WIFI_DMA_SIZE 0x80000u
#define WIFI_PHYS_MASK 0x0000fffffffff000ull
#define WIFI_FSR_FAULT 0xc00001feu /* excludes read-only translation-format bits [10:9] */
#define WIFI_MHI_IOVA 0xa0200000ull
#define WIFI_MHI_SIZE (8u * 1024 * 1024)
#define WIFI_DATA_IOVA 0xa1000000ull
#define WIFI_DATA_LIMIT 0xb0000000ull
#define WIFI_TABLE_PAGES (7u + 120u)
#define WIFI_TABLE_SIZE (WIFI_TABLE_PAGES * 4096)
static const unsigned dma_cb_offsets[] = {0, 0x10, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x38, 0x3c};
static struct {
    vm_offset_t mmio, cb, tables, data, extra, control;
    unsigned match, context;
    uint32_t smr, s2cr, cbar, cba2r, cb_old[9];
    bool owned, retain_pages;
    unsigned allocation_count;
    uint64_t next_iova;
    struct { vm_offset_t cached, uncached; unsigned size; uint64_t iova; } allocations[32];
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
    if (dma_space.control) { ml_io_unmap(dma_space.control, PAGE_SIZE); dma_space.control = 0; }
    while (dma_space.allocation_count) {
        unsigned n = --dma_space.allocation_count;
        if (dma_space.allocations[n].uncached) { ml_io_unmap(dma_space.allocations[n].uncached, dma_space.allocations[n].size); }
        kmem_free(kernel_map, dma_space.allocations[n].cached, dma_space.allocations[n].size);
    }
    if (dma_space.extra) { kmem_free(kernel_map, dma_space.extra, round_page(WIFI_MHI_SIZE)); dma_space.extra = 0; }
    if (dma_space.tables) { kmem_free(kernel_map, dma_space.tables, round_page(WIFI_TABLE_SIZE)); dma_space.tables = 0; }
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
    uint64_t *l1 = (void *)dma_space.tables, *l2 = (void *)(dma_space.tables + 4096), *l3 = NULL;
    if ((l1[(address >> 30) & 511] & (WIFI_PHYS_MASK | 3)) != (kvtophys((vm_offset_t)l2) | 3)) { return false; }
    uint64_t entry = l2[(address >> 21) & 511] & (WIFI_PHYS_MASK | 3);
    for (unsigned page = 2; page < WIFI_TABLE_PAGES; page++) {
        vm_offset_t candidate = dma_space.tables + page * 4096;
        if (entry == (kvtophys(candidate) | 3)) { l3 = (void *)candidate; break; }
    }
    if (!l3) { return false; }
    uint64_t pte = l3[(address >> 12) & 511];
    if ((pte & 3) != 3) { return false; }
    *physical = (pte & WIFI_PHYS_MASK) | (address & 4095);
    return true;
}
/* Only allocated pages are published, separated by a native-page guard. The
 * mapping stays owned until endpoint reset; a failure never frees live DMA. */
static bool dma_allocate(unsigned requested, bool coherent, vm_offset_t *memory, uint64_t *address) {
    if (!dma_space.owned || !requested || requested > 64u * 1024 * 1024 ||
        dma_space.allocation_count == 32) { return false; }
    unsigned size = round_page(requested);
    uint64_t iova = dma_space.next_iova ? dma_space.next_iova : WIFI_DATA_IOVA;
    if (iova > WIFI_DATA_LIMIT - size - PAGE_SIZE) { return false; }
    vm_offset_t cached = 0, uncached = 0;
    kern_return_t result = coherent ?
        kmem_alloc_contig(kernel_map, &cached, size, 0, 0, 0, KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) :
        kmem_alloc(kernel_map, &cached, size, KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT);
    if (result != KERN_SUCCESS) { return false; }
    unsigned allocation = dma_space.allocation_count++;
    dma_space.allocations[allocation].cached = cached;
    dma_space.allocations[allocation].size = size;
    dma_space.allocations[allocation].iova = iova;
    dma_space.next_iova = iova + size + PAGE_SIZE;
    dma_flush(cached, size);
    if (coherent) {
        uncached = ml_io_map_unmappable(kvtophys(cached), size, VM_WIMG_WCOMB);
        if (!uncached) { return false; }
        dma_space.allocations[allocation].uncached = uncached;
    }
    uint64_t *l2 = (void *)(dma_space.tables + 4096);
    lck_mtx_lock(&redwood_smmu_lock);
    bool ok = true;
    for (unsigned offset = 0; offset < size; offset += 4096) {
        uint64_t at = iova + offset, physical = kvtophys(cached + offset);
        unsigned page = 7 + ((at - WIFI_DATA_IOVA) >> 21);
        uint64_t *leaf = (void *)(dma_space.tables + page * 4096);
        unsigned index = (at >> 21) & 511;
        uint64_t expected = kvtophys((vm_offset_t)leaf) | 3;
        if (page >= WIFI_TABLE_PAGES || !physical || physical >> 36 || physical & 4095 ||
            (coherent && physical != kvtophys(cached) + offset) ||
            (l2[index] && l2[index] != expected) || leaf[(at >> 12) & 511]) { ok = false; break; }
        l2[index] = expected;
        leaf[(at >> 12) & 511] = physical | 3ull | (1ull << 6) | (2ull << 8) | (1ull << 10) |
            (1ull << 11) | (1ull << 53) | (1ull << 54);
    }
    dma_flush(dma_space.tables, WIFI_TABLE_SIZE);
    if (ok) { ok = dma_tlb_sync(); }
    lck_mtx_unlock(&redwood_smmu_lock);
    for (unsigned offset = 0; ok && offset < size; offset += 4096) {
        uint64_t physical = 0;
        ok = dma_walk(iova + offset, &physical) && physical == kvtophys(cached + offset);
    }
    uint64_t unused = 0;
    if (!ok || dma_walk(iova - 4096, &unused) || dma_walk(iova + size, &unused)) { return false; }
    *memory = coherent ? uncached : cached; *address = iova;
    redwood_tl("wifi-lemonade: owned DMA allocation %u requested %u mapped %u pages %u guards verified\n",
        allocation, requested, size, size / 4096);
    return true;
}
static bool dma_prepare(bool extended) {
    if ((rd(config, 4) | rd(dbi, 4)) & 4) { return false; }
    dma_space.mmio = ml_io_map(0x15000000, 0x100000);
    if (!dma_space.mmio) { return false; }
    uint32_t scr = rd(dma_space.mmio, 0), id0 = rd(dma_space.mmio, 0x20), id1 = rd(dma_space.mmio, 0x24);
    unsigned matches = id0 & 255, contexts = id1 & 255, s2contexts = (id1 >> 16) & 255;
    /* This probe supports only the exact MMU-500 register geometry observed. */
    if ((scr & 9) || !(scr & (1u << 10)) || !(id0 & (1u << 30)) ||
        (id1 & 0xf0000000u) != 0x60000000u || matches > 255 || !matches || contexts > 128 || !contexts ||
        rd(dma_space.mmio, 0x48)) { return false; }
    if (kmem_alloc(kernel_map, &dma_space.tables, round_page(WIFI_TABLE_SIZE), KMA_KOBJECT | KMA_ZERO | KMA_DATA,
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
    if (extended) {
        if (PAGE_SIZE != 16384 || kmem_alloc(kernel_map, &dma_space.extra, round_page(WIFI_MHI_SIZE),
            KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) { return false; }
        for (unsigned n = 0; n < 4; n++) {
            uint64_t *leaf = (void *)(dma_space.tables + (3 + n) * 4096);
            l2[((WIFI_MHI_IOVA >> 21) & 511) + n] = kvtophys((vm_offset_t)leaf) | 3;
            for (unsigned page = 0; page < 512; page++) {
                unsigned offset = n * 0x200000 + page * 4096;
                uint64_t physical = kvtophys(dma_space.extra + offset);
                if (!physical || physical >> 36 || physical & 4095) { return false; }
                leaf[page] = physical | 3ull | (1ull << 6) | (2ull << 8) | (1ull << 10) |
                    (1ull << 11) | (1ull << 53) | (1ull << 54);
            }
        }
        /* Only the first native 16K page holds shared contexts/rings. After
         * this flush use its uncached alias exclusively until endpoint reset. */
        dma_flush(dma_space.extra, WIFI_MHI_SIZE);
        dma_space.control = ml_io_map_unmappable(kvtophys(dma_space.extra), PAGE_SIZE, VM_WIMG_WCOMB);
        if (!dma_space.control) { return false; }
        for (unsigned offset = 0; offset < WIFI_MHI_SIZE; offset += 4096) {
            uint64_t physical = 0;
            if (!dma_walk(WIFI_MHI_IOVA + offset, &physical) || physical != kvtophys(dma_space.extra + offset)) { return false; }
        }
        uint64_t unused = 0;
        if (dma_walk(WIFI_MHI_IOVA - 4096, &unused) || dma_walk(WIFI_MHI_IOVA + WIFI_MHI_SIZE, &unused)) { return false; }
        redwood_tl("wifi-lemonade: MHI DMA arena verified 2048 pages with outer guards; control uses uncached alias\n");
    }
    dma_flush(dma_space.tables, WIFI_TABLE_SIZE);
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
/* Polling first: PCI MSI targets an owned mailbox, without a CPU IRQ handler.
 * Device-written ring pointers and
 * records live in the uncached control alias; MMIO doors use high-then-low. */
static struct {
    unsigned channel_db, event_db, consumed[3], returned[3], events, m0_events, ee_events;
    bool m0_seen, mission_seen;
} mhi;
static const unsigned mhi_ring_offset[3] = {0x3000, 0x2000, 0x3200};
static const unsigned mhi_ring_count[3] = {32, 256, 32};
static void mhi_write_address(unsigned offset, uint64_t value) {
    wr(bar + offset + 4, value >> 32); wr(bar + offset, value);
}
static bool mhi_dma_ok(void) {
    uint32_t fsr = rd(dma_space.cb, 0x58), global = rd(dma_space.mmio, 0x48);
    if (!(fsr & WIFI_FSR_FAULT) && !global) { return true; }
    redwood_tl("wifi-lemonade: MHI DMA fault FSR %#x FAR %#llx global %#x\n", fsr, rd64(dma_space.cb + 0x60), global);
    return false;
}
#define MHI_IPCR_MTU 32768u
#define MHI_IPCR_RX 0x600000u
#define MHI_IPCR_TX 0x700000u
#define QRTR_CONTROL_PORT 0xfffffffeu
struct lemonade_qrtr_header { uint32_t version, type, source_node, source_port, confirm, size, dest_node, dest_port; };
struct lemonade_qrtr_control { uint32_t command, words[4]; };
static struct {
    unsigned command_next, command_code, tx_write, tx_read, rx_write, rx_read;
    unsigned tx_packets, tx_completed, rx_packets, hellos, services;
    uint64_t pending_command;
    uint32_t service_node, service_port, service_instance;
    bool channels, hello_sent, capability_seen, capability_ok;
    unsigned transaction, message, response_result, response_error, memory_count;
    bool response_seen, memory_ready;
    struct { uint32_t size, type, configs; uint64_t address; } memory[16];
} ipcr;
static bool ipcr_transfer_event(unsigned channel, unsigned code, uint64_t pointer, unsigned length);
static void mhi_context_address(vm_offset_t context, unsigned offset, uint64_t address) {
    wr(context + offset, address); wr(context + offset + 4, address >> 32);
}
static bool ipcr_send(const void *packet, unsigned length) {
    unsigned next = lemonade_mhi_next(ipcr.tx_write, 32);
    if (!ipcr.channels || !length || length > MHI_IPCR_MTU || next == ipcr.tx_read) { return false; }
    unsigned index = ipcr.tx_write;
    vm_offset_t buffer = dma_space.extra + MHI_IPCR_TX + index * MHI_IPCR_MTU;
    memcpy((void *)buffer, packet, length); dma_flush(buffer, length);
    volatile struct lemonade_mhi_element *tre = (void *)(dma_space.control + 0x3600 + index * 16);
    tre->pointer = WIFI_MHI_IOVA + MHI_IPCR_TX + index * MHI_IPCR_MTU;
    tre->word0 = length; tre->word1 = (2u << 16) | (1u << 9);
    __asm__ volatile("dsb sy" ::: "memory");
    ipcr.tx_write = next; ipcr.tx_packets++;
    uint64_t write = WIFI_MHI_IOVA + 0x3600 + next * 16;
    mhi_context_address(dma_space.control + 20 * 44, 36, write);
    mhi_write_address(mhi.channel_db + 20 * 8, write);
    return true;
}
static bool ipcr_qrtr_send(uint32_t type, uint32_t source_port, uint32_t node, uint32_t port,
    const void *payload, unsigned size) {
    uint8_t packet[256] = {0};
    if (!size || size > sizeof(packet) - sizeof(struct lemonade_qrtr_header)) { return false; }
    struct lemonade_qrtr_header header = {1, type, 1, source_port, 0, size, node, port};
    memcpy(packet, &header, sizeof(header)); memcpy(packet + sizeof(header), payload, size);
    return ipcr_send(packet, sizeof(header) + ((size + 3) & ~3u));
}
static bool ipcr_receive(const uint8_t *data, unsigned size) {
    struct lemonade_qrtr_header header = {0};
    unsigned offset;
    if (size >= sizeof(header) && data[0] == 1) {
        memcpy(&header, data, sizeof(header)); offset = sizeof(header);
    } else if (size >= 16 && data[0] == 3) {
        header.type = data[1]; header.confirm = data[2] & 1;
        memcpy(&header.size, data + 4, 4);
        header.source_node = data[8] | (data[9] << 8); header.source_port = data[10] | (data[11] << 8);
        header.dest_node = data[12] | (data[13] << 8); header.dest_port = data[14] | (data[15] << 8);
        if (header.source_port == 0xfffe) { header.source_port = QRTR_CONTROL_PORT; }
        if (header.dest_port == 0xfffe) { header.dest_port = QRTR_CONTROL_PORT; }
        offset = 16 + data[3];
    } else { redwood_tl("wifi-lemonade: IPCR packet has unsupported QRTR version/length\n"); return false; }
    if (offset > size || !header.size || header.size > size - offset) { return false; }
    ipcr.rx_packets++;
    if (ipcr.rx_packets <= 20) {
        redwood_tl("wifi-lemonade: QRTR packet type %u payload %u bytes\n", header.type, header.size);
    }
    if (header.confirm) {
        struct lemonade_qrtr_control resume = {7, {header.dest_node, header.dest_port, 0, 0}};
        if (!ipcr_qrtr_send(7, header.dest_port, header.source_node, header.source_port, &resume, sizeof(resume))) { return false; }
    }
    if (header.dest_port == QRTR_CONTROL_PORT && header.size >= 4) {
        struct lemonade_qrtr_control control = {0};
        memcpy(&control, data + offset, header.size < sizeof(control) ? header.size : sizeof(control));
        if (control.command == 2) {
            ipcr.hellos++;
            if (!ipcr.hello_sent) {
                struct lemonade_qrtr_control hello = {2, {0}};
                if (!ipcr_qrtr_send(2, QRTR_CONTROL_PORT, header.source_node, QRTR_CONTROL_PORT, &hello, sizeof(hello))) { return false; }
                ipcr.hello_sent = true;
            }
        } else if (control.command == 4 && header.size >= sizeof(control)) {
            ipcr.services++;
            redwood_tl("wifi-lemonade: QRTR service %#x version %u instance %u announced\n",
                control.words[0], control.words[1] & 255, control.words[1] >> 8);
            if (control.words[0] == 0x45 && (control.words[1] & 255) == 1) {
                ipcr.service_node = control.words[2]; ipcr.service_port = control.words[3];
                ipcr.service_instance = control.words[1] >> 8;
            }
        }
    } else if (header.type == 1 && header.dest_port == 0x4000 &&
        header.source_node == ipcr.service_node && header.source_port == ipcr.service_port) {
        const uint8_t *qmi = data + offset;
        if (header.size < 7) { return false; }
        unsigned transaction = qmi[1] | (qmi[2] << 8), message = qmi[3] | (qmi[4] << 8);
        unsigned payload = qmi[5] | (qmi[6] << 8);
        if (payload != header.size - 7) { return false; }
        redwood_tl("wifi-lemonade: QMI packet type %u transaction %u message %#x payload %u bytes\n", qmi[0], transaction, message, payload);
        if (qmi[0] == 2 && transaction == ipcr.transaction && message == ipcr.message) {
            unsigned at = 7;
            while (at + 3 <= header.size) {
                unsigned type = qmi[at], length = qmi[at + 1] | (qmi[at + 2] << 8); at += 3;
                if (length > header.size - at) { return false; }
                if (type == 2 && length == 4) {
                    unsigned result = qmi[at] | (qmi[at + 1] << 8), error = qmi[at + 2] | (qmi[at + 3] << 8);
                    ipcr.response_seen = true; ipcr.response_result = result; ipcr.response_error = error;
                    redwood_tl("wifi-lemonade: WLFW response message %#x result %u error %u\n", message, result, error);
                    if (message == 0x24) {
                        ipcr.capability_seen = true; ipcr.capability_ok = result == 0 && error == 0;
                        redwood_tl("wifi-lemonade: WLFW capability response result %u error %u\n", result, error);
                    }
                }
                at += length;
            }
            if (at != header.size) { return false; }
        } else if (qmi[0] == 4 && message == 0x35) {
            unsigned at = 7;
            while (at + 3 <= header.size) {
                unsigned type = qmi[at], length = qmi[at + 1] | (qmi[at + 2] << 8); at += 3;
                if (length > header.size - at) { return false; }
                if (type == 1) {
                    if (!length || !qmi[at] || qmi[at] > 16 || ipcr.memory_count) { return false; }
                    unsigned count = qmi[at], pos = at + 1, end = at + length;
                    for (unsigned i = 0; i < count; i++) {
                        if (end - pos < 9) { return false; }
                        memcpy(&ipcr.memory[i].size, qmi + pos, 4);
                        memcpy(&ipcr.memory[i].type, qmi + pos + 4, 4);
                        unsigned configs = qmi[pos + 8]; pos += 9;
                        if (configs > 2 || configs * 13 > end - pos) { return false; }
                        ipcr.memory[i].configs = configs; pos += configs * 13;
                        redwood_tl("wifi-lemonade: WLFW memory request %u type %u bytes %u configs %u\n",
                            i, ipcr.memory[i].type, ipcr.memory[i].size, configs);
                    }
                    if (pos != end) { return false; }
                    ipcr.memory_count = count;
                }
                at += length;
            }
            if (at != header.size) { return false; }
        } else if (qmi[0] == 4 && message == 0x37) {
            ipcr.memory_ready = true;
            redwood_tl("wifi-lemonade: WLFW firmware memory ready indication\n");
        }
    }
    return true;
}
static bool ipcr_transfer_event(unsigned channel, unsigned code, uint64_t pointer, unsigned length) {
    if (!ipcr.channels || (channel != 20 && channel != 21)) { return false; }
    unsigned ring_offset = channel == 20 ? 0x3600 : 0x3800;
    uint64_t base = WIFI_MHI_IOVA + ring_offset;
    if (code == 6) {
        unsigned index = channel == 20 ? ipcr.tx_write : ipcr.rx_write;
        mhi_write_address(mhi.channel_db + channel * 8, base + index * 16);
        return true;
    }
    if ((code != 2 && code != 4) || !lemonade_mhi_pointer_valid(pointer, base, 32)) {
        redwood_tl("wifi-lemonade: IPCR transfer rejected channel %u code %u length %u\n", channel, code, length);
        return false;
    }
    unsigned index = (unsigned)((pointer - base) / 16);
    if (channel == 20) {
        unsigned outstanding = (ipcr.tx_write - ipcr.tx_read) & 31;
        unsigned done = ((index - ipcr.tx_read) & 31) + 1;
        if (!outstanding || done > outstanding) { return false; }
        ipcr.tx_read = lemonade_mhi_next(index, 32); ipcr.tx_completed += done;
        return true;
    }
    if (index != ipcr.rx_read || !length || length > MHI_IPCR_MTU) { return false; }
    vm_offset_t buffer = dma_space.extra + MHI_IPCR_RX + index * MHI_IPCR_MTU;
    /* RX buffers are never written by the CPU after initial zero/flush. */
    dma_flush(buffer, MHI_IPCR_MTU);
    bool ok = ipcr_receive((const uint8_t *)buffer, length);
    dma_flush(buffer, MHI_IPCR_MTU);
    if (!ok) { return false; }
    ipcr.rx_read = lemonade_mhi_next(ipcr.rx_read, 32);
    ipcr.rx_write = lemonade_mhi_next(ipcr.rx_write, 32);
    uint64_t write = base + ipcr.rx_write * 16;
    mhi_context_address(dma_space.control + 21 * 44, 36, write);
    mhi_write_address(mhi.channel_db + 21 * 8, write);
    return true;
}

static bool mhi_events(void) {

    if (!mhi_dma_ok()) { return false; }
    for (unsigned n = 0; n < 3; n++) {
        uint64_t base = WIFI_MHI_IOVA + mhi_ring_offset[n];
        unsigned budget = mhi_ring_count[n];
        bool recycled = false;
        while (budget--) {
            __asm__ volatile("dmb oshld" ::: "memory");
            vm_offset_t context = dma_space.control + 0x800 + n * 44;
            uint64_t pointer = ((uint64_t)rd(context, 32) << 32) | rd(context, 28);
            if (!lemonade_mhi_pointer_valid(pointer, base, mhi_ring_count[n])) {
                redwood_tl("wifi-lemonade: MHI event ring %u invalid read pointer %#llx\n", n, pointer);
                for (unsigned word = 0; word < 11; word++) {
                    redwood_tl("wifi-lemonade: MHI context %u word %u = %#x\n", n, word, rd(context, 4 * word));
                }
                vm_offset_t event_base = dma_space.control + mhi_ring_offset[n];
                redwood_tl("wifi-lemonade: MHI first event words %#x %#x %#x %#x next %#x %#x %#x %#x\n",
                    rd(event_base, 0), rd(event_base, 4), rd(event_base, 8), rd(event_base, 12),
                    rd(event_base, 16), rd(event_base, 20), rd(event_base, 24), rd(event_base, 28));
                return false;
            }
            if (pointer == base + mhi.consumed[n] * 16) { break; }
            volatile struct lemonade_mhi_element *event = (void *)(dma_space.control +
                mhi_ring_offset[n] + mhi.consumed[n] * 16);
            uint32_t word0 = event->word0, word1 = event->word1;
            unsigned type = (word1 >> 16) & 255, value = word0 >> 24;
            if (mhi.events < 32) {
                redwood_tl("wifi-lemonade: MHI event ring %u type %#x value %u channel %u\n", n, type, value, word1 >> 24);
            }
            mhi.events++;
            if (type == 0x20) {
                if (value == 2) { mhi.m0_seen = true; mhi.m0_events++; }
                else if (value == 0xff) { return false; }
            } else if (type == 0x40) {
                mhi.ee_events++;
                if (value == 2 || value == 4) { mhi.mission_seen = true; }
                if (value == 3) { return false; } /* crash-dump environment */
            } else if (type == 0x21) {
                if (!ipcr.pending_command || event->pointer != ipcr.pending_command || value != 1) {
                    redwood_tl("wifi-lemonade: IPCR command completion mismatch code %u\n", value);
                    return false;
                }
                ipcr.command_code = value;
            } else if (type == 0x22) {
                if (!ipcr_transfer_event(word1 >> 24, value, event->pointer, word0 & 0xffff)) { return false; }
            } else if (type != 0x50) {
                redwood_tl("wifi-lemonade: unsupported MHI event type %#x\n", type);
                return false;
            }
            mhi.consumed[n] = lemonade_mhi_next(mhi.consumed[n], mhi_ring_count[n]);
            mhi.returned[n] = lemonade_mhi_next(mhi.returned[n], mhi_ring_count[n]);
            wr(context + 36, (uint32_t)(base + mhi.returned[n] * 16));
            wr(context + 40, 0);
            recycled = true;
        }
        if (recycled) {
            __asm__ volatile("dsb sy" ::: "memory");
            mhi_write_address(mhi.event_db + n * 8, base + mhi.returned[n] * 16);
        }
    }
    return mhi_dma_ok();
}
/* Give MHI's interrupt writes a valid owned destination even while polling.
 * This is a DMA mailbox, not a GIC interrupt: SMMU protects it like the rings. */
static bool mhi_msi_prepare(void) {
    unsigned next = rd(config, 0x34) & 255;
    for (unsigned count = 0; count < 32 && next; count++) {
        if (next < 0x40 || next > 0xe8 || (next & 3)) { return false; }
        uint32_t cap = rd(config, next);
        if ((cap & 255) != 5) { next = (cap >> 8) & 255; continue; }
        uint32_t control = cap >> 16;
        redwood_tl("wifi-lemonade: PCI MSI capability %#x control %#x\n", next, control);
        if ((control & 1) || ((control >> 1) & 7) < 2) { return false; }
        msi_offset = next; msi_old_control = control;
        msi_data_offset = next + ((control & 0x80) ? 12 : 8);
        msi_mask_offset = msi_data_offset + 4;
        msi_old_low = rd(config, next + 4); msi_old_high = (control & 0x80) ? rd(config, next + 8) : 0;
        msi_old_data = rd(config, msi_data_offset) & 0xffff;
        if (control & 0x100) { msi_old_mask = rd(config, msi_mask_offset); }
        msi_saved = true;
        wr(dma_space.control + 0x1800, 0xfeed0000);
        wr(config + next + 4, (uint32_t)(WIFI_MHI_IOVA + 0x1800));
        if (control & 0x80) { wr(config + next + 8, 0); }
        wr16(config + msi_data_offset, 0x100);
        if (control & 0x100) { wr(config + msi_mask_offset, msi_old_mask & ~15u); }
        wr16(config + next + 2, (control & ~0x70u) | 0x21u); /* four messages */
        bool ok = ((rd(config, next) >> 16) & 0x71) == 0x21 &&
            rd(config, next + 4) == WIFI_MHI_IOVA + 0x1800 &&
            (!(control & 0x80) || !rd(config, next + 8));
        redwood_tl("wifi-lemonade: PCI MSI owned-mailbox configuration %s; four vectors, CPU IRQ disabled\n", ok ? "verified" : "failed");
        return ok;
    }
    return false;
}

static bool mhi_ready(void) {
    if (!dma_space.control || (rd(bar, 0x38) & 2) || !(rd(bar, 0x48) & 1) ||
        rd(bar, 0x128) != 1) { return false; }
    if (!mhi_msi_prepare()) { return false; }
    mhi.channel_db = rd(bar, 0x18); mhi.event_db = rd(bar, 0x20);
    if (mhi.channel_db < 0x100 || mhi.channel_db > 0x1ff000 || (mhi.channel_db & 7) ||
        mhi.event_db < 0x100 || mhi.event_db > 0x1ff000 || (mhi.event_db & 7)) { return false; }
    redwood_tl("wifi-lemonade: MHI control alias physical %#llx cached physical %#llx next %#llx\n",
        kvtophys(dma_space.control), kvtophys(dma_space.extra), kvtophys(dma_space.extra + 0x3000));
    volatile struct lemonade_mhi_context *channels = (void *)dma_space.control;
    volatile struct lemonade_mhi_context *events = (void *)(dma_space.control + 0x800);
    volatile struct lemonade_mhi_context *command = (void *)(dma_space.control + 0xc00);
    /* IPCR is channel pair 20/21 in this board's original MHI configuration.
     * Leave all channels disabled until a later explicit START command. */
    channels[20].type = 1; channels[20].index = 1;
    channels[21].type = 2; channels[21].index = 1;
    for (unsigned i = 0; i < 3; i++) {
        uint64_t base = WIFI_MHI_IOVA + mhi_ring_offset[i];
        events[i].type = 1;
        events[i].index = i == 2 ? 0 : i + 1;
        events[i].base = base; events[i].length = mhi_ring_count[i] * 16;
        events[i].read = base; events[i].write = base + (mhi_ring_count[i] - 1) * 16;
        mhi.returned[i] = mhi_ring_count[i] - 1;
    }
    command->base = WIFI_MHI_IOVA + 0x3400; command->length = 32 * 16;
    command->read = command->write = command->base;
    __asm__ volatile("dsb sy" ::: "memory");
    for (unsigned n = 0; n < 3; n++) {
        vm_offset_t context = dma_space.control + 0x800 + n * 44;
        redwood_tl("wifi-lemonade: MHI context %u before publish cfg/type/index %#x/%#x/%#x base %#x:%#x len %#x:%#x rp %#x:%#x wp %#x:%#x\n",
            n, rd(context, 0), rd(context, 4), rd(context, 8), rd(context, 16), rd(context, 12),
            rd(context, 24), rd(context, 20), rd(context, 32), rd(context, 28), rd(context, 40), rd(context, 36));
        if (rd(context, 12) != WIFI_MHI_IOVA + mhi_ring_offset[n] || rd(context, 16) != 0 ||
            rd(context, 28) != WIFI_MHI_IOVA + mhi_ring_offset[n] || rd(context, 32) != 0) {
            redwood_tl("wifi-lemonade: MHI context readback before publish failed\n");
            return false;
        }
    }
    mhi_write_address(mhi.channel_db + 127 * 8, 0);
    mhi_write_address(0x58, WIFI_MHI_IOVA);
    mhi_write_address(0x60, WIFI_MHI_IOVA + 0x800);
    mhi_write_address(0x68, WIFI_MHI_IOVA + 0xc00);
    wr(bar + 0x10, (rd(bar, 0x10) & 0xffff) | (3u << 16));
    /* Match the original board's advertised MHI window. This advertises
     * addresses only: SMMU still maps just our two bounded allocations. */
    mhi_write_address(0x80, 0xa0000000ull);
    mhi_write_address(0x88, 0xb0000000ull);
    mhi_write_address(0x98, 0xa0000000ull);
    mhi_write_address(0xa0, 0xb0000000ull);
    if (rd(bar, 0x58) != WIFI_MHI_IOVA || rd(bar, 0x60) != WIFI_MHI_IOVA + 0x800 ||
        ((rd(bar, 0x10) >> 16) & 0xffff) != 3) { return false; }
    for (unsigned i = 0; i < 3; i++) { mhi_write_address(mhi.event_db + i * 8, events[i].write); }
    redwood_tl("wifi-lemonade: MHI contexts published NER 3 CHDB %#x ERDB %#x CFG %#x VER %#x; requesting M0\n", mhi.channel_db, mhi.event_db, rd(bar, 0x10), rd(bar, 8));
    wr(bar + 0x38, 2u << 8);
    mhi_write_address(mhi.channel_db + 127 * 8, 1); /* keep firmware awake */
    bool ready = false;
    for (unsigned i = 0; i < 5000; i++) {
        if (!mhi_events()) { break; }
        uint32_t status = rd(bar, 0x48);
        if ((status & 4) || ((status >> 8) & 255) == 0xff) { break; }
        if (((status >> 8) & 255) == 2 && mhi.m0_seen) { ready = true; break; }
        pause_us(1000);
    }
    redwood_tl("wifi-lemonade: MHI ready result status %#x EE %u events %u M0events %u EEevents %u FSR %#x global %#x\n",
        rd(bar, 0x48), rd(bar, 0x128), mhi.events, mhi.m0_events, mhi.ee_events,
        rd(dma_space.cb, 0x58), rd(dma_space.mmio, 0x48));
    redwood_tl("wifi-lemonade: MHI MSI mailbox %#x\n", rd(dma_space.control, 0x1800));
    if (!ready || !mhi_dma_ok()) { return false; }
    redwood_tl("wifi-lemonade: stage 18 MHI M0 confirmed by device-written event; polling transport ready\n");
    return true;
}
static bool mhi_full_firmware(void) {
    uint64_t size = 0;
    const uint8_t *image = redwood_image_file("fw", "amss.bin", &size, NULL);
    if (!image || size != 5178848 || wifi_crc32(image, size) != 0x7b19c475u) { return false; }
    unsigned bhie = rd(bar, 0x2c);
    if (bhie < 0x100 || bhie > 0x1fff00 || bhie & 3) { return false; }
    vm_offset_t copy = dma_space.extra + 0x20000;
    memcpy((void *)copy, image, size);
    if (wifi_crc32((const uint8_t *)copy, size) != 0x7b19c475u) { return false; }
    dma_flush(copy, size);
    volatile struct lemonade_bhie_vector *vectors = (void *)(dma_space.control + 0x1000);
    unsigned count = 0;
    for (unsigned offset = 0; offset < size; offset += WIFI_DMA_SIZE) {
        vectors[count].address = WIFI_MHI_IOVA + 0x20000 + offset;
        vectors[count].length = size - offset < WIFI_DMA_SIZE ? size - offset : WIFI_DMA_SIZE;
        count++;
    }
    __asm__ volatile("dsb sy" ::: "memory");
    redwood_tl("wifi-lemonade: stage 19 BHIE original firmware %u bytes, %u vectors, offset %#x\n", (unsigned)size, count, bhie);
    mhi_write_address(bhie + 0x2c, WIFI_MHI_IOVA + 0x1000);
    wr(bar + bhie + 0x34, count * sizeof(*vectors));
    wr(bar + bhie + 0x3c, (rd(bar, bhie + 0x3c) & 0xc0000000u) | 2);
    bool complete = false;
    for (unsigned i = 0; i < 15000; i++) {
        if (!mhi_events()) { break; }
        uint32_t status = rd(bar, bhie + 0x44), ee = rd(bar, 0x128), state = rd(bar, 0x48);
        if ((status >> 30) == 3 || ee == 3 || (state & 4)) { break; }
        if ((status >> 30) == 2 && (ee == 2 || ee == 4) && mhi.mission_seen) { complete = true; break; }
        pause_us(1000);
    }
    redwood_tl("wifi-lemonade: BHIE result status %#x EE %u MHI %#x events %u mission_event %u FSR %#x global %#x\n",
        rd(bar, bhie + 0x44), rd(bar, 0x128), rd(bar, 0x48), mhi.events, mhi.mission_seen,
        rd(dma_space.cb, 0x58), rd(dma_space.mmio, 0x48));
    if (!complete || !mhi_dma_ok()) { return false; }
    redwood_tl("wifi-lemonade: stage 19 full firmware running; mission environment confirmed by event; no SMMU fault\n");
    return true;
}
static bool ipcr_start_command(unsigned channel) {
    unsigned index = ipcr.command_next;
    uint64_t address = WIFI_MHI_IOVA + 0x3400 + index * 16;
    volatile struct lemonade_mhi_element *command = (void *)(dma_space.control + 0x3400 + index * 16);
    command->pointer = 0; command->word0 = 0; command->word1 = (channel << 24) | (0x12u << 16);
    ipcr.pending_command = address; ipcr.command_code = 0;
    ipcr.command_next = lemonade_mhi_next(index, 32);
    uint64_t write = WIFI_MHI_IOVA + 0x3400 + ipcr.command_next * 16;
    mhi_context_address(dma_space.control + 0xc00, 36, write);
    __asm__ volatile("dsb sy" ::: "memory");
    mhi_write_address(0x70, write);
    for (unsigned i = 0; i < 3000 && !ipcr.command_code; i++) {
        if (!mhi_events()) { return false; }
        pause_us(1000);
    }
    redwood_tl("wifi-lemonade: IPCR channel %u START completion %u\n", channel, ipcr.command_code);
    ipcr.pending_command = 0;
    return ipcr.command_code == 1;
}
static bool ipcr_qmi_request(unsigned message, const void *payload, unsigned length) {
    uint8_t request[192] = {0};
    if (length > sizeof(request) - 7 || ipcr.transaction == 0xffff) { return false; }
    unsigned transaction = ++ipcr.transaction;
    ipcr.message = message; ipcr.response_seen = false;
    request[1] = transaction; request[2] = transaction >> 8;
    request[3] = message; request[4] = message >> 8;
    request[5] = length; request[6] = length >> 8;
    if (length) { memcpy(request + 7, payload, length); }
    if (!ipcr_qrtr_send(1, 0x4000, ipcr.service_node, ipcr.service_port, request, length + 7)) { return false; }
    for (unsigned i = 0; i < 5000 && !ipcr.response_seen; i++) {
        if (!mhi_events()) { return false; }
        pause_us(1000);
    }
    return ipcr.response_seen && !ipcr.response_result && !ipcr.response_error;
}
static bool ipcr_probe(unsigned step) {
    if (!mhi.mission_seen || (rd(bar, 0x128) != 2 && rd(bar, 0x128) != 4)) { return false; }
    for (unsigned channel = 20; channel <= 21; channel++) {
        vm_offset_t context = dma_space.control + channel * 44;
        uint64_t base = WIFI_MHI_IOVA + (channel == 20 ? 0x3600 : 0x3800);
        wr(context, 1u | (2u << 8)); wr(context + 4, channel == 20 ? 1 : 2); wr(context + 8, 1);
        mhi_context_address(context, 12, base); mhi_context_address(context, 20, 32 * 16);
        mhi_context_address(context, 28, base); mhi_context_address(context, 36, base);
    }
    for (unsigned i = 0; i < 32; i++) {
        volatile struct lemonade_mhi_element *tre = (void *)(dma_space.control + 0x3800 + i * 16);
        tre->pointer = WIFI_MHI_IOVA + MHI_IPCR_RX + i * MHI_IPCR_MTU;
        tre->word0 = MHI_IPCR_MTU; tre->word1 = (2u << 16) | (1u << 9);
    }
    __asm__ volatile("dsb sy" ::: "memory");
    ipcr.channels = true;
    if (!ipcr_start_command(20) || !ipcr_start_command(21)) { return false; }
    ipcr.rx_write = 31;
    mhi_context_address(dma_space.control + 21 * 44, 36, WIFI_MHI_IOVA + 0x3800 + 31 * 16);
    mhi_write_address(mhi.channel_db + 21 * 8, WIFI_MHI_IOVA + 0x3800 + 31 * 16);
    struct lemonade_qrtr_control hello = {2, {0}};
    if (!ipcr_qrtr_send(2, QRTR_CONTROL_PORT, 0xffffffffu, QRTR_CONTROL_PORT, &hello, sizeof(hello))) { return false; }
    bool ready = false;
    for (unsigned i = 0; i < 5000; i++) {
        if (!mhi_events()) { return false; }
        if (ipcr.service_port && ipcr.tx_completed && ipcr.rx_packets) { ready = true; break; }
        pause_us(1000);
    }
    redwood_tl("wifi-lemonade: IPCR result tx %u completed %u rx %u hellos %u services %u WLFW %u instance %u FSR %#x global %#x\n",
        ipcr.tx_packets, ipcr.tx_completed, ipcr.rx_packets, ipcr.hellos, ipcr.services,
        ipcr.service_port != 0, ipcr.service_instance, rd(dma_space.cb, 0x58), rd(dma_space.mmio, 0x48));
    if (!ready || !mhi_dma_ok()) { return false; }
    redwood_tl("wifi-lemonade: stage 20 IPCR channels 20/21 active; QRTR bidirectional and WLFW service found\n");
    if (step == 20) { return true; }
    /* WLFW requires the client registration and host capabilities before a
     * target capability query. Subscribe to the PCIe memory handshake. */
    static const uint8_t registration[] = {
        0x15,4,0, 0x4c,0x45,0x4e,0x4b, /* kernel client */
        0x16,1,0,1, 0x17,1,0,1, 0x18,1,0,1, 0x1b,1,0,1
    };
    static const uint8_t host[] = {
        0x10,4,0,1,0,0,0, /* one client */
        0x14,1,0,1, /* board data */
        0x16,1,0,1, 0x17,1,0,1, /* original M3 image/cache */
        0x1a,1,0,0 /* calibration not yet done */
    };
    if (!ipcr_qmi_request(0x20, registration, sizeof(registration)) ||
        !ipcr_qmi_request(0x34, host, sizeof(host))) { return false; }
    for (unsigned i = 0; i < 2000 && !ipcr.memory_count && !ipcr.memory_ready; i++) {
        if (!mhi_events()) { return false; }
        pause_us(1000);
    }
    if (ipcr.memory_count) {
        uint8_t response[4 + 17 * 8] = {1, 0, 0, 0};
        if (ipcr.memory_count > 8) { return false; }
        unsigned length = 1 + 17 * ipcr.memory_count;
        response[1] = length; response[2] = length >> 8; response[3] = ipcr.memory_count;
        for (unsigned i = 0; i < ipcr.memory_count; i++) {
            vm_offset_t memory = 0;
            if (ipcr.memory[i].configs || (ipcr.memory[i].type != 1 && ipcr.memory[i].type != 4) ||
                !dma_allocate(ipcr.memory[i].size, false, &memory, &ipcr.memory[i].address)) { return false; }
            memcpy(response + 4 + 17 * i, &ipcr.memory[i].address, 8);
            memcpy(response + 12 + 17 * i, &ipcr.memory[i].size, 4);
            memcpy(response + 16 + 17 * i, &ipcr.memory[i].type, 4);
        }
        if (!ipcr_qmi_request(0x36, response, length + 3)) { return false; }
        for (unsigned i = 0; i < 10000 && !ipcr.memory_ready; i++) {
            if (!mhi_events()) { return false; }
            pause_us(1000);
        }
        if (!ipcr.memory_ready) { return false; }
    }
    if (!ipcr_qmi_request(0x24, NULL, 0)) { return false; }
    redwood_tl("wifi-lemonade: QMI result response %u success %u tx_completed %u rx %u FSR %#x global %#x\n",
        ipcr.capability_seen, ipcr.capability_ok, ipcr.tx_completed, ipcr.rx_packets,
        rd(dma_space.cb, 0x58), rd(dma_space.mmio, 0x48));
    if (!ipcr.capability_seen || !ipcr.capability_ok || !mhi_dma_ok()) { return false; }
    redwood_tl("wifi-lemonade: stage 21 WLFW capability request/response successful over QMI; no SMMU fault\n");
    return true;
}

static bool mhi_probe(unsigned step) {
    if (!mhi_ready()) { return false; }
    if (step == 18) { return true; }
    if (!mhi_full_firmware()) { return false; }
    return step == 19 || ipcr_probe(step);
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
    if (!dma_prepare(step >= 18)) { return false; }
    if (step == 15) { return true; }
    if (step == 16) { return dma_fault_probe(); }
    if (!firmware_probe()) { return false; }
    return step == 17 || mhi_probe(step);
}
void lemonade_wifi_init(void) {
    unsigned mode = 0;
    if (!PE_parse_boot_argn("lemonade_wifi", &mode, sizeof(mode)) || !mode) { return; }
    if (!platform_is_lemonade()) {
        redwood_tl("wifi-lemonade: refused on another platform\n");
        return;
    }
    if (mode < 1 || mode > 21) {
        redwood_tl("wifi-lemonade: mode %u unsupported; no hardware access\n", mode);
        return;
    }
    if (mode >= 17) {
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
