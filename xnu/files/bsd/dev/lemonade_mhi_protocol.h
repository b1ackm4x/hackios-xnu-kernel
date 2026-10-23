/* MHI wire layouts, independently described from the public host interface.
 * Contexts have 4-byte alignment, including the 64-bit ring fields. */
#ifndef LEMONADE_MHI_PROTOCOL_H
#define LEMONADE_MHI_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>
struct lemonade_mhi_context {
    uint32_t config, type, index;
    uint64_t base, length, read, write;
} __attribute__((packed, aligned(4)));
struct lemonade_mhi_element { uint64_t pointer; uint32_t word0, word1; };
struct lemonade_bhie_vector { uint64_t address, length; };
_Static_assert(sizeof(struct lemonade_mhi_context) == 44, "MHI context stride");
_Static_assert(offsetof(struct lemonade_mhi_context, read) == 28, "MHI RP offset");
_Static_assert(offsetof(struct lemonade_mhi_context, write) == 36, "MHI WP offset");
_Static_assert(sizeof(struct lemonade_mhi_element) == 16, "MHI element stride");
_Static_assert(sizeof(struct lemonade_bhie_vector) == 16, "BHIE vector stride");
static inline int lemonade_mhi_pointer_valid(uint64_t p, uint64_t base, unsigned count) {
    return count >= 2 && count <= 4096 && p >= base && p - base < (uint64_t)count * 16 && !(p & 15);
}
static inline unsigned lemonade_mhi_next(unsigned position, unsigned count) {
    return position + 1 == count ? 0 : position + 1;
}
#endif
