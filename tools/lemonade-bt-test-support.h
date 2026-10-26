#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef void *DTEntry;
#define VM_WIMG_IO 0
#define NSEC_PER_USEC 1000
#define kSuccess 0
#ifndef __printflike
#define __printflike(a,b) __attribute__((format(printf,a,b)))
#endif
static uint32_t bt_test_read(uintptr_t);
static void bt_test_write(uintptr_t, uint32_t);
void delay_for_interval(unsigned, unsigned);
int PE_parse_boot_argn(const char *, void *, unsigned);
int SecureDTLookupEntry(void *, const char *, DTEntry *);
int SecureDTGetProperty(DTEntry, const char *, const void **, unsigned *);
bool lemonade_radio_supply_on(const char *, unsigned);
