/* Native bluetoothd H4/POSIX kernel-control transport. Hardware is accessed
 * only by the owning worker. Socket callbacks only move bytes or options. */
#ifndef LEMONADE_BT_NATIVE_H
#define LEMONADE_BT_NATIVE_H
#ifndef LEMONADE_BLUETOOTH_HOST_TEST
#include <kern/locks.h>
#include <sys/errno.h>
#include <sys/kauth.h>
#include <sys/kern_control.h>
#include <sys/kpi_mbuf.h>
#include <sys/socket.h>
#include <sys/termios.h>
#include "lemonade_bt_queue.h"

static LCK_GRP_DECLARE(bt_native_group, "lemonade-bluetooth");
static LCK_MTX_DECLARE(bt_native_lock, &bt_native_group);
static kern_ctl_ref bt_control;
static uint32_t bt_unit;
static bool bt_online, bt_closed, bt_fault;
static struct lemonade_bt_queue bt_outgoing;
static struct termios bt_termios;
static unsigned bt_native_tx, bt_native_rx;

static errno_t native_connect(kern_ctl_ref ref, struct sockaddr_ctl *address, void **info) {
    (void)ref;
    uid_t uid = kauth_cred_getuid(kauth_cred_get());
    if (uid != 0 && uid != 501) return EACCES;
    lck_mtx_lock(&bt_native_lock);
    errno_t error = !bt_online || bt_fault || bt_closed ? ENXIO : bt_unit ? EBUSY : 0;
    if (!error) { bt_unit = address->sc_unit; *info = &bt_outgoing; }
    lck_mtx_unlock(&bt_native_lock);
    redwood_tl("bt-lemonade: native H4 connect uid=%u result=%d\n", uid, error);
    return error;
}
static errno_t native_disconnect(kern_ctl_ref ref, uint32_t unit, void *info) {
    (void)ref; (void)info;
    lck_mtx_lock(&bt_native_lock);
    if (unit == bt_unit) { bt_unit = 0; bt_closed = true; }
    lck_mtx_unlock(&bt_native_lock);
    redwood_tl("bt-lemonade: native H4 disconnected; owner will reset and revalidate\n");
    return 0;
}
static errno_t native_send(kern_ctl_ref ref, uint32_t unit, void *info, mbuf_t m, int flags) {
    (void)ref; (void)info; (void)flags;
    size_t total = 0;
    for (mbuf_t next = m; next; next = mbuf_next(next)) {
        size_t size = mbuf_len(next);
        if (size > LEMONADE_BT_QUEUE_SIZE - total) { mbuf_freem(m); return EMSGSIZE; }
        total += size;
    }
    lck_mtx_lock(&bt_native_lock);
    errno_t error = !bt_online || bt_fault || unit != bt_unit ? ENXIO :
        total > LEMONADE_BT_QUEUE_SIZE - bt_outgoing.used ? ENOBUFS : 0;
    if (!error) {
        for (mbuf_t next = m; next; next = mbuf_next(next))
            (void)lemonade_bt_queue_push(&bt_outgoing, mbuf_data(next), mbuf_len(next));
    }
    lck_mtx_unlock(&bt_native_lock);
    mbuf_freem(m);
    return error;
}
static errno_t native_getopt(kern_ctl_ref ref, uint32_t unit, void *info, int option, void *data, size_t *size) {
    (void)ref; (void)info;
    if ((unsigned)option != 0x40487413u) {
        redwood_tl("bt-lemonade: native unsupported get option=%#x\n", option); return ENOPROTOOPT;
    }
    lck_mtx_lock(&bt_native_lock);
    errno_t error = !bt_online || unit != bt_unit ? ENXIO :
        data && *size < sizeof(bt_termios) ? EMSGSIZE : 0;
    if (!error) { if (data) memcpy(data, &bt_termios, sizeof(bt_termios)); *size = sizeof(bt_termios); }
    lck_mtx_unlock(&bt_native_lock);
    return error;
}
static errno_t native_setopt(kern_ctl_ref ref, uint32_t unit, void *info, int option, void *data, size_t size) {
    (void)ref; (void)info;
    if ((unsigned)option != 0x80487414u || size != sizeof(struct termios) || !data) {
        redwood_tl("bt-lemonade: native unsupported set option=%#x bytes=%u\n", option, (unsigned)size);
        return ENOPROTOOPT;
    }
    struct termios *requested = data;
    /* Report only settings the actual UART implements. No pretend baud or
     * framing change is accepted; initial transport is fixed 115200/8N1. */
    if (requested->c_ispeed != 115200 || requested->c_ospeed != 115200 ||
        (requested->c_cflag & (CSIZE | PARENB | CSTOPB)) != CS8 ||
        (requested->c_cflag & CRTSCTS) != CRTSCTS) {
        redwood_tl("bt-lemonade: unsupported native UART speed=%u/%u flags=%#x\n",
            (unsigned)requested->c_ispeed, (unsigned)requested->c_ospeed, (unsigned)requested->c_cflag);
        return EINVAL;
    }
    lck_mtx_lock(&bt_native_lock);
    errno_t error = !bt_online || unit != bt_unit ? ENXIO : 0;
    if (!error) bt_termios = *requested;
    lck_mtx_unlock(&bt_native_lock);
    return error;
}

static int native_receive(void *context, const uint8_t *packet, size_t size) {
    (void)context;
    lck_mtx_lock(&bt_native_lock);
    uint32_t unit = bt_unit;
    bool online = bt_online && !bt_fault;
    lck_mtx_unlock(&bt_native_lock);
    if (!online) return -1;
    if (!unit) return 0; /* no live host owns these events */
    errno_t error = 0;
    for (unsigned i = 0; i < 100; i++) {
        /* Never hold bt_native_lock across socket enqueue: send callbacks run
         * under the socket lock and take bt_native_lock in the other order. */
        error = ctl_enqueuedata(bt_control, unit, packet, size, 0);
        if (error != ENOBUFS) break;
        pause_us(1000);
    }
    bt_native_rx++;
    lck_mtx_lock(&bt_native_lock);
    bool disconnected = bt_closed || bt_unit != unit;
    lck_mtx_unlock(&bt_native_lock);
    if (error && disconnected) return 0; /* reset/drain before another client */
    if (bt_native_rx <= 100 && size >= 7 && packet[0] == 4 && packet[1] == 0x0e)
        redwood_tl("bt-lemonade: native REAL completion opcode=%#x status=%u delivery=%d\n",
            lemonade_bt_le16(packet + 4), packet[6], error);
    if (error) redwood_tl("bt-lemonade: native receive delivery failed=%d; session stopping\n", error);
    return error ? -1 : 0;
}
static int native_transmit(void *context, const uint8_t *packet, size_t size) {
    struct bt_identification *state = context;
    bt_native_tx++;
    if (bt_native_tx <= 100 && size >= 4 && packet[0] == 1)
        redwood_tl("bt-lemonade: native command opcode=%#x parameter_bytes=%u\n",
            lemonade_bt_le16(packet + 1), packet[3]);
    uint8_t corrected[6];
    if (state->firmware && state->actual.product == 0x13 && state->actual.rom == 0x100 &&
        state->actual.soc == 0x400c0110 && lemonade_bt_legacy_le_host(packet, size, corrected)) {
        redwood_tl("bt-lemonade: LE host support=%u obsolete simultaneous parameter=1 -> 0 per Core 5.2\n", packet[4]);
        return send_bytes(state->se, corrected, sizeof(corrected)) ? 0 : -1;
    }
    return send_bytes(state->se, packet, (unsigned)size) ? 0 : -1;
}
static bool native_run(struct bt_identification *state, struct lemonade_bt_parser *parser) {
    _Static_assert(sizeof(struct termios) == 72, "native iOS arm64 termios ABI");
    struct kern_ctl_reg reg = {0};
    strlcpy(reg.ctl_name, "com.apple.uart.bluetooth", sizeof(reg.ctl_name));
    reg.ctl_flags = CTL_FLAG_REG_SOCK_STREAM;
    reg.ctl_sendsize = reg.ctl_recvsize = 65536;
    reg.ctl_connect = native_connect; reg.ctl_disconnect = native_disconnect;
    reg.ctl_send = native_send; reg.ctl_getopt = native_getopt; reg.ctl_setopt = native_setopt;
    memset(&bt_termios, 0, sizeof(bt_termios));
    bt_termios.c_cflag = CS8 | CREAD | CLOCAL | CRTSCTS;
    bt_termios.c_ispeed = bt_termios.c_ospeed = 115200;
    errno_t error = ctl_register(&reg, &bt_control);
    if (error) { redwood_tl("bt-lemonade: native control registration error=%d\n", error); return false; }
    static struct lemonade_bt_parser tx;
    lemonade_bt_parser_init(&tx, LEMONADE_BT_FROM_HOST, 0);
    lemonade_bt_parser_init(parser, LEMONADE_BT_FROM_CONTROLLER, 1);
    state->native = true;
    lck_mtx_lock(&bt_native_lock); bt_online = true; lck_mtx_unlock(&bt_native_lock);
    (void)LemonadeBluetoothIdentity(state->address, true);
    redwood_tl("bt-lemonade: native H4 transport online; real controller firmware verified\n");
    bool ok = true;
    while (!__atomic_load_n(&bt_stop_requested, __ATOMIC_ACQUIRE)) {
        uint8_t bytes[256];
        lck_mtx_lock(&bt_native_lock);
        size_t size = lemonade_bt_queue_pop(&bt_outgoing, bytes, sizeof(bytes));
        bool closed = bt_closed;
        lck_mtx_unlock(&bt_native_lock);
        if (closed) {
            (void)LemonadeBluetoothIdentity(NULL, false);
            lck_mtx_lock(&bt_native_lock);
            bt_outgoing.head = bt_outgoing.used = 0;
            lck_mtx_unlock(&bt_native_lock);
            lemonade_bt_parser_init(&tx, LEMONADE_BT_FROM_HOST, 0);
            /* Preserve RX framing while draining the old session. A genuine
             * HCI reset and matching firmware/address readback must finish
             * before the next connection can observe a ready transport. */
            if (!revalidate_native_controller(state, parser)) { ok = false; break; }
            lck_mtx_lock(&bt_native_lock); bt_closed = false; lck_mtx_unlock(&bt_native_lock);
            (void)LemonadeBluetoothIdentity(state->address, true);
            redwood_tl("bt-lemonade: native transport ready for a new connection\n");
            continue;
        }
        if ((size && lemonade_bt_parse(&tx, bytes, size, native_transmit, NULL, state)) || !poll_rx(state, parser)) {
            ok = false; break;
        }
        pause_us(500);
    }
    lck_mtx_lock(&bt_native_lock); bt_online = false; bt_fault = !ok; uint32_t unit = bt_unit;
    lck_mtx_unlock(&bt_native_lock);
    if (!unit) (void)ctl_deregister(bt_control);
    state->native = false;
    (void)LemonadeBluetoothIdentity(NULL, false);
    redwood_tl("bt-lemonade: native transport stopped TX=%u RX=%u clean=%u\n", bt_native_tx, bt_native_rx, ok);
    return ok;
}
#else
static int native_receive(void *context, const uint8_t *packet, size_t size) {
    (void)context; (void)packet; (void)size; return -1;
}
static bool native_run(struct bt_identification *state, struct lemonade_bt_parser *parser) {
    (void)state; (void)parser; return false;
}
#endif
#endif
