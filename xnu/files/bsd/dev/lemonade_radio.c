/* Shared SM8350 RPMh supply transaction path for touch and the combo radio.
 * Board sequences retain votes until the RAM session ends. The lock covers
 * idle check, save, command submission, completion and register restoration.
 * Source: existing verified lemonade supply sequence, with serialized ownership.
 */
#ifdef LEMONADE_RADIO_HOST_TEST
#include "lemonade-radio-test-support.h"
#else
#include <kern/clock.h>
#include <kern/locks.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <mach/vm_param.h>
#include <pexpert/device_tree.h>
#include <sys/lemonade_radio.h>
#include <vm/memory_types.h>
#include <vm/vm_kern.h>
extern void redwood_tl(const char *, ...) __printflike(1, 2);
extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned);
#endif
static LCK_GRP_DECLARE(lemonade_radio_group, "lemonade-radio");
static LCK_MTX_DECLARE(lemonade_radio_lock, &lemonade_radio_group);
static vm_offset_t rsc, database;
static bool unavailable;
static struct { uint32_t address; unsigned millivolts; } retained_votes[32];
static uint32_t __attribute__((noinline)) rd(vm_offset_t base, unsigned offset) {
#ifdef LEMONADE_RADIO_HOST_TEST
    return radio_test_read(base + offset);
#else
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(base + offset) : "memory");
    return value;
#endif
}
static void __attribute__((noinline)) wr(vm_offset_t address, uint32_t value) {
#ifdef LEMONADE_RADIO_HOST_TEST
    radio_test_write(address, value);
#else
    __asm__ volatile("str %w0, [%1]" : : "r"(value), "r"(address) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
#endif
}
static void pause_us(unsigned us) { delay_for_interval(us, NSEC_PER_USEC); }
static bool platform_ok(void) {
    DTEntry entry; const void *value; unsigned size;
    return SecureDTLookupEntry(NULL, "/arm-io", &entry) == kSuccess &&
        SecureDTGetProperty(entry, "device_type", &value, &size) == kSuccess &&
        size == sizeof("lemonade") && !memcmp(value, "lemonade", size);
}
struct radio_db_slave { uint16_t id, header, data, count, version, reserved[3]; };
struct radio_db_header {
    uint32_t version;
    uint8_t magic[4];
    struct radio_db_slave slave[8];
    uint32_t checksum, reserved;
    uint8_t data[];
};
struct radio_db_entry { char id[8]; uint32_t priority[2], address; uint16_t length, offset; };

static uint32_t
supply_address(const char *name)
{
    const struct radio_db_header *db = (const void *)database;
    char id[8] = {0};
    strlcpy(id, name, sizeof(id));
    if (memcmp(db->magic, "\xdb\x30\x03\x0c", 4)) { return 0; }
    for (unsigned s = 0; s < 8; s++) {
        const struct radio_db_slave *slave = &db->slave[s];
        if (!slave->id || slave->count > 512 ||
            (uint32_t)slave->header + slave->count * sizeof(struct radio_db_entry) > 0x1f000) { continue; }
        const struct radio_db_entry *entry = (const void *)(db->data + slave->header);
        for (unsigned i = 0; i < slave->count; i++) {
            if (!memcmp(entry[i].id, id, sizeof(id))) { return entry[i].address; }
        }
    }
    return 0;
}

static bool supply_on_locked(const char *name, unsigned millivolts) {
    uint32_t address = supply_address(name);
    vm_offset_t t = rsc + 0xd00;
    if (!address || ((address >> 16) & 15) != 4) { return false; }
    unsigned vote = 0, empty = 32;
    for (; vote < 32; vote++) {
        if (retained_votes[vote].address == address) { break; }
        if (!retained_votes[vote].address && empty == 32) { empty = vote; }
    }
    if (vote < 32 && retained_votes[vote].millivolts >= millivolts) { return true; }
    if (vote == 32) { vote = empty; }
    if (vote == 32) { return false; }
    for (unsigned i = 0; !rd(t + 0x18, 0); i++) {
        if (i == 1000) { return false; }
        pause_us(100);
    }
    if (rd(t + 4, 0) & 1) {
        redwood_tl("lemonade-radio: RSC slot has a pending completion; supply request refused\n");
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
    bool idle = false;
    if (done) {
        for (unsigned i = 0; i < 1000; i++) {
            if (rd(t + 0x18, 0)) { idle = true; break; }
            pause_us(100);
        }
    }
    if (!done || !idle) {
        unavailable = true;
        redwood_tl("lemonade-radio: %s transaction did not complete idle; slot retained, reboot required\n", name);
        return false;
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
    retained_votes[vote].address = address;
    retained_votes[vote].millivolts = millivolts;
    redwood_tl("lemonade-radio: %s %u mV request complete\n", name, millivolts);
    return true;
}

bool lemonade_radio_supply_on(const char *name, unsigned millivolts) {
    if (!name || !name[0] || strlen(name) > 7 || !millivolts || millivolts > 3300 || !platform_ok())
        return false;
    lck_mtx_lock(&lemonade_radio_lock);
    if (!unavailable && !rsc) rsc = ml_io_map_unmappable(0x18220000, 0x10000, VM_WIMG_IO);
    if (!unavailable && !database) database = ml_io_map_unmappable(0x80860000, 0x20000, VM_WIMG_WCOMB);
    bool ok = !unavailable && rsc && database && supply_on_locked(name, millivolts);
    lck_mtx_unlock(&lemonade_radio_lock);
    return ok;
}
