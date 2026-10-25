/*
 * OnePlus 9 battery telemetry shared by the resident loader and XNU.
 * No charger control or estimated SOC is carried by this interface.
 */
#ifndef LEMONADE_BATTERY_H
#define LEMONADE_BATTERY_H

#include <stdint.h>
#include <stddef.h>

#define LEMONADE_BATTERY_MAGIC UINT64_C(0x31545441424e4d4c)
#define LEMONADE_BATTERY_VERSION 2u
#define LEMONADE_BATTERY_DEVICE_BQ28Z610 0xffa5u
#define LEMONADE_BATTERY_SOC      (1u << 0)
#define LEMONADE_BATTERY_VOLTAGE  (1u << 1)
#define LEMONADE_BATTERY_CURRENT  (1u << 2)
#define LEMONADE_BATTERY_TEMP     (1u << 3)
#define LEMONADE_BATTERY_CHARGE   (1u << 4)
#define LEMONADE_BATTERY_FULL     (1u << 5)
#define LEMONADE_BATTERY_CYCLES   (1u << 6)
#define LEMONADE_BATTERY_EXTERNAL (1u << 7)
#define LEMONADE_BATTERY_STATUS   (1u << 8)
#define LEMONADE_BATTERY_REQUIRED (LEMONADE_BATTERY_SOC | LEMONADE_BATTERY_VOLTAGE | LEMONADE_BATTERY_TEMP)
#define LEMONADE_BATTERY_ALL      0x1ffu
#define LEMONADE_BATTERY_STALE_NS UINT64_C(15000000000)

/* BQ28Z610 TRM SLUUA65E, BatteryStatus() at 0x0a/0x0b. DSG includes
 * relaxed/non-charging mode; a positive noisy current is not charge status. */
#define LEMONADE_BQ_FULL         (1u << 5)
#define LEMONADE_BQ_DISCHARGING  (1u << 6)
#define LEMONADE_BQ_CHARGE_ALARM ((1u << 12) | (1u << 14) | (1u << 15))
enum lemonade_charge_state {
    LEMONADE_CHARGE_UNKNOWN = 0, LEMONADE_CHARGE_CHARGING = 1,
    LEMONADE_CHARGE_DISCHARGING = 2, LEMONADE_CHARGE_NOT_CHARGING = 3,
    LEMONADE_CHARGE_FULL = 4
};

struct lemonade_battery_sample {
    uint32_t valid;
    int32_t percent_hundredths;
    int32_t voltage_uv;              /* whole series pack, NOT highest cell */
    int32_t current_ua;              /* positive into the pack */
    int32_t temperature_hundredths;  /* degrees Celsius */
    int32_t charge_uah, full_uah;    /* series-pack coulomb capacity, no x2 */
    int32_t cycles, external;
    int32_t battery_status;         /* raw SBS word; check STATUS validity */
};

struct lemonade_battery_cache {
    uint64_t magic;
    uint32_t version, size;
    volatile uint32_t sequence;      /* odd during publication */
    uint32_t generation;
    uint64_t sample_id;              /* advances only for a valid measurement */
    uint64_t sample_ticks, ticks_per_second;
    uint32_t polls, failures, last_error, device_type;
    struct lemonade_battery_sample sample;
    uint32_t reserved;
};

/* UEFI may relocate the PE image on a 4 KiB boundary. The cache still fits
 * within one 16 KiB XNU mapping at every possible 4 KiB offset. */
static inline int lemonade_battery_address_valid(uint64_t physical)
{
    return physical >= UINT64_C(0x80000000) && physical < UINT64_C(0x400000000) &&
        !(physical & 0xfff) && (physical & 0x3fff) + sizeof(struct lemonade_battery_cache) <= 0x4000;
}

/* Decoder and freshness logic are shared with the host regression tests. */
static inline int
lemonade_battery_sample_valid(const struct lemonade_battery_sample *s)
{
    if ((s->valid & LEMONADE_BATTERY_REQUIRED) != LEMONADE_BATTERY_REQUIRED ||
        (s->valid & ~LEMONADE_BATTERY_ALL) ||
        s->percent_hundredths < 0 || s->percent_hundredths > 10000 ||
        s->voltage_uv < 4500000 || s->voltage_uv > 10000000 ||
        s->temperature_hundredths < -4100 || s->temperature_hundredths > 10000) return 0;
    if ((s->valid & LEMONADE_BATTERY_CURRENT) &&
        (s->current_ua < -32768000 || s->current_ua > 32767000)) return 0;
    if ((s->valid & LEMONADE_BATTERY_CHARGE) && (s->charge_uah < 0 || s->charge_uah > 10000000)) return 0;
    if ((s->valid & LEMONADE_BATTERY_FULL) && (s->full_uah <= 0 || s->full_uah > 10000000)) return 0;
    if ((s->valid & LEMONADE_BATTERY_CYCLES) && (s->cycles < 0 || s->cycles > 65535)) return 0;
    if ((s->valid & LEMONADE_BATTERY_EXTERNAL) && (s->external != 0 && s->external != 1)) return 0;
    if ((s->valid & LEMONADE_BATTERY_STATUS) &&
        (s->battery_status < 0 || s->battery_status > 65535 || (s->battery_status & 0xf))) return 0;
    return 1;
}

static inline int lemonade_battery_charge_state(const struct lemonade_battery_sample *s)
{
    if (!(s->valid & LEMONADE_BATTERY_EXTERNAL)) return LEMONADE_CHARGE_UNKNOWN;
    if (!s->external) return LEMONADE_CHARGE_DISCHARGING;
    if (!(s->valid & LEMONADE_BATTERY_STATUS)) return LEMONADE_CHARGE_UNKNOWN;
    if (s->battery_status & LEMONADE_BQ_FULL) return LEMONADE_CHARGE_FULL;
    if (s->battery_status & (LEMONADE_BQ_DISCHARGING | LEMONADE_BQ_CHARGE_ALARM))
        return LEMONADE_CHARGE_NOT_CHARGING;
    return LEMONADE_CHARGE_CHARGING;
}

struct lemonade_battery_tracker {
    uint32_t generation;
    uint64_t sample_id, accepted_ns, age_at_accept_ns;
    int have_sample;
};

/* Both cores read CNTVCT_EL0. Reject an already-old sample at first attachment. */
static inline int
lemonade_battery_recent(const struct lemonade_battery_cache *c, uint64_t ticks, uint64_t frequency)
{
    return frequency && frequency <= UINT64_C(1000000000) &&
        c->ticks_per_second == frequency && ticks >= c->sample_ticks &&
        ticks - c->sample_ticks < frequency * 15;
}

/* 1: new sample, 0: repeated/invalid, -1: a changed producer generation. */
static inline int
lemonade_battery_accept(struct lemonade_battery_tracker *t,
    const struct lemonade_battery_cache *c, uint64_t now_ns, uint64_t ticks, uint64_t frequency)
{
    if (c->magic != LEMONADE_BATTERY_MAGIC || c->version != LEMONADE_BATTERY_VERSION ||
        c->size != sizeof(*c) || (c->sequence & 1) || !c->generation) return 0;
    if (t->generation && t->generation != c->generation) {
        if ((int32_t)(c->generation - t->generation) <= 0) return 0;
        t->generation = c->generation;
        t->sample_id = 0;
        t->have_sample = 0;
        return -1;
    }
    if (c->device_type != LEMONADE_BATTERY_DEVICE_BQ28Z610 ||
        !lemonade_battery_recent(c, ticks, frequency) ||
        !c->sample_id || !lemonade_battery_sample_valid(&c->sample) ||
        (t->have_sample && c->sample_id <= t->sample_id)) return 0;
    t->generation = c->generation;
    t->sample_id = c->sample_id;
    t->accepted_ns = now_ns;
    t->age_at_accept_ns = (ticks - c->sample_ticks) * UINT64_C(1000000000) / frequency;
    t->have_sample = 1;
    return 1;
}

static inline int
lemonade_battery_fresh(const struct lemonade_battery_tracker *t, uint64_t now_ns)
{
    return t->have_sample && now_ns >= t->accepted_ns &&
        now_ns - t->accepted_ns < LEMONADE_BATTERY_STALE_NS - t->age_at_accept_ns;
}

#endif
