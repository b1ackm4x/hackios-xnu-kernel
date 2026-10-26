/* OnePlus 9 / SM8350 SE18 Bluetooth bring-up.
 * lemonade_bt=1 only observes existing state. No clock, rail, FIFO or HCI writes.
 * Register sources: OnePlusOSS android_kernel_oneplus_sm8350 at
 * 13ca08fdf0979fdd61d5e8991661874bb2d19150, gcc-lahaina.c / msm-geni-se.h.
 */
#ifdef LEMONADE_BLUETOOTH_HOST_TEST
#include "lemonade-bt-test-support.h"
#else
#include <libkern/libkern.h>
#include <kern/clock.h>
#include <kern/thread.h>
extern kern_return_t thread_terminate(thread_t);
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <vm/memory_types.h>
#include <sys/lemonade_radio.h>
#endif
#include "lemonade_bt_protocol.h"
#include "lemonade_bt_firmware.h"
extern const uint8_t *redwood_image_file(const char *, const char *, uint64_t *, int *);
extern bool LemonadeBluetoothIdentity(const uint8_t *, bool);
extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned);
extern void ml_io_unmap(vm_offset_t, vm_size_t);
extern void redwood_tl(const char *, ...) __printflike(1, 2);
static uint32_t read_register(vm_offset_t base, unsigned offset) {
#ifdef LEMONADE_BLUETOOTH_HOST_TEST
    return bt_test_read(base + offset);
#else
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(base + offset) : "memory");
    return value;
#endif
}
static void write_register(vm_offset_t base, unsigned offset, uint32_t value) {
#ifdef LEMONADE_BLUETOOTH_HOST_TEST
    bt_test_write(base + offset, value);
#else
    __asm__ volatile("str %w0, [%1]" : : "r"(value), "r"(base + offset) : "memory");
    __asm__ volatile("dsb sy" ::: "memory");
#endif
}
/* Cold-ROM identification only: finite FIFO polling, no DMA or firmware writes. */
static vm_offset_t emergency_pins;
static bool emergency_radio;
static bool bt_stop_requested, bt_worker_active;
static int native_receive(void *, const uint8_t *, size_t);
void lemonade_bluetooth_quiesce(void) {
    __atomic_store_n(&bt_stop_requested, true, __ATOMIC_RELEASE);
    if (emergency_pins && emergency_radio) {
        write_register(emergency_pins, 65 * 0x1000 + 4,
            read_register(emergency_pins, 65 * 0x1000 + 4) & ~2u);
    }
}
static void pause_us(unsigned us) { delay_for_interval(us, NSEC_PER_USEC); }
static bool wait_bits(vm_offset_t base, unsigned offset, uint32_t mask, uint32_t wanted, unsigned attempts) {
    for (unsigned i = 0; i < attempts; i++) {
        if ((read_register(base, offset) & mask) == wanted) return true;
        pause_us(10);
    }
    return false;
}
static bool rcg_commit(vm_offset_t gcc) {
    write_register(gcc, 0x1e4d0, read_register(gcc, 0x1e4d0) | 1);
    return wait_bits(gcc, 0x1e4d0, 1, 0, 1000);
}
static bool stop_sequencer(vm_offset_t se, bool receive) {
    uint32_t active = receive ? 0x1000 : 1;
    unsigned control = receive ? 0x634 : 0x604;
    unsigned irq = receive ? 0x640 : 0x610;
    unsigned clear = receive ? 0x648 : 0x618;
    if (!(read_register(se, 0x40) & active)) return true;
    if (receive) {
        /* Vendor GENI shutdown: close RFR, wait twice the stale period, then
         * cancel. An idle status alone is not the command-completion fence. */
        write_register(se, 0x2ac, 1u << 31);
        pause_us(10);
        write_register(se, 0x2ac, (1u << 31) | 2);
        pause_us(4000); /* > 2 * 160 bits / 115200 baud */
    }
    write_register(se, clear, 0x30);
    write_register(se, control, 4);
    bool done = wait_bits(se, irq, 0x10, 0x10, 10000);
    if (!done || (read_register(se, 0x40) & active)) {
        write_register(se, clear, 0x30);
        write_register(se, control, 2);
        done = wait_bits(se, irq, 0x20, 0x20, 10000);
    }
    bool idle = done && wait_bits(se, 0x40, active, 0, 10000);
    redwood_tl("bt-lemonade: %s command completion=%u idle=%u\n", receive ? "RX" : "TX", done, idle);
    if (idle) write_register(se, clear, 0x30);
    return idle;
}
static bool send_bytes(vm_offset_t se, const uint8_t *bytes, unsigned count) {
    unsigned depth = (read_register(se, 0xe24) >> 16) & 0x3f;
    if (__atomic_load_n(&bt_stop_requested, __ATOMIC_ACQUIRE) || !count || count > LEMONADE_BT_MAX_PACKET || !depth || depth > 64 ||
        (read_register(se, 0x40) & 1)) return false;
    write_register(se, 0x618, 0xffffffff);
    write_register(se, 0x270, count);
    write_register(se, 0x600, 1u << 27);
    unsigned sent = 0;
    for (unsigned polls = 0; polls < 200000 && sent < count; polls++) {
        unsigned used = read_register(se, 0x800) & 0xfffffff;
        if (used > depth) break;
        unsigned available = depth - used;
        while (available-- && sent < count) write_register(se, 0x700, bytes[sent++]);
        if (sent < count) pause_us(10);
    }
    bool ok = sent == count && wait_bits(se, 0x610, 1, 1, 100000);
    if (!ok || count <= 8)
        redwood_tl("bt-lemonade: TX count=%u complete=%u status=%#x irq=%#x IOS=%#x\n",
            count, ok, read_register(se, 0x40), read_register(se, 0x610), read_register(se, 0x908));
    write_register(se, 0x618, 0xffffffff);
    return ok;
}
struct bt_identification {
    vm_offset_t se;
    bool version, board, address_matches, firmware, native, recovering;
    unsigned events, bytes, completions, board_id, reports, diagnostics;
    unsigned last_opcode, last_status, last_subtype;
    struct lemonade_bt_version actual;
    const uint8_t *address;
};
static int identification_event(void *context, const uint8_t *p, size_t size) {
    struct bt_identification *state = context;
    if (state->actual.product == 0x13 && state->actual.rom == 0x100 &&
        state->actual.soc == 0x400c0110 && lemonade_bt_qca_diagnostic(p, size)) {
        if (!state->diagnostics++)
            redwood_tl("bt-lemonade: controller diagnostic ACL separated; payload omitted\n");
        return 0;
    }
    if (state->native) return native_receive(context, p, size);
    struct lemonade_bt_version version;
    state->events++;
    /* Once a host has closed, old ACL/SCO/ISO traffic belongs to that dead
     * connection. Drain complete packets while the owner resets the chip. */
    if (p[0] != 4) {
        if (!state->recovering)
            redwood_tl("bt-lemonade: unexpected initialization packet type=%u bytes=%u header=%#x; payload omitted\n",
                p[0], (unsigned)size, size >= 3 ? lemonade_bt_le16(p + 1) : 0);
        return state->recovering ? 0 : -1;
    }
    if (size >= 7 && p[1] == 0x0e) {
        state->completions++;
        state->last_opcode = lemonade_bt_le16(p + 4);
        state->last_status = p[6];
        state->last_subtype = size > 7 ? p[7] : 256;
        if (state->last_opcode != 0xfc00 || state->last_subtype != 0x1e)
            redwood_tl("bt-lemonade: command complete opcode=%#x status=%u bytes=%u\n",
                state->last_opcode, state->last_status, (unsigned)size);
    }
    if (lemonade_bt_read_version(p, size, &version)) {
        state->version = true; state->actual = version;
        redwood_tl("bt-lemonade: REAL controller product=%#x ROM=%#x patch=%#x SoC=%#x\n",
            version.product, version.rom, version.patch, version.soc);
    }
    if (size == 11 && p[1] == 0x0e && lemonade_bt_le16(p + 4) == 0xfc00 &&
        p[6] == 0 && p[7] == 0x23 && p[8] == 2) {
        state->board = true; state->board_id = ((unsigned)p[9] << 8) | p[10];
        redwood_tl("bt-lemonade: REAL controller board=%#x\n", state->board_id);
    }
    if (size == 13 && p[1] == 0x0e && lemonade_bt_le16(p + 4) == 0x1009 && !p[6] && state->address) {
        state->address_matches = !memcmp(p + 7, state->address, 6);
        redwood_tl("bt-lemonade: controller address matches this phone=%u\n", state->address_matches);
    }
    if (size == 15 && p[1] == 0x0e && lemonade_bt_le16(p + 4) == 0x1001 && !p[6])
        redwood_tl("bt-lemonade: HCI version=%u manufacturer=%u subversion=%#x\n",
            p[7], lemonade_bt_le16(p + 11), lemonade_bt_le16(p + 13));
    if (size >= 5 && p[1] == 0x3e && p[3] == 2) state->reports += p[4];
    return 0;
}
static int identification_ibs(void *context, uint8_t byte) {
    struct bt_identification *state = context;
    if (byte == LEMONADE_BT_IBS_WAKE) {
        const uint8_t ack = LEMONADE_BT_IBS_ACK;
        return send_bytes(state->se, &ack, 1) ? 0 : -1;
    }
    return 0;
}
static bool poll_rx(struct bt_identification *state, struct lemonade_bt_parser *parser) {
    unsigned words = read_register(state->se, 0x804) & 0x1ffffff;
    if (words > 64) return false;
    for (unsigned n = 0; n < words; n++) {
        uint8_t byte = read_register(state->se, 0x780);
        state->bytes++;
        if (lemonade_bt_parse(parser, &byte, 1, identification_event, identification_ibs, state)) return false;
    }
    write_register(state->se, 0x648, read_register(state->se, 0x640));
    return !parser->error;
}
static bool wait_complete(struct bt_identification *state, struct lemonade_bt_parser *parser,
                          unsigned previous, unsigned opcode, int subtype) {
    for (unsigned i = 0; i < 20000; i++) {
        if (__atomic_load_n(&bt_stop_requested, __ATOMIC_ACQUIRE)) return false;
        if (!poll_rx(state, parser)) return false;
        if (state->completions != previous && state->last_opcode == opcode &&
            (subtype < 0 || state->last_subtype == (unsigned)subtype)) return state->last_status == 0;
        pause_us(100);
    }
    redwood_tl("bt-lemonade: command timeout opcode=%#x subtype=%d\n", opcode, subtype);
    return false;
}
static bool command_complete(struct bt_identification *state, struct lemonade_bt_parser *parser,
                             const uint8_t *command, size_t size, int subtype) {
    unsigned previous = state->completions;
    return size >= 4 && send_bytes(state->se, command, (unsigned)size) &&
        wait_complete(state, parser, previous, lemonade_bt_le16(command + 1), subtype);
}
static bool download_tlv(struct bt_identification *state, struct lemonade_bt_parser *parser,
                         const uint8_t *data, size_t size, unsigned mode) {
    uint8_t command[249] = {1,0,0xfc}; unsigned segments = 0;
    for (size_t offset = 0; offset < size;) {
        unsigned count = size - offset > 243 ? 243 : (unsigned)(size - offset);
        command[3] = count + 2; command[4] = 0x1e; command[5] = count;
        memcpy(command + 6, data + offset, count);
        unsigned previous = state->completions;
        if (!send_bytes(state->se, command, count + 6)) return false;
        offset += count; segments++;
        if ((!mode || offset == size) && !wait_complete(state, parser, previous, 0xfc00, 0x1e)) {
            redwood_tl("bt-lemonade: firmware segment failed at=%u\n", segments); return false;
        }
        if (!poll_rx(state, parser)) return false;
        if (!(segments % 256)) redwood_tl("bt-lemonade: firmware progress bytes=%u\n", (unsigned)offset);
    }
    redwood_tl("bt-lemonade: firmware bytes=%u segments=%u final controller ACK=1\n", (unsigned)size, segments);
    return true;
}
static bool load_controller_firmware(struct bt_identification *state, struct lemonade_bt_parser *parser) {
    uint64_t length = 0; struct lemonade_bt_patch patch;
    const uint8_t *data = redwood_image_file("fw", "hpbtfw10.tlv", &length, NULL);
    if (!state->version || state->actual.product != 0x13 || state->actual.soc != 0x400c0110 ||
        state->actual.rom != 0x100 || !lemonade_bt_patch_info(data, (size_t)length, &patch) ||
        patch.product != state->actual.product || patch.rom != state->actual.rom) {
        redwood_tl("bt-lemonade: firmware missing or mismatched to actual controller\n"); return false;
    }
    redwood_tl("bt-lemonade: loading matched RAM patch version=%#x bytes=%u mode=%u\n", patch.patch, (unsigned)length, patch.mode);
    if (!download_tlv(state, parser, data, (size_t)length, patch.mode)) return false;
    pause_us(10000);
    const uint8_t board[] = {1,0,0xfc,1,0x23}; state->board = false;
    if (!command_complete(state, parser, board, sizeof(board), 0x23) || !state->board) return false;
    char name[32];
    snprintf(name, sizeof(name), state->board_id < 256 ? "hpnv10.b%02x" : "hpnv10.b%04x", state->board_id);
    data = redwood_image_file("fw", name, &length, NULL);
    if (!data) {
        snprintf(name, sizeof(name), state->board_id < 256 ? "hpnv10.b%02X" : "hpnv10.b%04X", state->board_id);
        data = redwood_image_file("fw", name, &length, NULL);
    }
    if (!data) { strlcpy(name, "hpnv10.bin", sizeof(name)); data = redwood_image_file("fw", name, &length, NULL); }
    if (!lemonade_bt_nvm_validate(data, (size_t)length)) {
        redwood_tl("bt-lemonade: matching stock NVM invalid or missing\n"); return false;
    }
    static uint8_t nvm[65536]; memcpy(nvm, data, (size_t)length);
    uint64_t address_size = 0; state->address = redwood_image_file("fw", "bt-address", &address_size, NULL);
    if (address_size != 6 || !lemonade_bt_nvm_prepare(nvm, (size_t)length, state->address)) return false;
    redwood_tl("bt-lemonade: loading stock NVM=%s bytes=%u; private address; awake UART=115200\n", name, (unsigned)length);
    if (!download_tlv(state, parser, nvm, (size_t)length, 0)) return false;
    /* The QCA initialization sequence disables SoC logging after NVM and
     * before Reset (btqca.c:qca_disable_soc_logging). Coexistence can produce
     * diagnostic ACL traffic immediately, so the receiver also demultiplexes
     * its reserved channel while awaiting the real command completion. */
    const uint8_t logging[] = {1,0x17,0xfc,2,0x14,0};
    if (!command_complete(state, parser, logging, sizeof(logging), -1)) return false;
    const uint8_t reset[] = {1,3,0x0c,0}, version[] = {1,0,0xfc,1,0x19};
    const uint8_t local[] = {1,1,0x10,0}, address[] = {1,9,0x10,0};
    if (!command_complete(state, parser, reset, sizeof(reset), -1)) return false;
    state->version = false;
    if (!command_complete(state, parser, version, sizeof(version), 0x19) ||
        !state->version || state->actual.patch != patch.patch ||
        !command_complete(state, parser, local, sizeof(local), -1) ||
        !command_complete(state, parser, address, sizeof(address), -1) || !state->address_matches) return false;
    state->firmware = true;
    redwood_tl("bt-lemonade: firmware acceptance patch/reset/HCI/address verified=1\n");
    return true;
}
static bool revalidate_native_controller(struct bt_identification *state, struct lemonade_bt_parser *parser) {
    if (!state->firmware || parser->error) return false;
    struct lemonade_bt_version expected = state->actual;
    const uint8_t reset[] = {1,3,0x0c,0}, version[] = {1,0,0xfc,1,0x19}, address[] = {1,9,0x10,0};
    state->native = false; state->recovering = true;
    state->version = state->address_matches = false;
    bool ok = command_complete(state, parser, reset, sizeof(reset), -1) &&
        command_complete(state, parser, version, sizeof(version), 0x19) && state->version &&
        state->actual.product == expected.product && state->actual.rom == expected.rom &&
        state->actual.patch == expected.patch && state->actual.soc == expected.soc &&
        command_complete(state, parser, address, sizeof(address), -1) && state->address_matches &&
        !parser->used && !parser->error;
    state->recovering = false; state->native = true;
    redwood_tl("bt-lemonade: reconnect real reset/firmware/address verification=%u\n", ok);
    return ok;
}
#include "lemonade_bt_native.h"

static bool scan_controller(struct bt_identification *state, struct lemonade_bt_parser *parser) {
    const uint8_t mask[] = {1,1,0x0c,8,0xff,0xff,0xfb,0xff,7,0xf8,0xbf,0x3d};
    const uint8_t le_mask[] = {1,1,0x20,8,0x1f,0,0,0,0,0,0,0};
    const uint8_t params[] = {1,0x0b,0x20,7,0,0x60,0,0x30,0,0,0};
    const uint8_t enable[] = {1,0x0c,0x20,2,1,0}, disable[] = {1,0x0c,0x20,2,0,0};
    if (!state->firmware || !command_complete(state, parser, mask, sizeof(mask), -1) ||
        !command_complete(state, parser, le_mask, sizeof(le_mask), -1) ||
        !command_complete(state, parser, params, sizeof(params), -1) ||
        !command_complete(state, parser, enable, sizeof(enable), -1)) return false;
    bool ok = true; state->reports = 0;
    for (unsigned i = 0; i < 50000; i++) {
        if (!poll_rx(state, parser)) { ok = false; break; }
        pause_us(100);
    }
    bool stopped = command_complete(state, parser, disable, sizeof(disable), -1);
    redwood_tl("bt-lemonade: REAL LE scan reports=%u clean=%u stopped=%u; addresses and names omitted\n",
        state->reports, ok, stopped);
    return ok && stopped;
}
static bool identify_controller(vm_offset_t gcc, vm_offset_t pins, vm_offset_t se, unsigned mode) {
    const unsigned gpios[] = {65, 68, 69, 70, 71, 153};
    const unsigned offsets[] = {0x20,0x24,0x28,0x48,0x4c,0x7c,0x22c,0x254,0x258,
        0x25c,0x260,0x264,0x268,0x26c,0x270,0x280,0x284,0x288,0x28c,0x294,
        0x2a4,0x2a8,0x2ac,0x614,0x644,0x80c,0x810,0x814,0xe18,0xe1c,0xe30};
    uint32_t saved_pins[6][2], saved_se[sizeof(offsets)/sizeof(offsets[0])], saved_rcg[4];
    bool changed = false, pins_changed = false, restored = true;
    vm_offset_t wrapper = 0;
    static struct lemonade_bt_parser parser;
    struct bt_identification state = {.se = se};
    if ((read_register(pins, 65 * 0x1000 + 4) & 1) || (read_register(se, 0x40) & 0x1001) ||
        (read_register(se, 0x800) & 0xffffff) || (read_register(se, 0x804) & 0x1ffffff)) {
        redwood_tl("bt-lemonade: cold identification refused; controller or UART is active\n"); return true;
    }
    wrapper = ml_io_map_unmappable(0x8c0000, 0x2000, VM_WIMG_IO);
    if (!wrapper) return true;
    uint32_t hw = read_register(wrapper, 4);
    unsigned major = hw >> 28, minor = (hw >> 16) & 0xfff;
    if (major < 2 || major > 3) { redwood_tl("bt-lemonade: unknown QUP revision=%#x\n", hw); goto out; }
    unsigned divisor = major > 2 || minor >= 5 ? 4 : 2;
    for (unsigned i = 0; i < 6; i++) {
        saved_pins[i][0] = read_register(pins, gpios[i] * 0x1000);
        saved_pins[i][1] = read_register(pins, gpios[i] * 0x1000 + 4);
    }
    for (unsigned i = 0; i < sizeof(offsets)/sizeof(offsets[0]); i++) saved_se[i] = read_register(se, offsets[i]);
    for (unsigned i = 0; i < 4; i++) saved_rcg[i] = read_register(gcc, 0x1e4d4 + 4*i);
    changed = true;
    /* 7.3728 MHz from GPLL0-even: pinned SM8350 frequency table M=384/N=15625. */
    write_register(gcc, 0x1e4d8, 384);
    write_register(gcc, 0x1e4dc, (~(15625u - 384)) & 0xffff);
    write_register(gcc, 0x1e4e0, (~15625u) & 0xffff);
    write_register(gcc, 0x1e4d4, (saved_rcg[0] & ~0x371fu) | 0x2601);
    if (!rcg_commit(gcc)) { redwood_tl("bt-lemonade: UART root clock update timed out\n"); goto restore; }
    write_register(se, 0xe18, 0); write_register(se, 0xe1c, 0);
    write_register(se, 0x614, 0); write_register(se, 0x644, 0);
    write_register(se, 0x258, 0); /* strictly FIFO; no DMA descriptors */
    write_register(se, 0x28, read_register(se, 0x28) | 0x7f);
    write_register(se, 0xe30, read_register(se, 0xe30) | 0xf);
    write_register(se, 0x24, 0x7f); write_register(se, 0x20, 1);
    write_register(se, 0x7c, 0);
    write_register(se, 0x48, (divisor << 4) | 1); write_register(se, 0x4c, (divisor << 4) | 1);
    write_register(se, 0x22c, 0); /* never loop back */
    write_register(se, 0x254, 0);
    write_register(se, 0x260, 0xf); write_register(se, 0x264, 0);
    write_register(se, 0x284, 0xf); write_register(se, 0x288, 0); /* one byte per FIFO word */
    write_register(se, 0x25c, 0); write_register(se, 0x280, 0); /* 8N1 + hardware CTS */
    write_register(se, 0x268, 8); write_register(se, 0x28c, 8); write_register(se, 0x26c, 0);
    write_register(se, 0x2a4, 0); write_register(se, 0x2a8, 0); write_register(se, 0x2ac, 0);
    write_register(se, 0x294, 160); write_register(se, 0x80c, 1);
    write_register(se, 0x810, 1); write_register(se, 0x814, 2);
    write_register(se, 0x618, 0xffffffff); write_register(se, 0x648, 0xffffffff);
    if (!lemonade_radio_supply_on("smpb11", 950) || !lemonade_radio_supply_on("smpc1", 1880) ||
        !lemonade_radio_supply_on("smpb12", 1350) || !lemonade_radio_supply_on("ldoe7", 2800)) {
        redwood_tl("bt-lemonade: controller supply request failed\n"); goto restore;
    }
    pins_changed = true;
    emergency_pins = pins; emergency_radio = true;
    write_register(pins, 65 * 0x1000 + 4, saved_pins[0][1] & ~2u);
    write_register(pins, 65 * 0x1000, (saved_pins[0][0] & ~0x3ffu) | 0x200);
    write_register(pins, 153 * 0x1000, saved_pins[5][0] & ~0x3ffu);
    for (unsigned i = 1; i < 5; i++) {
        unsigned pull = gpios[i] == 69 ? 1 : gpios[i] == 70 ? 3 : 0;
        write_register(pins, gpios[i] * 0x1000, (saved_pins[i][0] & ~0x3ffu) | 4 | pull);
    }
    pause_us(150000); /* original board power sequence: 50 ms reset + 100 ms discharge */
    write_register(pins, 65 * 0x1000 + 4, saved_pins[0][1] | 2);
    pause_us(50000);
    /* Start reception only after reset release and the board startup delay;
     * otherwise the unpowered RX line is sampled as a spurious break byte. */
    write_register(se, 0x630, (1u << 27) | 0x80); /* receive, RFR open */
    pause_us(100);
    redwood_tl("bt-lemonade: QUP=%#x UART=115200 divisor=%u BT_EN=%u SW_CTRL=%u IOS=%#x\n",
        hw, divisor, read_register(pins, 65 * 0x1000 + 4) & 1,
        read_register(pins, 153 * 0x1000 + 4) & 1, read_register(se, 0x908));
    const uint8_t version_query[] = {1, 0x00, 0xfc, 1, 0x19};
    lemonade_bt_parser_init(&parser, LEMONADE_BT_FROM_CONTROLLER, 1);
    if (!command_complete(&state, &parser, version_query, sizeof(version_query), 0x19)) goto restore;
    if (state.version) {
        const uint8_t board_query[] = {1, 0x00, 0xfc, 1, 0x23};
        (void)command_complete(&state, &parser, board_query, sizeof(board_query), 0x23);
        if (mode >= 4 && !load_controller_firmware(&state, &parser))
            redwood_tl("bt-lemonade: firmware acceptance failed; reset required\n");
        if (mode == 5 && !scan_controller(&state, &parser))
            redwood_tl("bt-lemonade: low-level scan failed\n");
        if (mode == 6 && state.firmware) (void)native_run(&state, &parser);
    }
    redwood_tl("bt-lemonade: identify verified=%u events=%u received_bytes=%u framing_error=%u RX_IRQ=%#x\n",
        state.version, state.events, state.bytes, parser.error, read_register(se, 0x640));
restore:
    if (changed) {
        /* Stop both sequencers before restoring any FIFO/clock configuration. */
        bool tx_idle = stop_sequencer(se, false);
        bool rx_idle = stop_sequencer(se, true);
        bool idle = tx_idle && rx_idle;
        if (pins_changed) {
            lemonade_bluetooth_quiesce();
            pause_us(100000); /* board BT-OFF reset hold, from the vendor driver */
            emergency_radio = false; emergency_pins = 0;
        }
        if (!idle) { restored = false; redwood_tl("bt-lemonade: UART abort timed out; clocks retained; reboot required\n"); }
        if (idle) {
            for (unsigned i = 0; i < 64 && (read_register(se, 0x804) & 0x1ffffff); i++) (void)read_register(se, 0x780);
            write_register(se, 0x618, 0xffffffff); write_register(se, 0x648, 0xffffffff);
            for (unsigned i = 0; i < sizeof(offsets)/sizeof(offsets[0]); i++) write_register(se, offsets[i], saved_se[i]);
            for (unsigned i = 0; i < 4; i++) write_register(gcc, 0x1e4d4 + 4*i, saved_rcg[i]);
            bool clock_ok = rcg_commit(gcc);
            restored = clock_ok;
            redwood_tl("bt-lemonade: UART stopped; root clock restored=%u\n", clock_ok);
        }
    }
    if (pins_changed) {
        for (unsigned i = 0; i < 6; i++) {
            write_register(pins, gpios[i] * 0x1000 + 4, saved_pins[i][1]);
            write_register(pins, gpios[i] * 0x1000, saved_pins[i][0]);
        }
        redwood_tl("bt-lemonade: controller reset low; pin journal restored; shared supply votes retained\n");
    }
out:
    if (wrapper) ml_io_unmap(wrapper, 0x2000);
    return restored;
}

static void bluetooth_run(unsigned mode) {
    if (!mode) { return; }
    DTEntry entry; const void *platform; unsigned size;
    if (SecureDTLookupEntry(NULL, "/arm-io", &entry) != kSuccess ||
        SecureDTGetProperty(entry, "device_type", &platform, &size) != kSuccess ||
        size != sizeof("lemonade") || memcmp(platform, "lemonade", size)) { return; }
    if (mode == 6) {
        uint64_t length = 0;
        const uint8_t *address = redwood_image_file("fw", "bt-address", &length, NULL);
        if (length != 6 || !LemonadeBluetoothIdentity(address, false)) {
            redwood_tl("bt-lemonade: private real identity unavailable; native start refused\n"); return;
        }
    }
    if (mode < 1 || mode > 6) {
        redwood_tl("bt-lemonade: unsupported mode; transport remains unavailable\n");
        return;
    }
    vm_offset_t gcc = ml_io_map_unmappable(0x100000, 0x100000, VM_WIMG_IO);
    vm_offset_t pins = ml_io_map_unmappable(0xf100000, 0x100000, VM_WIMG_IO);
    vm_offset_t se = 0;
    uint32_t old_votes = 0, old_master = 0, old_slave = 0;
    bool clocks_changed = false;
    if (!gcc || !pins) {
        redwood_tl("bt-lemonade: register mapping failed\n");
        goto out;
    }
    const unsigned gpios[] = {64, 65, 68, 69, 70, 71, 153};
    for (unsigned i = 0; i < sizeof(gpios) / sizeof(gpios[0]); i++) {
        unsigned offset = 0x1000 * gpios[i];
        redwood_tl("bt-lemonade: GPIO %u config %#x level %#x\n", gpios[i],
            read_register(pins, offset), read_register(pins, offset + 4));
    }
    uint32_t votes = read_register(gcc, 0x52010);
    uint32_t master = read_register(gcc, 0x1e004), slave = read_register(gcc, 0x1e008);
    uint32_t serial = read_register(gcc, 0x1e4cc);
    redwood_tl("bt-lemonade: clocks votes %#x master %#x slave %#x serial %#x\n", votes, master, slave, serial);
    redwood_tl("bt-lemonade: RCGR command %#x config %#x M %#x N %#x D %#x\n",
        read_register(gcc, 0x1e4d0), read_register(gcc, 0x1e4d4),
        read_register(gcc, 0x1e4d8), read_register(gcc, 0x1e4dc), read_register(gcc, 0x1e4e0));
    if (mode >= 2) {
        old_votes = votes; old_master = master; old_slave = slave;
        clocks_changed = true;
        /* The wrapper AHB branches are hardware-gated even with a valid vote.
         * Hold only these two branch gates open during register observation.
         * Never change a UART root frequency or touch a radio enable pin here. */
        write_register(gcc, 0x1e004, master & ~2u);
        write_register(gcc, 0x1e008, slave & ~2u);
        write_register(gcc, 0x52010, votes | 0x106);
        for (unsigned i = 0; i < 1000; i++) {
            master = read_register(gcc, 0x1e004);
            slave = read_register(gcc, 0x1e008);
            serial = read_register(gcc, 0x1e4cc);
            if (!((master | slave | serial) & (1u << 31))) { break; }
            delay_for_interval(10, NSEC_PER_USEC);
        }
        votes = read_register(gcc, 0x52010);
        redwood_tl("bt-lemonade: clock probe votes %#x master %#x slave %#x serial %#x\n",
            votes, master, slave, serial);
    }
    if ((votes & 0x106) != 0x106 || ((master | slave | serial) & (1u << 31))) {
        redwood_tl("bt-lemonade: SE18 access skipped; required clocks are not established\n");
        goto out;
    }
    se = ml_io_map_unmappable(0x890000, 0x4000, VM_WIMG_IO);
    if (!se) { redwood_tl("bt-lemonade: SE18 mapping failed\n"); goto out; }
    uint32_t fw = read_register(se, 0x68), sfw = read_register(se, 0x6c);
    redwood_tl("bt-lemonade: SE18 protocol %u master revision %#x slave revision %#x status %#x\n",
        (fw >> 8) & 0xff, fw, sfw, read_register(se, 0x40));
    if (((fw >> 8) & 0xff) != 2 || ((sfw >> 8) & 0xff) != 2) {
        redwood_tl("bt-lemonade: UART protocol is not loaded; no transport available\n");
        goto out;
    }
    redwood_tl("bt-lemonade: UART clocks M %#x S %#x IOS %#x TX count %#x RX count %#x DMA %#x\n",
        read_register(se, 0x48), read_register(se, 0x4c), read_register(se, 0x908),
        read_register(se, 0x800), read_register(se, 0x804), read_register(se, 0x258));
    redwood_tl("bt-lemonade: UART parameters TX %#x RX %#x packing TX %#x/%#x RX %#x/%#x\n",
        read_register(se, 0x25c), read_register(se, 0x280), read_register(se, 0x260),
        read_register(se, 0x264), read_register(se, 0x284), read_register(se, 0x288));
    if (mode >= 3 && !identify_controller(gcc, pins, se, mode)) clocks_changed = false;
out:
    if (se) { ml_io_unmap(se, 0x4000); }
    if (clocks_changed) {
        /* Restore only the bits this probe owns; retain unrelated live votes. */
        write_register(gcc, 0x52010, (read_register(gcc, 0x52010) & ~0x106u) | (old_votes & 0x106));
        write_register(gcc, 0x1e008, (read_register(gcc, 0x1e008) & ~2u) | (old_slave & 2));
        write_register(gcc, 0x1e004, (read_register(gcc, 0x1e004) & ~2u) | (old_master & 2));
        bool restored = (read_register(gcc, 0x52010) & 0x106) == (old_votes & 0x106) &&
            (read_register(gcc, 0x1e004) & 2) == (old_master & 2) &&
            (read_register(gcc, 0x1e008) & 2) == (old_slave & 2);
        redwood_tl("bt-lemonade: clock probe journal restored=%u\n", restored);
    }
    if (pins) { ml_io_unmap(pins, 0x100000); }
    if (gcc) { ml_io_unmap(gcc, 0x100000); }
    redwood_tl("bt-lemonade: inspection ended; mode=%u; HCI unavailable\n", mode);
}

void lemonade_bluetooth_stop(void) {
    if (!__atomic_load_n(&bt_worker_active, __ATOMIC_ACQUIRE)) return;
    __atomic_store_n(&bt_stop_requested, true, __ATOMIC_RELEASE);
    for (unsigned i = 0; i < 100 && __atomic_load_n(&bt_worker_active, __ATOMIC_ACQUIRE); i++) pause_us(50000);
    if (__atomic_load_n(&bt_worker_active, __ATOMIC_ACQUIRE)) {
        lemonade_bluetooth_quiesce();
        redwood_tl("bt-lemonade: worker stop timed out; controller held in reset\n");
    }
}
#ifndef LEMONADE_BLUETOOTH_HOST_TEST
static void bluetooth_worker(void *argument, wait_result_t result) {
    (void)argument; (void)result;
    bluetooth_run(6);
    __atomic_store_n(&bt_worker_active, false, __ATOMIC_RELEASE);
    thread_terminate(current_thread());
}
#endif
void lemonade_bluetooth_init(void) {
    uint32_t mode = 0;
    PE_parse_boot_argn("lemonade_bt", &mode, sizeof(mode));
#ifndef LEMONADE_BLUETOOTH_HOST_TEST
    if (mode == 6) {
        thread_t thread;
        __atomic_store_n(&bt_worker_active, true, __ATOMIC_RELEASE);
        if (kernel_thread_start(bluetooth_worker, NULL, &thread) == KERN_SUCCESS) thread_deallocate(thread);
        else { __atomic_store_n(&bt_worker_active, false, __ATOMIC_RELEASE); redwood_tl("bt-lemonade: worker creation failed\n"); }
        return;
    }
#endif
    bluetooth_run(mode);
}
