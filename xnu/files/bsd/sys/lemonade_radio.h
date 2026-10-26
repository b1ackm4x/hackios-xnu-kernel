#ifndef LEMONADE_RADIO_H
#define LEMONADE_RADIO_H
#include <stdbool.h>

/* Sleepable/thread context only. Physical resource names refer to cmd-db;
 * callers sharing the same regulator retain its vote for this RAM session.
 * Failure after submission quarantines the slot until reboot. */
bool lemonade_radio_supply_on(const char *name, unsigned millivolts);
#endif
