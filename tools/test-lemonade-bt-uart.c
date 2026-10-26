/* Executes the production stop sequence with only MMIO/time/kernel substituted. */
#define LEMONADE_BLUETOOTH_HOST_TEST
#include "../xnu/files/bsd/dev/lemonade_bluetooth.c"
#include <assert.h>
#include <stdarg.h>
static uint32_t registers[0x1000 / 4];
static unsigned delay_us, writes, cancel_calls, abort_calls, blocked_restore;
static unsigned fail_cancel, fail_abort, cancel_without_idle;
static unsigned tx_test, tx_blocked, tx_sent, tx_max_used;
static uint32_t bt_test_read(uintptr_t address) {
    assert(address < sizeof(registers));
    if (tx_test && address == 0x800 && !tx_blocked && registers[0x800 / 4]) registers[0x800 / 4]--;
    return registers[address / 4];
}
static void bt_test_write(uintptr_t address, uint32_t value) {
    assert(address < sizeof(registers)); writes++;
    if (tx_test && address == 0x700) {
        unsigned depth = (registers[0xe24 / 4] >> 16) & 0x3f;
        assert(registers[0x800 / 4] < depth);
        registers[0x800 / 4]++; tx_sent++;
        if (registers[0x800 / 4] > tx_max_used) tx_max_used = registers[0x800 / 4];
        if (tx_sent == registers[0x270 / 4]) registers[0x610 / 4] |= 1;
    }
    if (address == 0x648 || address == 0x618) {
        registers[(address - 8) / 4] &= ~value;
        return;
    }
    if (address == 0x634 || address == 0x604) {
        bool receive = address == 0x634;
        if (receive) {
            assert(registers[0x2ac / 4] == 0x80000002);
            assert(delay_us >= 4010);
        }
        unsigned irq = receive ? 0x640 : 0x610;
        unsigned active = receive ? 0x1000 : 1;
        if (value == 4) {
            cancel_calls++;
            if (!fail_cancel) {
                registers[irq / 4] |= 0x10;
                if (!cancel_without_idle) registers[0x40 / 4] &= ~active;
            }
        } else if (value == 2) {
            abort_calls++;
            if (!fail_abort) {
                registers[irq / 4] |= 0x20;
                registers[0x40 / 4] &= ~active;
            }
        } else assert(0);
    }
    if ((address == 0x48 || address == 0x4c) && (registers[0x40 / 4] & 0x1001)) blocked_restore++;
    registers[address / 4] = value;
}
void delay_for_interval(unsigned ticks, unsigned scale) { assert(scale == NSEC_PER_USEC); delay_us += ticks; }
int PE_parse_boot_argn(const char *n, void *p, unsigned s) { (void)n;(void)p;(void)s;return 0; }
int SecureDTLookupEntry(void *p, const char *n, DTEntry *d) { (void)p;(void)n;(void)d;return -1; }
int SecureDTGetProperty(DTEntry d, const char *n, const void **p, unsigned *s) { (void)d;(void)n;(void)p;(void)s;return -1; }
bool lemonade_radio_supply_on(const char *n, unsigned mv) { (void)n;(void)mv;return false; }
vm_offset_t ml_io_map_unmappable(vm_offset_t p, vm_size_t s, unsigned f) { (void)p;(void)s;(void)f;return 0; }
void ml_io_unmap(vm_offset_t p, vm_size_t s) { (void)p;(void)s; }
void redwood_tl(const char *f, ...) { (void)f; }
bool LemonadeBluetoothIdentity(const uint8_t *a, bool ready) { (void)a;(void)ready;return false; }
const uint8_t *redwood_image_file(const char *d, const char *n, uint64_t *s, int *i) { (void)d;(void)n;(void)s;(void)i;return NULL; }
static void reset(void) {
    memset(registers, 0, sizeof(registers)); delay_us = writes = cancel_calls = abort_calls = blocked_restore = 0;
    fail_cancel = fail_abort = cancel_without_idle = 0;
    tx_test = tx_blocked = tx_sent = tx_max_used = 0;
}
int main(void) {
    reset(); assert(stop_sequencer(0, true)); assert(!writes);
    reset(); registers[0x40/4] = 0x1000;
    assert(stop_sequencer(0, true)); assert(cancel_calls == 1 && abort_calls == 0);
    assert(registers[0x40/4] == 0 && !(registers[0x640/4] & 0x30));
    reset(); registers[0x40/4] = 0x1000; fail_cancel = 1;
    assert(stop_sequencer(0, true)); assert(cancel_calls == 1 && abort_calls == 1);
    reset(); registers[0x40/4] = 0x1000; cancel_without_idle = 1;
    assert(stop_sequencer(0, true)); assert(abort_calls == 1);
    reset(); registers[0x40/4] = 0x1000; fail_cancel = fail_abort = 1;
    assert(!stop_sequencer(0, true)); assert(registers[0x40/4] == 0x1000);
    assert(delay_us <= 205000 && !blocked_restore);
    reset(); registers[0x40/4] = 1;
    assert(stop_sequencer(0, false)); assert(!delay_us && cancel_calls == 1);
    assert(!blocked_restore);
    uint8_t packet[249] = {1};
    reset(); tx_test = 1; registers[0xe24 / 4] = 16 << 16;
    assert(send_bytes(0, packet, sizeof(packet))); assert(tx_sent == sizeof(packet) && tx_max_used == 16);
    reset(); tx_test = tx_blocked = 1; registers[0xe24 / 4] = 16 << 16; registers[0x800 / 4] = 16;
    assert(!send_bytes(0, packet, sizeof(packet))); assert(tx_sent == 0 && delay_us <= 2000000);
    reset(); assert(!send_bytes(0, packet, sizeof(packet))); assert(writes == 0);
    puts("Production GENI bounded FIFO fill and stop: flow fence, cancel/abort completion, active-status and bounded timeout tests passed");
}
