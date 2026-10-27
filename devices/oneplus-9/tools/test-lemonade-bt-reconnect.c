/* Run the production reconnect transaction against a scripted UART peer. */
#define LEMONADE_BLUETOOTH_HOST_TEST
#include "../../../xnu/files/bsd/dev/lemonade_bluetooth.c"
#include <assert.h>

static uint32_t registers[0x1000 / 4];
static uint8_t incoming[256], outgoing[32];
static size_t rx_head, rx_used, tx_size, tx_used;
static unsigned commands, elapsed, no_reset_reply, wrong_patch, wrong_address;
static const uint8_t test_address[] = {1,2,3,4,5,6};
static void queue(const uint8_t *data, size_t size) {
    assert(rx_head + rx_used + size <= sizeof(incoming));
    memcpy(incoming + rx_head + rx_used, data, size); rx_used += size;
}
static void peer_reply(void) {
    unsigned opcode = lemonade_bt_le16(outgoing + 1); commands++;
    assert(outgoing[0] == 1 && outgoing[3] + 4 == tx_size);
    if (opcode == 0xc03) {
        const uint8_t p[] = {4,0x0e,4,1,3,0x0c,0};
        if (!no_reset_reply) queue(p, sizeof(p));
    } else if (opcode == 0xfc00) {
        uint8_t p[] = {4,0x0e,18,1,0,0xfc,0,0x19,12,0x13,0,0,0,0x3e,0x2f,0,1,0x10,1,0x0c,0x40};
        assert(outgoing[4] == 0x19);
        if (wrong_patch) p[13] ^= 1;
        queue(p, sizeof(p));
    } else if (opcode == 0x1009) {
        uint8_t p[] = {4,0x0e,10,1,9,0x10,0,1,2,3,4,5,6};
        if (wrong_address) p[7] ^= 1;
        queue(p, sizeof(p));
    } else assert(0);
}
static uint32_t bt_test_read(uintptr_t address) {
    assert(address < sizeof(registers));
    if (address == 0x804) return (uint32_t)(rx_used < 64 ? rx_used : 64);
    if (address == 0x780) { assert(rx_used); rx_used--; return incoming[rx_head++]; }
    return registers[address / 4];
}
static void bt_test_write(uintptr_t address, uint32_t value) {
    assert(address < sizeof(registers));
    if (address == 0x270) { tx_size = value; tx_used = 0; assert(tx_size <= sizeof(outgoing)); }
    if (address == 0x700) {
        assert(tx_used < tx_size); outgoing[tx_used++] = (uint8_t)value;
        if (tx_used == tx_size) { peer_reply(); registers[0x610 / 4] |= 1; }
    }
    if (address == 0x648 || address == 0x618) registers[(address - 8) / 4] &= ~value;
    else registers[address / 4] = value;
}
void delay_for_interval(unsigned ticks, unsigned scale) { assert(scale == NSEC_PER_USEC); elapsed += ticks; }
int PE_parse_boot_argn(const char *n, void *p, unsigned s) { (void)n;(void)p;(void)s;return 0; }
int SecureDTLookupEntry(void *p, const char *n, DTEntry *d) { (void)p;(void)n;(void)d;return -1; }
int SecureDTGetProperty(DTEntry d, const char *n, const void **p, unsigned *s) { (void)d;(void)n;(void)p;(void)s;return -1; }
bool lemonade_radio_supply_on(const char *n, unsigned mv) { (void)n;(void)mv;return false; }
vm_offset_t ml_io_map_unmappable(vm_offset_t p, vm_size_t s, unsigned f) { (void)p;(void)s;(void)f;return 0; }
void ml_io_unmap(vm_offset_t p, vm_size_t s) { (void)p;(void)s; }
void redwood_tl(const char *f, ...) { (void)f; }
bool LemonadeBluetoothIdentity(const uint8_t *a, bool ready) { (void)a;(void)ready;return false; }
const uint8_t *redwood_image_file(const char *d, const char *n, uint64_t *s, int *i) { (void)d;(void)n;(void)s;(void)i;return NULL; }
static struct bt_identification reset(struct lemonade_bt_parser *parser) {
    memset(registers, 0, sizeof(registers)); registers[0xe24 / 4] = 16 << 16;
    rx_head = rx_used = tx_size = tx_used = 0;
    commands = elapsed = no_reset_reply = wrong_patch = wrong_address = 0;
    bt_stop_requested = false;
    lemonade_bt_parser_init(parser, LEMONADE_BT_FROM_CONTROLLER, 1);
    return (struct bt_identification){.firmware=true, .native=true, .address=test_address,
        .actual={.product=0x13,.rom=0x100,.patch=0x2f3e,.soc=0x400c0110}};
}
int main(void) {
    struct lemonade_bt_parser parser;
    struct bt_identification state = reset(&parser);
    assert(revalidate_native_controller(&state, &parser));
    assert(commands == 3 && !rx_used && state.native && !state.recovering);
    state = reset(&parser); no_reset_reply = 1;
    assert(!revalidate_native_controller(&state, &parser)); assert(commands == 1 && elapsed <= 2100000);
    state = reset(&parser); wrong_patch = 1;
    assert(!revalidate_native_controller(&state, &parser)); assert(commands == 2);
    state = reset(&parser); wrong_address = 1;
    assert(!revalidate_native_controller(&state, &parser)); assert(commands == 3);
    state = reset(&parser); state.firmware = false;
    assert(!revalidate_native_controller(&state, &parser)); assert(!commands);
    state = reset(&parser); parser.error = LEMONADE_BT_PARSE_TYPE;
    assert(!revalidate_native_controller(&state, &parser)); assert(!commands);
    state = reset(&parser);
    const uint8_t old_acl[] = {2,1,0,1,0,0x55};
    /* The last host can close in the middle of receiving an ACL packet. Its
     * remainder must be drained as that packet, never searched for H4 bytes. */
    assert(!lemonade_bt_parse(&parser, old_acl, 3, identification_event, identification_ibs, &state));
    queue(old_acl + 3, sizeof(old_acl) - 3);
    assert(revalidate_native_controller(&state, &parser));
    assert(commands == 3 && !parser.used && !rx_used);
    state = reset(&parser); state.native = false;
    assert(identification_event(&state, old_acl, sizeof(old_acl)) == -1);
    uint8_t diagnostic[] = {2,0xdc,0x2e,2,0,0xfd,0xfe};
    state.actual.product=0x13;state.actual.rom=0x100;state.actual.soc=0x400c0110;
    assert(!identification_event(&state,diagnostic,sizeof(diagnostic)));
    assert(state.diagnostics==1);
    state.native=true;
    assert(!identification_event(&state,diagnostic,sizeof(diagnostic)));
    assert(state.diagnostics==2);
    state.native=false;state.actual.soc=0;
    assert(identification_event(&state,diagnostic,sizeof(diagnostic))==-1);
    state.actual.soc=0x400c0110;diagnostic[1]^=1;
    assert(identification_event(&state,diagnostic,sizeof(diagnostic))==-1);
    puts("Production reconnect reset/version/address transaction: success, timeout, mismatch, parser fault and partial old ACL tests passed");
}
