#ifndef LEMONADE_DISPLAY_H
#define LEMONADE_DISPLAY_H
#include <stdint.h>
#include <kern/thread_call.h>
#ifdef __cplusplus
extern "C" {
#endif
bool lemonade_display_vsync_register(thread_call_t);
void lemonade_display_vsync_unregister(thread_call_t);
bool lemonade_display_vsync_snapshot(uint64_t *, uint64_t *, uint64_t *);
struct lemonade_present_window {
    uint64_t began, te;
    uint32_t frame, line, tear;
};
/* 0: inactive; 1: safe measured idle window; -1: active but window unavailable. */
int lemonade_display_present_begin(struct lemonade_present_window *);
void lemonade_display_present_end(const struct lemonade_present_window *, uint32_t);
/* Synchronous, one presenter. Caller holds prepared source memory throughout.
 * 1: transferred and unmapped; 0: CPU fallback safe; -1: retain source and do
 * not release producer (transfer/restoration safety could not be established). */
bool lemonade_display_direct_requested(void);
/* Zero: no override. Otherwise the mode measured by the active desktop path. */
uint32_t lemonade_display_refresh_hz(void);
int lemonade_display_direct_blank(uint32_t swap);
int lemonade_display_direct_present(const void *, uint32_t, uint32_t, uint32_t, uint32_t);
int lemonade_display_direct_fallback(void);
#ifdef __cplusplus
}
#endif
#endif
