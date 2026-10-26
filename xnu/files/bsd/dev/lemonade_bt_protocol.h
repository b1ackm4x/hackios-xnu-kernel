/*
 * Bounded H4 framing shared by the GENI transport and its host tests.
 * Packet lengths follow the Bluetooth HCI UART framing. Qualcomm IBS is
 * recognized ONLY between packets, never by searching inside a payload.
 * No controller registers or operating-system services are used here.
 */
#ifndef LEMONADE_BT_PROTOCOL_H
#define LEMONADE_BT_PROTOCOL_H
#include <stddef.h>
#include <stdint.h>

#define LEMONADE_BT_MAX_PACKET 4101u
#define LEMONADE_BT_IBS_WAKE 0xfdu
#define LEMONADE_BT_IBS_ACK 0xfcu
#define LEMONADE_BT_IBS_SLEEP 0xfeu
#define LEMONADE_BT_TYPE(type) (1u << (type))
#define LEMONADE_BT_FROM_CONTROLLER (LEMONADE_BT_TYPE(2) | LEMONADE_BT_TYPE(3) | \
                                    LEMONADE_BT_TYPE(4) | LEMONADE_BT_TYPE(5))
#define LEMONADE_BT_FROM_HOST (LEMONADE_BT_TYPE(1) | LEMONADE_BT_TYPE(2) | \
                              LEMONADE_BT_TYPE(3) | LEMONADE_BT_TYPE(5))

enum lemonade_bt_parse_error {
    LEMONADE_BT_PARSE_OK,
    LEMONADE_BT_PARSE_TYPE,
    LEMONADE_BT_PARSE_LENGTH,
    LEMONADE_BT_PARSE_DELIVERY,
};

struct lemonade_bt_parser {
    uint8_t packet[LEMONADE_BT_MAX_PACKET];
    size_t used, expected;
    unsigned allowed, ibs;
    enum lemonade_bt_parse_error error;
};

typedef int (*lemonade_bt_packet_fn)(void *, const uint8_t *, size_t);
typedef int (*lemonade_bt_ibs_fn)(void *, uint8_t);

static inline void lemonade_bt_parser_init(struct lemonade_bt_parser *p,
                                           unsigned allowed, unsigned ibs)
{
    p->used = p->expected = 0;
    p->allowed = allowed;
    p->ibs = ibs;
    p->error = LEMONADE_BT_PARSE_OK;
}

static inline size_t lemonade_bt_header_size(uint8_t type)
{
    switch (type) {
    case 1: case 3: return 4; /* type + command/SCO header */
    case 2: case 5: return 5; /* type + ACL/ISO header */
    case 4: return 3;         /* type + event header */
    default: return 0;
    }
}

static inline size_t lemonade_bt_payload_size(const uint8_t *packet)
{
    switch (packet[0]) {
    case 1: case 3: return packet[3];
    case 2: return (size_t)packet[3] | ((size_t)packet[4] << 8);
    case 4: return packet[2];
    case 5: return ((size_t)packet[3] | ((size_t)packet[4] << 8)) & 0x3fffu;
    default: return 0;
    }
}

/* A framing or delivery error latches until an explicit session reset.
 * Scanning for a new type byte would mistake payload/IBS bytes for packets.
 * Callbacks consume/copy the complete packet synchronously, returning zero
 * on success. Delivery failure must stop the controller session, not silently
 * discard an event that may carry command or ACL credits. */
static inline enum lemonade_bt_parse_error lemonade_bt_parse(
    struct lemonade_bt_parser *p, const uint8_t *bytes, size_t size,
    lemonade_bt_packet_fn packet, lemonade_bt_ibs_fn ibs, void *context)
{
    if (p->error) return p->error;
    for (size_t i = 0; i < size; i++) {
        uint8_t byte = bytes[i];
        if (!p->used) {
            if (p->ibs && (byte == LEMONADE_BT_IBS_WAKE || byte == LEMONADE_BT_IBS_ACK ||
                          byte == LEMONADE_BT_IBS_SLEEP)) {
                if (!ibs || ibs(context, byte))
                    return p->error = LEMONADE_BT_PARSE_DELIVERY;
                continue;
            }
            p->expected = lemonade_bt_header_size(byte);
            if (!p->expected || !(p->allowed & LEMONADE_BT_TYPE(byte)))
                return p->error = LEMONADE_BT_PARSE_TYPE;
        }
        if (p->used >= sizeof(p->packet)) return p->error = LEMONADE_BT_PARSE_LENGTH;
        p->packet[p->used++] = byte;
        if (p->used == lemonade_bt_header_size(p->packet[0])) {
            size_t payload = lemonade_bt_payload_size(p->packet);
            if (payload > sizeof(p->packet) - p->used)
                return p->error = LEMONADE_BT_PARSE_LENGTH;
            p->expected = p->used + payload;
        }
        if (p->used == p->expected) {
            if (!packet || packet(context, p->packet, p->used))
                return p->error = LEMONADE_BT_PARSE_DELIVERY;
            p->used = p->expected = 0;
        }
    }
    return LEMONADE_BT_PARSE_OK;
}
struct lemonade_bt_version {
    uint32_t product, soc;
    uint16_t rom, patch;
};
static inline uint16_t lemonade_bt_le16(const uint8_t *p) {
    return p[0] | ((uint16_t)p[1] << 8);
}
static inline uint32_t lemonade_bt_le32(const uint8_t *p) {
    return lemonade_bt_le16(p) | ((uint32_t)lemonade_bt_le16(p + 2) << 16);
}
/* Qualcomm's reserved diagnostic ACL channel, as defined by hci_qca.c.
 * Recognize only a complete framed packet. It carries controller logs, not
 * connection data or command credits; its private payload must not be logged.
 * The caller must separately verify the physical Qualcomm controller identity. */
static inline int lemonade_bt_qca_diagnostic(const uint8_t *packet, size_t size) {
    return packet && size >= 5 && packet[0] == 2 &&
        lemonade_bt_le16(packet + 1) == 0x2edc &&
        lemonade_bt_le16(packet + 3) == size - 5;
}
/* iOS 16.5's legacy OI initializer requests LE host support as {1,1}.
 * Core 5.2, Vol 4 Part E 7.3.79/6.35 requires the obsolete Simultaneous_LE_Host
 * parameter to be zero. Only normalize that legacy boolean; the enable bit,
 * all other commands, and every real controller response remain untouched.
 * Call only for the physically identified WCN6851 board, never generically. */
static inline int lemonade_bt_legacy_le_host(const uint8_t *packet, size_t size,
                                            uint8_t corrected[6]) {
    if (size != 6 || packet[0] != 1 || lemonade_bt_le16(packet + 1) != 0x0c6d ||
        packet[3] != 2 || packet[4] > 1 || packet[5] != 1) return 0;
    for (size_t i = 0; i < size; i++) corrected[i] = packet[i];
    corrected[5] = 0;
    return 1;
}
/* Qualcomm version responses: legacy vendor event, or unified Command Complete.
 * The unified return parameters are response/subcommand/length, then the 12-byte version.
 * Failure never changes the caller's last validated version. */
static inline int lemonade_bt_read_version(const uint8_t *p, size_t size,
                                           struct lemonade_bt_version *result) {
    const uint8_t *v = NULL;
    if (!p || !result || size < 3 || p[0] != 4 || size != (size_t)p[2] + 3) return 0;
    if (size == 17 && p[1] == 0xff && p[3] == 0 && p[4] == 2) v = p + 5;
    if (size == 21 && p[1] == 0x0e && lemonade_bt_le16(p + 4) == 0xfc00 &&
        p[6] == 0 && p[7] == 0x19 && p[8] == 12) v = p + 9;
    if (!v || !lemonade_bt_le16(v + 6) || !lemonade_bt_le32(v + 8)) return 0;
    result->product = lemonade_bt_le32(v);
    result->patch = lemonade_bt_le16(v + 4);
    result->rom = lemonade_bt_le16(v + 6);
    result->soc = lemonade_bt_le32(v + 8);
    return 1;
}
#endif
