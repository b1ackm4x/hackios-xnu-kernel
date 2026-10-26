#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../xnu/files/bsd/dev/lemonade_bt_protocol.h"

struct received { unsigned packets, controls, fail; size_t bytes; uint8_t data[8192]; };
static int packet(void *context, const uint8_t *data, size_t size)
{
    struct received *r = context;
    if (r->fail) return 1;
    assert(r->bytes + size <= sizeof(r->data));
    memcpy(r->data + r->bytes, data, size);
    r->bytes += size;
    r->packets++;
    return 0;
}
static int control(void *context, uint8_t value)
{
    struct received *r = context;
    assert(value == 0xfc || value == 0xfd || value == 0xfe);
    if (r->fail) return 1;
    r->controls++;
    return 0;
}

int main(void)
{
    uint8_t diagnostic[] = {2,0xdc,0x2e,2,0,0xfd,0xfe};
    assert(lemonade_bt_qca_diagnostic(diagnostic,sizeof(diagnostic)));
    for (size_t size=0;size<sizeof(diagnostic);size++)
        assert(!lemonade_bt_qca_diagnostic(diagnostic,size));
    assert(!lemonade_bt_qca_diagnostic(NULL,sizeof(diagnostic)));
    diagnostic[0]=4;assert(!lemonade_bt_qca_diagnostic(diagnostic,sizeof(diagnostic)));
    diagnostic[0]=2;diagnostic[1]^=1;assert(!lemonade_bt_qca_diagnostic(diagnostic,sizeof(diagnostic)));
    diagnostic[1]^=1;diagnostic[3]=3;assert(!lemonade_bt_qca_diagnostic(diagnostic,sizeof(diagnostic)));
    /* Unified CC includes vendor response, subtype and version length in that order. */
    const uint8_t unified[] = {4,0x0e,18,1,0,0xfc,0,0x19,12,
        0x13,0,0,0,0x17,0x10,0,1,0x10,1,0x0c,0x40};
    const uint8_t legacy[] = {4,0xff,14,0,2,
        0x13,0,0,0,0x17,0x10,0,1,0x10,1,0x0c,0x40};
    struct lemonade_bt_version v = {0};
    assert(lemonade_bt_read_version(unified, sizeof(unified), &v));
    assert(v.product == 0x13 && v.soc == 0x400c0110 && v.rom == 0x100 && v.patch == 0x1017);
    assert(lemonade_bt_read_version(legacy, sizeof(legacy), &v));
    for (size_t n = 0; n < sizeof(unified); n++) assert(!lemonade_bt_read_version(unified, n, &v));
    const unsigned invalid_fields[] = {0,1,2,4,5,6,7,8};
    for (unsigned i = 0; i < sizeof(invalid_fields)/sizeof(invalid_fields[0]); i++) {
        uint8_t invalid[sizeof(unified)]; memcpy(invalid, unified, sizeof(invalid));
        invalid[invalid_fields[i]] ^= 1;
        assert(!lemonade_bt_read_version(invalid, sizeof(invalid), &v));
        assert(v.product == 0x13 && v.soc == 0x400c0110);
    }
    uint8_t missing[sizeof(unified)]; memcpy(missing, unified, sizeof(missing));
    memset(missing + 15, 0, 6); /* absent ROM and SoC cannot identify a controller */
    assert(!lemonade_bt_read_version(missing, sizeof(missing), &v));
    struct lemonade_bt_parser p;
    /* A Command Complete and an ACL packet containing ALL IBS values. */
    const uint8_t event[] = {4, 0x0e, 4, 1, 3, 0x0c, 0};
    const uint8_t acl[] = {2, 1, 0x20, 4, 0, 0xfd, 0xfc, 0xfe, 4};
    uint8_t stream[1 + sizeof(event) + sizeof(acl) + 1];
    stream[0] = 0xfd;
    memcpy(stream + 1, event, sizeof(event));
    memcpy(stream + 1 + sizeof(event), acl, sizeof(acl));
    stream[sizeof(stream) - 1] = 0xfe;
    for (size_t split = 0; split <= sizeof(stream); split++) {
        struct received r = {0};
        lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1);
        assert(!lemonade_bt_parse(&p, stream, split, packet, control, &r));
        assert(!lemonade_bt_parse(&p, stream + split, sizeof(stream) - split, packet, control, &r));
        assert(r.packets == 2 && r.controls == 2 && r.bytes == sizeof(event) + sizeof(acl));
        assert(!memcmp(r.data, event, sizeof(event)));
        assert(!memcmp(r.data + sizeof(event), acl, sizeof(acl)));
        assert(p.used == 0);
    }
    struct received r = {0};
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1);
    for (size_t i = 0; i < sizeof(stream); i++)
        assert(!lemonade_bt_parse(&p, stream + i, 1, packet, control, &r));
    assert(r.packets == 2 && r.controls == 2);

    /* A short header cannot deliver, and an oversized payload fails at the
     * length field instead of filling or scanning beyond the buffer. */
    const uint8_t huge[] = {2, 1, 0x20, 0xff, 0xff};
    memset(&r, 0, sizeof(r));
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1);
    assert(!lemonade_bt_parse(&p, huge, 4, packet, control, &r));
    assert(!r.packets);
    assert(lemonade_bt_parse(&p, huge + 4, 1, packet, control, &r) == LEMONADE_BT_PARSE_LENGTH);
    assert(lemonade_bt_parse(&p, event, sizeof(event), packet, control, &r) == LEMONADE_BT_PARSE_LENGTH);
    assert(!r.packets);

    /* Host and controller direction are enforced. */
    const uint8_t reset[] = {1, 3, 0x0c, 0};
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_HOST, 0);
    assert(!lemonade_bt_parse(&p, reset, sizeof(reset), packet, control, &r));
    assert(r.packets == 1);
    assert(lemonade_bt_parse(&p, event, sizeof(event), packet, control, &r) == LEMONADE_BT_PARSE_TYPE);
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1);
    assert(lemonade_bt_parse(&p, reset, sizeof(reset), packet, control, &r) == LEMONADE_BT_PARSE_TYPE);
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 0);
    assert(lemonade_bt_parse(&p, stream, 1, packet, control, &r) == LEMONADE_BT_PARSE_TYPE);

    /* Delivery failure latches instead of silently losing a credit event. */
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1); r.fail = 1;
    assert(lemonade_bt_parse(&p, event, sizeof(event), packet, control, &r) == LEMONADE_BT_PARSE_DELIVERY);
    r.fail = 0;
    assert(lemonade_bt_parse(&p, event, sizeof(event), packet, control, &r) == LEMONADE_BT_PARSE_DELIVERY);

    /* ISO length is 14 bits; flag bits are not part of the length. */
    const uint8_t iso[] = {5, 1, 0, 1, 0xc0, 0xfd};
    memset(&r, 0, sizeof(r)); lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1);
    assert(!lemonade_bt_parse(&p, iso, sizeof(iso), packet, control, &r));
    assert(r.packets == 1 && r.controls == 0 && r.bytes == sizeof(iso));
    /* Unknown bytes do not cause opportunistic resynchronization. */
    const uint8_t bad[] = {0, 4, 0x0e, 0};
    lemonade_bt_parser_init(&p, LEMONADE_BT_FROM_CONTROLLER, 1);
    assert(lemonade_bt_parse(&p, bad, sizeof(bad), packet, control, &r) == LEMONADE_BT_PARSE_TYPE);
    puts("H4 fragmentation, direction, IBS boundaries, bounds and failure-state tests passed");
    return 0;
}
