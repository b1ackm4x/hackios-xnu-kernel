#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "../../../userland/src/lemonade_bt_hid_report.h"

/* Standard keyboard, LED and independent consumer reports. No device-specific
 * identifiers, radio addresses, captured input or pairing material. */
static const uint8_t descriptor[]={
    0x05,1,0x09,6,0xa1,1,0x85,1,
    0x05,7,0x19,0xe0,0x29,0xe7,0x15,0,0x25,1,0x75,1,0x95,8,0x81,2,
    0x75,8,0x95,1,0x81,3,
    0x05,8,0x19,1,0x29,5,0x75,1,0x95,5,0x91,2,
    0x75,3,0x95,1,0x91,3,
    0x05,7,0x19,0,0x29,0xff,0x15,0,0x26,0xff,0,0x75,8,0x95,6,0x81,0,0xc0,
    0x05,12,0x09,1,0xa1,1,0x85,2,0x15,0,0x25,1,
    0x09,0xe9,0x09,0xea,0x75,1,0x95,2,0x81,2,
    0x75,6,0x95,1,0x81,3,0xc0
};
static uint32_t events[1024];static unsigned count, fail_at;
static bool emit(void *context,uint32_t key,bool down) {
    (void)context;
    if (fail_at && count+1==fail_at) return false;
    assert(count<1024);events[count++]=key|((uint32_t)down<<31);return true;
}
int main(int argc,char **argv) {
    struct lbh_device *d=calloc(1,sizeof(*d));assert(d);
    assert(lbh_descriptor(d,descriptor,sizeof(descriptor)));
    uint8_t shifted[]={1,2,0,4,0,0,0,0,0}, clear[]={1,0,0,0,0,0,0,0,0}, volume[]={2,1};
    assert(lbh_input(d,shifted,sizeof(shifted),emit,NULL));
    assert(count==2 && events[0]==0x800700e1 && events[1]==0x80070004);
    assert(lbh_input(d,shifted,sizeof(shifted),emit,NULL)&&count==2); /* no duplicate downs */
    assert(lbh_input(d,volume,sizeof(volume),emit,NULL)&&count==3 && events[2]==0x800c00e9);
    assert(lbh_input(d,clear,sizeof(clear),emit,NULL)&&count==5);
    assert(events[3]==0x70004 && events[4]==0x700e1); /* consumer state remains */
    assert(lbh_release(d,emit,NULL)&&count==6&&events[5]==0xc00e9);
    count=0;assert(lbh_input(d,shifted,sizeof(shifted),emit,NULL));
    uint8_t rollover[]={1,0,0,1,1,1,1,1,1};
    assert(lbh_input(d,rollover,sizeof(rollover),emit,NULL)&&count==2);
    assert(!lbh_input(d,clear,sizeof(clear)-1,emit,NULL)&&count==2);
    uint8_t unknown[]={3,0};assert(!lbh_input(d,unknown,sizeof(unknown),emit,NULL));
    assert(lbh_release(d,emit,NULL)&&count==4);
    count=0;fail_at=2;
    assert(!lbh_input(d,shifted,sizeof(shifted),emit,NULL)&&count==1);
    fail_at=0;assert(lbh_input(d,shifted,sizeof(shifted),emit,NULL)&&count==2);
    assert(lbh_release(d,emit,NULL));
    uint8_t output[LBH_BYTES+1];size_t length=0;
    assert(lbh_led(d,2,true,output,&length)==1&&length==2&&output[0]==1&&output[1]==2);
    assert(lbh_led(d,1,true,output,&length)==1&&output[1]==3);
    assert(lbh_led(d,2,false,output,&length)==1&&output[1]==1);
    assert(lbh_led(d,6,true,output,&length)==-1);
    for(size_t n=0;n<sizeof(descriptor);n++) {
        struct lbh_device trial;
        bool valid=lbh_descriptor(&trial,descriptor,n);
        if(valid) {count=0;(void)lbh_input(&trial,shifted,sizeof(shifted),emit,NULL);}
    }
    unsigned seed=380;
    for(unsigned i=0;i<20000;i++) {
        size_t n=rand_r(&seed)%400;uint8_t *bytes=malloc(n?n:1);assert(bytes);
        for(size_t j=0;j<n;j++)bytes[j]=(uint8_t)rand_r(&seed);
        if(lbh_descriptor(d,bytes,n)) {count=0;(void)lbh_input(d,bytes,n,emit,NULL);}
        assert(lbh_descriptor(d,descriptor,sizeof(descriptor)));
        count=0;(void)lbh_input(d,bytes,n,emit,NULL);free(bytes);
    }
    if(argc==2) {
        FILE *stream=fopen(argv[1],"rb");assert(stream);uint8_t bytes[4097];
        size_t n=fread(bytes,1,sizeof(bytes),stream);fclose(stream);
        assert(lbh_descriptor(d,bytes,n));
        printf("Private real descriptor accepted: reports=%u fields=%u; no report bytes retained\n",d->report_count,d->field_count);
    }
    free(d);puts("PASS: HID modifier ordering, report isolation, rollover, release, retry, LEDs, malformed/random inputs");
}
