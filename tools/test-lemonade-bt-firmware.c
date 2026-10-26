#include "../xnu/files/bsd/dev/lemonade_bt_firmware.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void put32(uint8_t *p, unsigned v) {
    for (unsigned i = 0; i < 4; i++) p[i] = v >> (i * 8);
}
static size_t tag(uint8_t *p, unsigned id, unsigned n, uint8_t value) {
    memset(p, 0, 12 + n); p[0] = id; p[2] = n;
    memset(p + 12, value, n); return 12 + n;
}
int main(void) {
    uint8_t patch[64] = {0}; struct lemonade_bt_patch result = {0};
    put32(patch, ((sizeof(patch) - 4) << 8) | 1);
    patch[14] = 3; patch[16] = 19; patch[19] = 1; patch[20] = 7;
    assert(lemonade_bt_patch_info(patch, sizeof(patch), &result));
    assert(result.product == 19 && result.rom == 256 && result.patch == 7 && result.mode == 3);
    for (size_t n = 0; n < sizeof(patch); n++) assert(!lemonade_bt_patch_info(patch, n, &result));
    patch[14] = 2; assert(!lemonade_bt_patch_info(patch, sizeof(patch), &result));
    patch[14] = 0; assert(lemonade_bt_patch_info(patch, sizeof(patch), &result));
    uint8_t nvm[256] = {0}, original[256]; size_t end = 4;
    end += tag(nvm + end, 2, 6, 0x22);
    end += tag(nvm + end, 17, 3, 0xff);
    end += tag(nvm + end, 27, 1, 0xff);
    end += tag(nvm + end, 99, 16, 0xa5);
    put32(nvm, ((end - 4) << 8) | 2);
    assert(lemonade_bt_nvm_validate(nvm, end)); memcpy(original, nvm, sizeof(nvm));
    /* Synthetic fixture only; no device identity is embedded in this test. */
    const uint8_t address[6] = {1,2,3,4,5,2};
    const uint8_t zero[6] = {0}, multicast[6] = {1,2,3,4,5,1};
    assert(!lemonade_bt_nvm_prepare(nvm, end, zero));
    assert(!lemonade_bt_nvm_prepare(nvm, end, multicast));
    assert(!memcmp(original, nvm, end));
    for (size_t n = 0; n < end; n++) assert(!lemonade_bt_nvm_validate(nvm, n));
    assert(lemonade_bt_nvm_prepare(nvm, end, address));
    memcpy(original + 16, address, 6); original[34] = 0x7f; original[35] = 0;
    original[49] = 0xfe;
    assert(!memcmp(nvm, original, end)); /* including all calibration bytes */
    uint8_t bundle[300] = {0}; memcpy(bundle + 4, nvm, end);
    put32(bundle, (end << 8) | 4);
    assert(lemonade_bt_nvm_validate(bundle, end + 4));
    assert(lemonade_bt_nvm_prepare(bundle, end + 4, address));
    bundle[6] = 0xff; assert(!lemonade_bt_nvm_validate(bundle, end + 4));
    memcpy(bundle + 4, nvm, end); bundle[4] = 4;
    assert(!lemonade_bt_nvm_validate(bundle, end + 4)); /* nested bundles rejected */
    nvm[6] = 0xff; assert(!lemonade_bt_nvm_validate(nvm, end)); /* overflowing tag */
    memcpy(nvm, original, end); nvm[4 + 18] = 2; /* duplicate address tag */
    assert(!lemonade_bt_nvm_validate(nvm, end));
    puts("Firmware TLV extents, nested bounds, duplicate tags and calibration-preserving RAM edits passed");
}
