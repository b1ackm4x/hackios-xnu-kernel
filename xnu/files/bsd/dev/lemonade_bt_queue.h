#ifndef LEMONADE_BT_QUEUE_H
#define LEMONADE_BT_QUEUE_H
#include <stddef.h>
#include <stdint.h>
#define LEMONADE_BT_QUEUE_SIZE 65536u
struct lemonade_bt_queue { uint8_t bytes[LEMONADE_BT_QUEUE_SIZE]; size_t head, used; };
/* Caller serializes access. Failed writes leave all queued bytes untouched. */
static inline int lemonade_bt_queue_push(struct lemonade_bt_queue *q, const uint8_t *data, size_t size) {
    if (size > LEMONADE_BT_QUEUE_SIZE - q->used) return 0;
    for (size_t i = 0; i < size; i++) q->bytes[(q->head + q->used + i) % LEMONADE_BT_QUEUE_SIZE] = data[i];
    q->used += size;
    return 1;
}
static inline size_t lemonade_bt_queue_pop(struct lemonade_bt_queue *q, uint8_t *data, size_t capacity) {
    size_t size = q->used < capacity ? q->used : capacity;
    for (size_t i = 0; i < size; i++) data[i] = q->bytes[(q->head + i) % LEMONADE_BT_QUEUE_SIZE];
    q->used -= size; q->head = (q->head + size) % LEMONADE_BT_QUEUE_SIZE;
    return size;
}
#endif
