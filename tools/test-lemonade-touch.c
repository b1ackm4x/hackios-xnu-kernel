/* Host-side tests of documented wire fields; compile with -I xnu/files/bsd/dev. */
#include <assert.h>
#include <stdio.h>
#include "lemonade_touch_protocol.h"
int main(void) {
 struct lemonade_contact c;
 const uint8_t bottom_right[8]={0x44,0x43,0x95,0x7f,20,10,1,0};
 assert(lemonade_decode_contact(bottom_right,&c));
 assert(c.finger==0 && c.action==1 && c.x==65535 && c.y==65535);
 const uint8_t top_left_release[8]={0xe8,0,0,0,0,0,0,0};
 assert(lemonade_decode_contact(top_left_release,&c));
 assert(c.finger==9 && c.action==3 && c.x==0 && c.y==0);
 const uint8_t middle_move[8]={0x84,0x21,0x4b,0xc0,20,10,1,0};
 assert(lemonade_decode_contact(middle_move,&c));
 assert(c.action==2 && c.x==32798 && c.y==32781);
 const uint8_t invalid[][8]={{0},{0x40},{0x6c},{0x45},{0x44,0xff,0,0},{0x44,0,0xff,0}};
 for(unsigned i=0;i<sizeof(invalid)/8;i++) assert(!lemonade_decode_contact(invalid[i],&c));
 puts("S6SY792 wire-format boundary and malformed-packet checks passed");
}
