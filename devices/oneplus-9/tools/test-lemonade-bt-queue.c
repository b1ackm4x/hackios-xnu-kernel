#include "../../../xnu/files/bsd/dev/lemonade_bt_queue.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    static struct lemonade_bt_queue q;
    static uint8_t source[65536], result[65536];
    for (unsigned i = 0; i < sizeof(source); i++) source[i] = i * 17;
    assert(lemonade_bt_queue_push(&q, source, sizeof(source)));
    assert(!lemonade_bt_queue_push(&q, source, 1));
    assert(lemonade_bt_queue_pop(&q, result, 50000) == 50000);
    assert(!memcmp(result, source, 50000));
    assert(lemonade_bt_queue_push(&q, source, 50000));
    assert(!lemonade_bt_queue_push(&q, source, 1));
    assert(lemonade_bt_queue_pop(&q, result, sizeof(result)) == sizeof(result));
    assert(!memcmp(result, source + 50000, 15536));
    assert(!memcmp(result + 15536, source, 50000));
    assert(!q.used && !lemonade_bt_queue_pop(&q, result, 1));
    for (unsigned i = 0; i < 1000; i++) {
        assert(lemonade_bt_queue_push(&q, source, 249));
        assert(lemonade_bt_queue_pop(&q, result, 249) == 249);
        assert(!memcmp(result, source, 249));
    }
    puts("Bluetooth queue wrap, full backpressure, ordering and repeated drain tests passed");
}
