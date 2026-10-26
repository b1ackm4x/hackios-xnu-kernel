#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef int DTEntry;
#define kSuccess 0
#define NSEC_PER_USEC 1000
#define VM_WIMG_IO 0
#define VM_WIMG_WCOMB 1
#define LCK_GRP_DECLARE(name, label) int name __attribute__((unused))
#define LCK_MTX_DECLARE(name, group) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
#define lck_mtx_lock(lock) pthread_mutex_lock(lock)
#define lck_mtx_unlock(lock) pthread_mutex_unlock(lock)
static uint32_t radio_test_read(vm_offset_t address);
static void radio_test_write(vm_offset_t address, uint32_t value);
static void delay_for_interval(unsigned amount, unsigned unit);
static int SecureDTLookupEntry(void *root, const char *path, DTEntry *entry);
static int SecureDTGetProperty(DTEntry entry, const char *name, const void **value, unsigned *size);
static vm_offset_t ml_io_map_unmappable(vm_offset_t address, vm_size_t size, unsigned flags);
static void redwood_tl(const char *format, ...);
