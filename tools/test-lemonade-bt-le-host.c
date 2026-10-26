#include "../xnu/files/bsd/dev/lemonade_bt_protocol.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
int main(void) {
    uint8_t command[] = {1,0x6d,0x0c,2,1,1}, output[6] = {0};
    assert(lemonade_bt_legacy_le_host(command, sizeof(command), output));
    assert(!memcmp(output, command, 5) && output[5] == 0 && command[5] == 1);
    command[4] = 0;
    assert(lemonade_bt_legacy_le_host(command, sizeof(command), output));
    assert(output[4] == 0 && output[5] == 0);
    command[4] = 2; assert(!lemonade_bt_legacy_le_host(command, sizeof(command), output));
    command[4] = 1; command[5] = 0; assert(!lemonade_bt_legacy_le_host(command, sizeof(command), output));
    command[5] = 2; assert(!lemonade_bt_legacy_le_host(command, sizeof(command), output));
    command[5] = 1; command[0] = 2; assert(!lemonade_bt_legacy_le_host(command, sizeof(command), output));
    command[0] = 1; command[1] = 0x6c; assert(!lemonade_bt_legacy_le_host(command, sizeof(command), output));
    command[1] = 0x6d; command[3] = 1; assert(!lemonade_bt_legacy_le_host(command, sizeof(command), output));
    command[3] = 2;
    for (size_t i = 0; i < sizeof(command); i++) assert(!lemonade_bt_legacy_le_host(command, i, output));
    puts("Legacy LE host parameter: exact command/length/boolean gate and unchanged input tests passed");
}
