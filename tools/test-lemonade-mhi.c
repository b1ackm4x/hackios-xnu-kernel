/* Host-only wire format and untrusted ring-pointer checks. */
#include <assert.h>
#include <string.h>
#include "../xnu/files/bsd/dev/lemonade_mhi_protocol.h"
int main(void) {
    const uint64_t base = 0xa0203000ull;
    assert(lemonade_mhi_pointer_valid(base, base, 32));
    assert(lemonade_mhi_pointer_valid(base + 31 * 16, base, 32));
    assert(!lemonade_mhi_pointer_valid(base - 16, base, 32));
    assert(!lemonade_mhi_pointer_valid(base + 32 * 16, base, 32));
    assert(!lemonade_mhi_pointer_valid(base + 1, base, 32));
    assert(!lemonade_mhi_pointer_valid(UINT64_MAX, base, 32));
    assert(!lemonade_mhi_pointer_valid(base, base, 0));
    unsigned position = 0;
    for (unsigned i = 0; i < 1024; i++) {
        assert(position == i % 32);
        position = lemonade_mhi_next(position, 32);
    }
    struct lemonade_mhi_context contexts[2] = {0};
    contexts[1].base = base;
    contexts[1].read = base + 16;
    const unsigned char *bytes = (const void *)contexts;
    uint64_t from_wire = 0;
    memcpy(&from_wire, bytes + 44 + 28, 8);
    assert(from_wire == base + 16);
    return 0;
}
