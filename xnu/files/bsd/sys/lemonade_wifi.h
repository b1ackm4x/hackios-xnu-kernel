#ifndef _SYS_LEMONADE_WIFI_H_
#define _SYS_LEMONADE_WIFI_H_
#include <stdbool.h>
#include <stdint.h>

/* Available only while the exact-platform PCIe/MHI runtime owns the device. */
void lemonade_wifi_stop(void);
unsigned lemonade_wifi_driver_mode(void);
void *lemonade_wifi_driver_memory(unsigned long size, unsigned long long *iova);
const uint8_t *lemonade_wifi_driver_file(const char *name, uint64_t *size);
void *lemonade_wifi_driver_bar(void);
uint32_t lemonade_wifi_register_read(uint32_t offset);
void lemonade_wifi_register_write(uint32_t offset, uint32_t value);
#endif
