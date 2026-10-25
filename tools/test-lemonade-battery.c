/* Exercise the production loader backend against a strict fake gauge. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../xnuloader/lemonade_battery.h"

_Static_assert(sizeof(struct lemonade_battery_sample) == 40, "sample ABI");
_Static_assert(sizeof(struct lemonade_battery_cache) == 112, "cache ABI");
_Static_assert(offsetof(struct lemonade_battery_cache, sample) == 64, "cache ABI offset");
_Static_assert(sizeof(efi_qcom_i2c_config) == 24, "firmware ABI");

static uint16_t regs[256];
static int opens, closes, reads, writes, fail_reg = -1, short_reg = -1, ex_fail;
static int open_fail, locate_fail, query_fail, short_query;
static unsigned query_nacks;
static uint8_t plugged = 1;
static uint64_t ticks = 1000;
static uint64_t fake_ticks(void) { return ticks; }
static uint64_t fake_frequency(void) { return 1000; }
static void publish(const void *data, size_t size) { assert(data && size == sizeof(struct lemonade_battery_cache)); }
static uint32_t open_bus(uint32_t instance, void **handle)
{
    assert(instance == 3); opens++;
    *handle = open_fail ? NULL : regs;
    return open_fail;
}
static uint32_t close_bus(void *handle) { assert(handle == regs); closes++; return 0; }
static void check_config(void *handle, const efi_qcom_i2c_config *c, uint16_t offset_length, uint16_t length, uint32_t timeout)
{
    assert(handle == regs && c->address == 0x55 && c->frequency_khz == 100 && c->mode == 0);
    assert(offset_length == 1 && length == 2 && timeout == 50);
}
static uint32_t read_bus(void *h, const efi_qcom_i2c_config *c, uint16_t offset,
    uint16_t offset_length, uint8_t *buffer, uint16_t length, uint32_t *count, uint32_t timeout)
{
    check_config(h, c, offset_length, length, timeout);
    assert(offset == 0 || offset == 0x2c || offset == 8 || offset == 6 || offset == 0x14 ||
        offset == 0x10 || offset == 0x12 || offset == 0x2a || offset == 0x0a);
    reads++;
    if (offset == fail_reg) { *count = 0; return 10; }
    buffer[0] = regs[offset] & 255; buffer[1] = regs[offset] >> 8;
    *count = offset == short_reg ? 1 : 2;
    return 0;
}
static uint32_t write_bus(void *h, const efi_qcom_i2c_config *c, uint16_t offset,
    uint16_t offset_length, const uint8_t *buffer, uint16_t length, uint32_t *count, uint32_t timeout)
{
    check_config(h, c, offset_length, length, timeout);
    /* Any configuration, unseal, reset or charger write fails this test. */
    assert(offset == 0 && buffer[0] == 1 && buffer[1] == 0);
    writes++;
    if (query_nacks) { query_nacks--; *count = 0; return 22; }
    *count = short_query ? 1 : 2; return query_fail;
}
static efi_status presence(uint8_t *out) { *out = plugged; return ex_fail ? EFI_ERROR_BIT | 7 : 0; }
static efi_qcom_i2c protocol = {0x10000, open_bus, read_bus, write_bus, NULL, close_bus};
static efi_charger_ex ex_protocol = {.revision = 0x10004, .get_charger_presence = presence};
static efi_status locate(const efi_guid *g, void *registration, void **out)
{
    static const efi_guid i2c_guid = QCOM_I2C_PROTOCOL_GUID, ex_guid = QCOM_CHARGER_EX_GUID;
    assert(registration == NULL);
    if (!memcmp(g, &i2c_guid, sizeof(*g))) {
        *out = locate_fail ? NULL : &protocol;
        return locate_fail ? EFI_ERROR_BIT | 14 : 0;
    }
    assert(!memcmp(g, &ex_guid, sizeof(*g)));
    *out = &ex_protocol; return 0;
}
static efi_status stall(uint64_t us) { assert(us == 1000 || us == 20000); return 0; }
static efi_boot_services services = {.locate_protocol = locate, .stall = stall};
static const struct lemonade_battery_ops callbacks = {fake_ticks, fake_frequency, publish, NULL};
static struct lemonade_battery_cache *start(void) { return lemonade_battery_start(&services, &callbacks); }

int main(void)
{
    assert(lemonade_battery_address_valid(UINT64_C(0xa025d000)));
    assert(lemonade_battery_address_valid(UINT64_C(0x3fffff000)));
    assert(!lemonade_battery_address_valid(UINT64_C(0x400000000)));
    assert(!lemonade_battery_address_valid(UINT64_C(0xa025d004)));
    assert(!lemonade_battery_address_valid(0));
    struct lemonade_battery_tracker tracker = {0};
    struct lemonade_battery_cache *c, copy;
    regs[0] = 0xffa5; regs[0x2c] = 73; regs[8] = 7820; regs[6] = 2981;
    regs[0x14] = (uint16_t)-420; regs[0x10] = 1520; regs[0x12] = 2200; regs[0x2a] = 0;
    regs[0x0a] = 0xc0; /* initialized, relaxed/discharging */
    c = start();
    assert(c->sample_id == 1 && !(c->sequence & 1) && c->sample.valid == LEMONADE_BATTERY_ALL);
    assert(c->sample.percent_hundredths == 7300 && c->sample.voltage_uv == 7820000);
    assert(c->sample.current_ua == -420000 && c->sample.temperature_hundredths == 2495);
    assert(c->sample.charge_uah == 1520000 && c->sample.full_uah == 2200000 && c->sample.cycles == 0);
    assert(opens == 1 && writes == 1 && reads == 9);
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_NOT_CHARGING);
    assert(lemonade_battery_accept(&tracker, c, 0, ticks, 1000) == 1);
    copy = *c;
    copy.sequence |= 1;
    assert(!lemonade_battery_accept(&tracker, &copy, 1, ticks, 1000));
    copy = *c; copy.version++;
    assert(!lemonade_battery_accept(&tracker, &copy, 1, ticks, 1000));
    assert(!lemonade_battery_accept(&tracker, c, 1, ticks, 2000));
    /* Failed reads do not extend freshness, even though publication advances. */
    fail_reg = 0x2c; ticks += 5000; lemonade_battery_poll();
    assert(c->sample_id == 1 && c->failures == 1);
    assert(!lemonade_battery_accept(&tracker, c, UINT64_C(5000000000), ticks, 1000));
    assert(lemonade_battery_fresh(&tracker, UINT64_C(14999999999)));
    assert(!lemonade_battery_fresh(&tracker, UINT64_C(15000000000)));
    struct lemonade_battery_tracker late = {0};
    assert(!lemonade_battery_accept(&late, c, 0, c->sample_ticks + 15000, 1000));
    assert(lemonade_battery_accept(&late, c, 0, c->sample_ticks + 14000, 1000) == 1);
    assert(lemonade_battery_fresh(&late, UINT64_C(999999999)));
    assert(!lemonade_battery_fresh(&late, UINT64_C(1000000000)));
    fail_reg = -1; short_reg = 8; lemonade_battery_poll();
    assert(c->sample_id == 1);
    short_reg = -1; regs[0x2c] = 101; lemonade_battery_poll();
    assert(c->sample_id == 1);
    regs[0x2c] = 0; regs[0x10] = 0; regs[0x14] = 500; ticks += 20000;
    regs[0x0a] = 0x80;
    lemonade_battery_poll();
    assert(c->sample_id == 2 && c->sample.percent_hundredths == 0 && c->sample.charge_uah == 0);
    assert(c->sample.current_ua == 500000 && c->sample.cycles == 0);
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_CHARGING);
    assert(lemonade_battery_accept(&tracker, c, UINT64_C(25000000000), ticks, 1000) == 1);
    /* Optional field loss clears validity but preserves usable real SOC. */
    fail_reg = 0x14; ex_fail = 1; lemonade_battery_poll();
    assert(c->sample_id == 3 && !(c->sample.valid & (LEMONADE_BATTERY_CURRENT | LEMONADE_BATTERY_EXTERNAL)));
    fail_reg = -1; ex_fail = 0;
    copy = *c;
    c = start();
    assert(closes == 1 && c->sample_id == 1 && c->generation != copy.generation);
    assert(lemonade_battery_accept(&tracker, c, UINT64_C(26000000000), ticks, 1000) == -1);
    assert(!lemonade_battery_fresh(&tracker, UINT64_C(26000000000)));
    assert(!lemonade_battery_accept(&tracker, &copy, UINT64_C(26000000000), ticks, 1000));
    assert(lemonade_battery_accept(&tracker, c, UINT64_C(27000000000), ticks, 1000) == 1);
    /* Wrong model/revision: no telemetry reads after identification fails. */
    regs[0] = 0x0541; int before = reads;
    c = start(); assert(c->sample_id == 0 && reads == before + 1 && c->last_error);
    regs[0] = 0xffa5; protocol.revision++; before = opens;
    c = start(); assert(c->sample_id == 0 && opens == before && c->last_error);
    protocol.revision--; locate_fail = 1;
    c = start(); assert(c->sample_id == 0 && opens == before);
    locate_fail = 0; open_fail = 1; before = writes;
    c = start(); assert(c->sample_id == 0 && writes == before);
    open_fail = 0; short_query = 1; before = reads;
    c = start(); assert(c->sample_id == 0 && reads == before);
    short_query = 0; query_fail = 10;
    c = start(); assert(c->sample_id == 0 && reads == before);
    query_fail = 0;
    query_nacks = 1; before = writes;
    c = start(); assert(c->sample_id == 1 && writes == before + 2);
    query_nacks = 4; before = writes;
    c = start(); assert(c->sample_id == 0 && writes == before + 3);
    query_nacks = 0;
    c = start(); assert(c->sample_id == 1);
    /* Charging is a gauge state, not a sign test on a noisy average current. */
    regs[0x0a] = 0xc0; regs[0x14] = 4; lemonade_battery_poll();
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_NOT_CHARGING);
    regs[0x0a] = 0x80; regs[0x14] = (uint16_t)-4; lemonade_battery_poll();
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_CHARGING);
    regs[0x0a] = 0xe0; lemonade_battery_poll();
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_FULL);
    plugged = 0; lemonade_battery_poll();
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_DISCHARGING);
    plugged = 1; regs[0x0a] = 0x4080; lemonade_battery_poll();
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_NOT_CHARGING);
    for (unsigned i = 0; i < 3; i++) {
        fail_reg = i == 0 ? 0x0a : -1; short_reg = i == 1 ? 0x0a : -1;
        regs[0x0a] = i == 2 ? 0x81 : 0x80; lemonade_battery_poll();
        assert(!(c->sample.valid & LEMONADE_BATTERY_STATUS));
        assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_UNKNOWN);
    }
    fail_reg = short_reg = -1; regs[0x0a] = 0x80;
    ex_protocol.revision = 0x10005; c = start();
    assert(!(c->sample.valid & LEMONADE_BATTERY_EXTERNAL));
    assert(lemonade_battery_charge_state(&c->sample) == LEMONADE_CHARGE_UNKNOWN);
    ex_protocol.revision = 0x10003; c = start();
    assert(c->sample.valid & LEMONADE_BATTERY_EXTERNAL);
    lemonade_battery_stop();
    before = reads; lemonade_battery_poll(); assert(reads == before);
    puts("PASS: production battery backend, units, 0%/zero cycles, field loss, stale/recovery, ABI and model rejection");
    return 0;
}
