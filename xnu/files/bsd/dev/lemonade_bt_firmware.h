/* Qualcomm TLV validation for the exact phone's private RAM firmware inputs.
 * No file contents, addresses, calibration data or keys are logged here. */
#ifndef LEMONADE_BT_FIRMWARE_H
#define LEMONADE_BT_FIRMWARE_H
#include "lemonade_bt_protocol.h"

struct lemonade_bt_patch {
    uint16_t product, rom, patch;
    uint8_t mode;
};

static inline int lemonade_bt_tlv_extent(const uint8_t *p, size_t size, unsigned type) {
    return p && size >= 4 && p[0] == type &&
        (lemonade_bt_le32(p) >> 8) == size - 4;
}

static inline int lemonade_bt_patch_info(const uint8_t *p, size_t size,
                                         struct lemonade_bt_patch *result) {
    if (!result || size < 28 || size > 2 * 1024 * 1024 ||
        !lemonade_bt_tlv_extent(p, size, 1)) return 0;
    /* Header offsets include the four-byte TLV prefix. Only the two download
     * policies implemented by this transport are accepted. */
    if (p[14] != 0 && p[14] != 3) return 0;
    if (!lemonade_bt_le16(p + 18) || !lemonade_bt_le16(p + 20)) return 0;
    result->product = lemonade_bt_le16(p + 16);
    result->rom = lemonade_bt_le16(p + 18);
    result->patch = lemonade_bt_le16(p + 20);
    result->mode = p[14];
    return 1;
}

/* Validate every tag before permitting changes. Type 4 is a bundle; each
 * child has its own strict TLV extent. Unknown child types are preserved. */
static inline int lemonade_bt_nvm_validate(const uint8_t *p, size_t size) {
    if (!p || size < 4 || size > 65536) return 0;
    if (p[0] == 4) {
        if (!lemonade_bt_tlv_extent(p, size, 4)) return 0;
        size_t offset = 4; unsigned nvm_children = 0;
        while (offset < size) {
            if (size - offset < 4) return 0;
            size_t child = (lemonade_bt_le32(p + offset) >> 8) + 4;
            if (child > size - offset || p[offset] == 4) return 0;
            if (p[offset] == 2) {
                if (!lemonade_bt_nvm_validate(p + offset, child)) return 0;
                nvm_children++;
            }
            offset += child;
        }
        return nvm_children == 1;
    }
    if (!lemonade_bt_tlv_extent(p, size, 2)) return 0;
    size_t offset = 4; unsigned address = 0, transport = 0, sleep = 0;
    while (offset < size) {
        if (size - offset < 12) return 0;
        unsigned tag = lemonade_bt_le16(p + offset);
        size_t length = lemonade_bt_le16(p + offset + 2);
        if (length > size - offset - 12) return 0;
        if (tag == 2 && (++address != 1 || length != 6)) return 0;
        if (tag == 17 && (++transport != 1 || length < 3)) return 0;
        if (tag == 27 && (++sleep != 1 || length < 1)) return 0;
        offset += 12 + length;
    }
    return address == 1 && transport == 1 && sleep == 1;
}

/* This modifies a private RAM copy only. Keep the radio awake at 115200 for
 * initial bring-up; preserve all unrelated calibration and coexistence tags.
 * The address must be read privately from this same phone, never invented. */
static inline int lemonade_bt_nvm_prepare(uint8_t *p, size_t size, const uint8_t address[6]) {
    if (!address || !lemonade_bt_nvm_validate(p, size)) return 0;
    unsigned nonzero = 0, not_ff = 0;
    for (unsigned i = 0; i < 6; i++) { nonzero |= address[i]; not_ff |= address[i] ^ 0xff; }
    if (!nonzero || !not_ff || (address[5] & 1)) return 0; /* HCI little-endian address */
    if (p[0] == 4) {
        size_t offset = 4;
        while (offset < size) {
            size_t child = (lemonade_bt_le32(p + offset) >> 8) + 4;
            if (p[offset] == 2) return lemonade_bt_nvm_prepare(p + offset, child, address);
            offset += child;
        }
        return 0;
    }
    for (size_t offset = 4; offset < size;) {
        unsigned tag = lemonade_bt_le16(p + offset);
        size_t length = lemonade_bt_le16(p + offset + 2);
        uint8_t *data = p + offset + 12;
        if (tag == 2) for (unsigned i = 0; i < 6; i++) data[i] = address[i];
        if (tag == 17) { data[0] &= ~0x80u; data[1] = 0; }
        if (tag == 27) data[0] &= ~1u;
        offset += 12 + length;
    }
    return 1;
}
#endif
