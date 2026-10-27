/* Hardware admission for the imported SM7325 modem experiment. */
#ifndef REDWOOD_MODEM_POLICY_H
#define REDWOOD_MODEM_POLICY_H
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
static inline bool redwood_modem_platform_allowed(const void *type, size_t size)
{
    return type != NULL && size == sizeof("redwood") &&
        memcmp(type, "redwood", sizeof("redwood")) == 0;
}
#endif
