/* Samsung S6SY792 eight-byte coordinate wire format, independent of C bitfields. */
#ifndef LEMONADE_TOUCH_PROTOCOL_H
#define LEMONADE_TOUCH_PROTOCOL_H
#include <stdint.h>
#include <stdbool.h>

struct lemonade_contact { uint32_t finger, action, x, y; };
static inline bool
lemonade_decode_contact(const uint8_t event[8], struct lemonade_contact *out)
{
    unsigned id = (event[0] >> 2) & 15;
    unsigned action = event[0] >> 6;
    unsigned x = ((unsigned)event[1] << 4) | (event[3] >> 4);
    unsigned y = ((unsigned)event[2] << 4) | (event[3] & 15);
    if ((event[0] & 3) || id < 1 || id > 10 || action < 1 || x > 1079 || y > 2399) {
        return false;
    }
    out->finger = id - 1;
    out->action = action;
    out->x = (x * 65535u + 539u) / 1079u;
    out->y = (y * 65535u + 1199u) / 2399u;
    return true;
}
#endif
