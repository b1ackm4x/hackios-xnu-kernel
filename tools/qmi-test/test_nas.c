#include <assert.h>
#include <stdio.h>
#include <sys/redwood_nas.h>
#include <sys/redwood_qrtr.h>

/* Literal NAS frames using the public QMI TLV layout. */
static const unsigned char registered[] = {
    2, 1, 0, 0x24, 0, 15, 0,
    2, 4, 0, 0, 0, 0, 0,
    1, 5, 0, 1, 2, 2, 2, 0
};
static const unsigned char disconnected[] = {
    4, 1, 0, 0x24, 0, 8, 0,
    1, 5, 0, 0, 3, 3, 0, 0
};
static const unsigned char lte_signal[] = {
    2, 2, 0, 0x4f, 0, 16, 0,
    2, 4, 0, 0, 0, 0, 0,
    0x14, 6, 0, 0xba, 0xf6, 0xa1, 0xff, 0x64, 0
};
static const unsigned char failed_signal[] = {
    2, 2, 0, 0x4f, 0, 16, 0,
    2, 4, 0, 1, 0, 1, 0,
    0x14, 6, 0, 0xba, 0xf6, 0xa1, 0xff, 0x64, 0
};

static redwood_qrtr_receive_t received;
static redwood_qrtr_server_t server;
static void *callback_context;
static int requests, updates;

static struct redwood_qrtr_port *open_port(redwood_qrtr_receive_t receive, redwood_qrtr_server_t on_server, void *context)
{
    received = receive; server = on_server; callback_context = context;
    return (struct redwood_qrtr_port *)(uintptr_t)1;
}
static int send_packet(struct redwood_qrtr_port *port, uint32_t node, uint32_t peer,
                       const void *data, size_t length)
{
    const unsigned char *bytes = data;
    assert(port && node == 7 && peer == 9 && length == 7 && bytes[0] == 0);
    assert((requests == 0 && bytes[3] == 0x24) || (requests == 1 && bytes[3] == 0x4f));
    requests++;
    return 0;
}
static int lookup_port(struct redwood_qrtr_port *port, uint32_t service, uint32_t version, uint32_t instance)
{
    assert(port && service == 3 && version == 0 && instance == 0);
    server(callback_context, true, 3, 1, 0, 7, 9);
    return 0;
}
static void close_port(struct redwood_qrtr_port *port) { assert(port); }
static void changed(void *context, const struct redwood_nas_state *state)
{
    assert(context && state);
    updates++;
}
static const struct redwood_qrtr_ops transport = {
    .open = open_port, .send = send_packet, .lookup = lookup_port, .close = close_port
};

int main(void)
{
    struct redwood_nas_state state = {0};
    assert(redwood_nas_apply(&state, lte_signal, sizeof(lte_signal)) == 1);
    assert(state.has_signal && !state.registered && state.bars == 0);
    assert(redwood_nas_apply(&state, registered, sizeof(registered)) == 1);
    assert(state.registered && state.bars == 4);     /* the reading from before the registration counts */
    assert(redwood_nas_apply(&state, lte_signal, sizeof(lte_signal)) == 0);   /* the same reading again: no change */
    assert(state.radio_interface == 8 && state.dbm == -95 && state.bars == 4);
    assert(redwood_nas_apply(&state, failed_signal, sizeof(failed_signal)) == 0);
    assert(state.bars == 4);
    for (size_t n = 0; n < sizeof(lte_signal); n++) {
        assert(redwood_nas_apply(&state, lte_signal, n) == -1);
        assert(state.bars == 4);
    }
    assert(redwood_nas_apply(&state, disconnected, sizeof(disconnected)) == 1);
    assert(!state.registered && state.has_signal && state.bars == 0);   /* the last reading is kept, no bars */
    assert(redwood_nas_apply(&state, registered, sizeof(registered)) == 1);
    assert(state.registered && state.bars == 4);     /* ... and they come back with the registration */
    assert(redwood_nas_apply(&state, disconnected, sizeof(disconnected)) == 1);
    struct redwood_nas_client client;
    assert(redwood_nas_client_open(&client, &transport, changed, &client) == 0);
    assert(requests == 2);
    received(callback_context, 7, 9, registered, sizeof(registered));
    received(callback_context, 7, 9, lte_signal, sizeof(lte_signal));
    assert(client.state.registered && client.state.bars == 4 && updates == 2);
    received(callback_context, 8, 9, disconnected, sizeof(disconnected));
    assert(client.state.registered && updates == 2);
    server(callback_context, false, 3, 1, 0, 7, 9);
    assert(!client.state.registered && client.state.bars == 0 && updates == 3);
    redwood_nas_client_close(&client);
    puts("nas-test: registration gates bars, the last reading kept across a lost registration; QRTR lookup, polling and disconnect verified");
    return 0;
}
