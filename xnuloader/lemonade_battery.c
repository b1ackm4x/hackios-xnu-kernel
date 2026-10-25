/*
 * Read BQ28Z610 telemetry through the resident Qualcomm I2C driver.
 * Only the nonpersistent DEVICE_TYPE query is written. No charger control,
 * unseal, reset, calibration, data-flash, or protection command is used.
 * Caller owns the UEFI context on core 0 and serializes these operations.
 */
#include "lemonade_battery.h"

#define BQ_INSTANCE 3u       /* firmware /dev/i2c3: QUPv3 SE2 on lemonade */
#define I2C_REVISION UINT64_C(0x10000)
#define ERROR_PROTOCOL 0x10000u
#define ERROR_SHORT_READ 0x10001u
#define ERROR_DEVICE 0x10002u
#define ERROR_SAMPLE 0x10003u

static struct lemonade_battery_cache cache __attribute__((aligned(0x2000))) = {
    .magic = LEMONADE_BATTERY_MAGIC, .version = LEMONADE_BATTERY_VERSION, .size = sizeof(cache)
};
static struct lemonade_battery_ops ops;
static efi_qcom_i2c *i2c;
static efi_charger_ex *charger_ex;
static void *gauge;
static const efi_qcom_i2c_config config = {100, 0x55, 0, 10000, 0, 0};
static uint32_t last_error;

static void publish(void) { if (ops.publish) ops.publish(&cache, sizeof(cache)); }
static void barrier(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
/* The consumer maps uncached RAM. Publish the odd marker before changing data,
 * and flush all data while still odd before publishing the even marker. */
static void begin(void) { cache.sequence++; barrier(); publish(); }
static void end(void) { barrier(); publish(); cache.sequence++; barrier(); publish(); }

static int read_word(uint8_t reg, uint16_t *out)
{
    uint8_t b[2] = {0};
    uint32_t count = 0;
    uint32_t status = i2c->read(gauge, &config, reg, 1, b, 2, &count, 50);
    if (status || count != 2) {
        last_error = status ? status : ERROR_SHORT_READ;
        return 0;
    }
    *out = (uint16_t)b[0] | (uint16_t)b[1] << 8;
    return 1;
}

void lemonade_battery_stop(void)
{
    if (gauge && i2c) i2c->close(gauge);
    gauge = NULL;
}

struct lemonade_battery_cache *
lemonade_battery_start(efi_boot_services *bs, const struct lemonade_battery_ops *callbacks)
{
    static const efi_guid guid = QCOM_I2C_PROTOCOL_GUID;
    static const efi_guid ex_guid = QCOM_CHARGER_EX_GUID;
    efi_status status;
    uint16_t device = 0;
    uint32_t count = 0, result;
    const uint8_t query[2] = {1, 0};  /* Control(DEVICE_TYPE), read-only subcommand */

    lemonade_battery_stop();
    ops = *callbacks;
    i2c = NULL; charger_ex = NULL; last_error = 0;
    begin();
    cache.generation++;
    if (!cache.generation) cache.generation = 1;
    cache.sample_id = 0; cache.sample.valid = 0; cache.device_type = 0;
    cache.polls = 0; cache.failures = 0; cache.last_error = 0;
    cache.sample_ticks = 0; cache.ticks_per_second = ops.frequency();
    end();
    status = bs->locate_protocol(&guid, NULL, (void **)&i2c);
    if (EFI_ERROR(status) || !i2c || i2c->revision != I2C_REVISION ||
        !i2c->open || !i2c->read || !i2c->write || !i2c->close) {
        last_error = ERROR_PROTOCOL;
        goto failed;
    }
    result = i2c->open(BQ_INSTANCE, &gauge);
    if (result || !gauge) { last_error = result ? result : ERROR_PROTOCOL; goto failed; }
    /* The real gauge can NACK its first address after firmware starts the bus.
     * Retry only that transient status, at most three attempts. Repeating this
     * read-only query cannot change gauge configuration. Other errors fail closed. */
    for (unsigned attempt = 0;; attempt++) {
        count = 0;
        result = i2c->write(gauge, &config, 0x00, 1, query, sizeof(query), &count, 50);
        if (result != 22 || attempt == 2) break; /* I2C_ERROR_ADDR_NACK */
        bs->stall(20000);
    }
    if (result || count != sizeof(query)) { last_error = result ? result : ERROR_SHORT_READ; goto failed; }
    bs->stall(1000);
    if (!read_word(0x00, &device)) goto failed;
    begin(); cache.device_type = device; end();
    if (device != LEMONADE_BATTERY_DEVICE_BQ28Z610) { last_error = ERROR_DEVICE; goto failed; }
    /* ChargerEx has a public status-only ABI. Do not use QcomCharger Initialize/TakeAction. */
    status = bs->locate_protocol(&ex_guid, NULL, (void **)&charger_ex);
    /* lemonade's pinned ChargerExDxe uses 1.4; the queried first slot has
     * the same BOOLEAN* ABI as the public 1.3 header. No control slots used. */
    if (EFI_ERROR(status) || !charger_ex ||
        (charger_ex->revision != UINT64_C(0x10003) && charger_ex->revision != UINT64_C(0x10004))) charger_ex = NULL;
    lemonade_battery_poll();
    lemonade_battery_status();
    return &cache;
failed:
    begin(); cache.last_error = last_error; cache.failures++; end();
    lemonade_battery_stop();
    lemonade_battery_status();
    return &cache;
}

void lemonade_battery_poll(void)
{
    struct lemonade_battery_sample sample = {0};
    uint16_t value;
    uint8_t external = 0;
    if (!gauge) return;
    last_error = 0;
    if (read_word(0x2c, &value) && value <= 100) {
        sample.percent_hundredths = value * 100;
        sample.valid |= LEMONADE_BATTERY_SOC;
    }
    if (read_word(0x08, &value) && value >= 4500 && value <= 10000) {
        sample.voltage_uv = value * 1000;
        sample.valid |= LEMONADE_BATTERY_VOLTAGE;
    }
    if (read_word(0x06, &value) && value >= 2331 && value <= 3731) {
        sample.temperature_hundredths = (int32_t)value * 10 - 27315;
        sample.valid |= LEMONADE_BATTERY_TEMP;
    }
    if (read_word(0x14, &value)) {
        /* TI's signed current is already positive into the pack. The Android
         * vendor getter negates it; we do NOT call that getter here. */
        sample.current_ua = (value & 0x8000 ? (int32_t)value - 65536 : (int32_t)value) * 1000;
        sample.valid |= LEMONADE_BATTERY_CURRENT;
    }
    if (read_word(0x10, &value) && value <= 10000) {
        sample.charge_uah = value * 1000;
        sample.valid |= LEMONADE_BATTERY_CHARGE;
    }
    if (read_word(0x12, &value) && value > 0 && value <= 10000) {
        sample.full_uah = value * 1000;
        sample.valid |= LEMONADE_BATTERY_FULL;
    }
    if (read_word(0x2a, &value)) {
        sample.cycles = value;
        sample.valid |= LEMONADE_BATTERY_CYCLES;
    }
    if (read_word(0x0a, &value) && !(value & 0xf)) {
        sample.battery_status = value;
        sample.valid |= LEMONADE_BATTERY_STATUS;
    }
    if (charger_ex && charger_ex->get_charger_presence &&
        !EFI_ERROR(charger_ex->get_charger_presence(&external)) && external <= 1) {
        sample.external = external;
        sample.valid |= LEMONADE_BATTERY_EXTERNAL;
    }
    begin();
    cache.polls++;
    if (lemonade_battery_sample_valid(&sample)) {
        cache.sample = sample;
        cache.sample_id++;
        cache.sample_ticks = ops.ticks();
    } else if (!last_error) last_error = ERROR_SAMPLE;
    if (last_error) cache.failures++;
    cache.last_error = last_error;
    end();
}

void lemonade_battery_status(void)
{
    if (!ops.log) return;
    /* The loader's tiny formatter treats %d as uint64_t. Keep signed sensor
     * values readable without changing formatting for the other platforms. */
    int64_t current = cache.sample.current_ua, temperature = cache.sample.temperature_hundredths;
    const char *external = !(cache.sample.valid & LEMONADE_BATTERY_EXTERNAL) ? "unknown" :
        cache.sample.external ? "1" : "0";
    ops.log("lemonade-battery: gauge=%x sample=%d SOC=%d/100%% V=%d uV I=%s%d uA T=%s%d/100C valid=%x polls=%d failures=%d error=%x external=%s status=%x charge_state=%d\n",
        (uint64_t)cache.device_type, cache.sample_id, (uint64_t)(int64_t)cache.sample.percent_hundredths,
        (uint64_t)(int64_t)cache.sample.voltage_uv, current < 0 ? "-" : "", (uint64_t)(current < 0 ? -current : current),
        temperature < 0 ? "-" : "", (uint64_t)(temperature < 0 ? -temperature : temperature), (uint64_t)cache.sample.valid,
        (uint64_t)cache.polls, (uint64_t)cache.failures, (uint64_t)cache.last_error,
        external,
        (uint64_t)cache.sample.battery_status, (uint64_t)lemonade_battery_charge_state(&cache.sample));
}
