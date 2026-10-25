#ifndef XNULOADER_LEMONADE_BATTERY_H
#define XNULOADER_LEMONADE_BATTERY_H

#include "efi.h"
#include "../xnu/files/bsd/sys/lemonade_battery.h"

struct lemonade_battery_ops {
    uint64_t (*ticks)(void);
    uint64_t (*frequency)(void);
    void (*publish)(const void *, size_t);
    void (*log)(const char *, ...);
};

struct lemonade_battery_cache *lemonade_battery_start(efi_boot_services *, const struct lemonade_battery_ops *);
void lemonade_battery_poll(void);
void lemonade_battery_stop(void);
void lemonade_battery_status(void);

#endif
