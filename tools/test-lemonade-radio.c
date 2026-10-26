/* Exercise the production regulator transaction, including real pthread locks.
 * Only MMIO, delays and kernel/DT primitives are replaced by the host fixture. */
#define LEMONADE_RADIO_HOST_TEST 1
#include "../xnu/files/bsd/dev/lemonade_radio.c"
#include <stdarg.h>
static uint32_t registers[0x10000 / 4];
static union { uint64_t align; uint8_t bytes[0x20000]; } db_memory;
static bool wrong_platform, complete, triggered, forever_busy;
static unsigned writes, transactions, ticks, idle_after, submitted_mv;

static uint32_t radio_test_read(vm_offset_t address) {
    assert(address >= (uintptr_t)registers && address < (uintptr_t)(registers + sizeof(registers) / 4));
    if (address == (uintptr_t)registers + 0xd18 && triggered && ticks >= idle_after && !forever_busy)
        registers[0xd18 / 4] = 1;
    return *(uint32_t *)address;
}
static void radio_test_write(vm_offset_t address, uint32_t value) {
    writes++;
    if (address == (uintptr_t)registers + 0xd08) registers[0xd04 / 4] &= ~value;
    else *(uint32_t *)address = value;
    if (address == (uintptr_t)registers + 0xd14 && (value & (1u << 24))) {
        triggered = true; transactions++; ticks = 0;
        submitted_mv = registers[0xd38 / 4];
        registers[0xd18 / 4] = 0;
        if (complete) registers[0xd04 / 4] |= 1;
    }
}
static void delay_for_interval(unsigned amount, unsigned unit) { (void)amount; (void)unit; ticks++; }
static int SecureDTLookupEntry(void *root, const char *path, DTEntry *entry) {
    (void)root; (void)path; *entry = 1; return 0;
}
static int SecureDTGetProperty(DTEntry entry, const char *name, const void **value, unsigned *size) {
    (void)entry; (void)name; *value = wrong_platform ? "redwood" : "lemonade";
    *size = (unsigned)strlen(*value) + 1; return 0;
}
static vm_offset_t ml_io_map_unmappable(vm_offset_t address, vm_size_t size, unsigned flags) {
    (void)size; (void)flags;
    assert(address == 0x18220000 || address == 0x80860000);
    return address == 0x18220000 ? (uintptr_t)registers : (uintptr_t)db_memory.bytes;
}
static void redwood_tl(const char *format, ...) { (void)format; }
static void reset(void) {
    memset(registers, 0, sizeof(registers)); memset(&db_memory, 0, sizeof(db_memory));
    memset(retained_votes, 0, sizeof(retained_votes));
    rsc = database = 0; unavailable = wrong_platform = triggered = forever_busy = false;
    writes = transactions = ticks = submitted_mv = idle_after = 0; complete = true;
    registers[0xd18 / 4] = 1;
    struct radio_db_header *db = (void *)db_memory.bytes;
    memcpy(db->magic, "\xdb\x30\x03\x0c", 4); db->slave[0].id = 4; db->slave[0].count = 2;
    struct radio_db_entry *entry = (void *)db->data;
    memcpy(entry[0].id, "ldoe7", 6); entry[0].address = 0x40010;
    memcpy(entry[1].id, "alias", 6); entry[1].address = 0x40010;
}
static void *request(void *value) {
    for (unsigned i = 0; i < 100; i++) assert(lemonade_radio_supply_on("ldoe7", (unsigned)(uintptr_t)value));
    return NULL;
}
int main(void) {
    reset(); wrong_platform = true; assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(!writes);
    reset(); assert(!lemonade_radio_supply_on("missing", 1800)); assert(!writes);
    assert(!lemonade_radio_supply_on("ldoe7", 3301)); assert(!writes);
    reset(); registers[0xd18 / 4] = 0; assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(!writes && !unavailable);
    reset(); registers[0xd04 / 4] = 1; assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(!writes);
    reset();
    registers[0xd00 / 4] = 0x24; registers[0xd14 / 4] = 0x80;
    registers[0xd10 / 4] = 0x20; registers[0xd1c / 4] = 0x20;
    for (unsigned i = 0; i < 3; i++) for (unsigned j = 0; j < 3; j++) registers[(0xd30 + 20 * i + 4 * j) / 4] = 0x9000 + 3 * i + j;
    uint32_t before[sizeof(registers) / 4]; memcpy(before, registers, sizeof(before));
    idle_after = 7; assert(lemonade_radio_supply_on("ldoe7", 2800)); assert(ticks == 7 && submitted_mv == 2800);
    for (unsigned i = 0; i < sizeof(registers) / 4; i++) if (i != 0xd08 / 4) assert(before[i] == registers[i]);
    unsigned previous = writes; assert(lemonade_radio_supply_on("alias", 1800)); assert(writes == previous);
    assert(lemonade_radio_supply_on("alias", 3000)); assert(submitted_mv == 3000 && transactions == 2);
    reset(); complete = false; assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(unavailable);
    previous = writes; assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(writes == previous);
    assert(registers[0xd38 / 4] == 1800); /* In-flight command was not restored over. */
    reset(); forever_busy = true; assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(unavailable && ticks == 1000);
    reset(); for (unsigned i = 0; i < 32; i++) retained_votes[i].address = i + 1;
    assert(!lemonade_radio_supply_on("ldoe7", 1800)); assert(!writes);
    reset(); pthread_t a, b; assert(!pthread_create(&a, NULL, request, (void *)(uintptr_t)750));
    assert(!pthread_create(&b, NULL, request, (void *)(uintptr_t)1800));
    assert(!pthread_join(a, NULL)); assert(!pthread_join(b, NULL));
    assert(submitted_mv == 1800 && transactions <= 2 && !unavailable);
    puts("PASS: shared RPMh transaction, retained maximum vote, concurrent callers, restore and timeout quarantine");
}
