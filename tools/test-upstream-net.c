/* Host KPI shim. The tested attach/TX/RX/link-state functions come from production source. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../xnu/files/bsd/dev/redwood_modem_policy.h"
typedef int errno_t, lck_mtx_t, thread_t;
typedef struct fake_if { void *softc; unsigned unit, received, sent, failed, event; char id[16]; } *ifnet_t;
typedef struct fake_packet { uint8_t bytes[1600]; size_t len; ifnet_t owner; } *mbuf_t;
struct ifnet_stat_increment_param { unsigned packets_in, bytes_in; };
struct ifnet_init_eparams {
    unsigned ver, len, flags, uniqueid_len, unit, family, subfamily, type, broadcast_len;
    const char *uniqueid, *name; void *softc;
    errno_t (*output)(ifnet_t, mbuf_t);
    void *demux, *add_proto, *del_proto, *check_multi, *framer_extended, *ioctl;
    const uint8_t *broadcast_addr;
};
#define REDWOOD_NET_UNITS 2
#define REDWOOD_NET_FRAME_MAX 1514
#define ETHER_HDR_LEN 14
#define ETHER_ADDR_LEN 6
#define ETHERMTU 1500
#define KEV_DL_LINK_ON 1
#define KEV_DL_LINK_OFF 2
#define DLT_EN10MB 1
#define MBUF_DONTWAIT 0
#define IFNET_INIT_CURRENT_VERSION 1
#define IFNET_INIT_LEGACY 1
#define IFNET_INIT_NX_NOAUTO 2
#define IFNET_FAMILY_ETHERNET 1
#define IFNET_SUBFAMILY_WIFI 1
#define IFNET_SUBFAMILY_ANY 0
#define IFT_ETHER 1
#define IFF_BROADCAST 1
#define IFF_MULTICAST 2
#define IFF_SIMPLEX 4
#define LCK_ATTR_NULL 0
#define KERN_SUCCESS 0
#define ether_demux NULL
#define ether_add_proto NULL
#define ether_del_proto NULL
#define ether_check_multi NULL
#define ether_frameout_extended NULL
#define net_ioctl NULL
#define net_thread NULL
static const uint8_t etherbroadcastaddr[6] = {255,255,255,255,255,255};
static int net_lck_grp, lock_count, allocations, releases, allocation_error, attach_error, copy_error;
static char wlan_name[8];
static struct fake_if interfaces[2];
static void *ifnet_softc(ifnet_t p) { return p->softc; }
static size_t mbuf_pkthdr_len(mbuf_t p) { return p->len; }
static void lck_mtx_init(int *p, int *g, int a) { assert(!*p); *p=1; lock_count++; }
static void lck_mtx_destroy(int *p, int *g) { assert(*p==1); *p=0; lock_count--; }
static void lck_mtx_lock(int *p) { assert(*p==1); *p=2; }
static void lck_mtx_unlock(int *p) { assert(*p==2); *p=1; }
static void bpf_tap_out(ifnet_t p,int t,mbuf_t m,void *x,int z) {}
static void bpf_tap_in(ifnet_t p,int t,mbuf_t m,void *x,int z) {}
static void trace_packet(mbuf_t p,size_t n) {}
static void trace_frame(const char *s,const uint8_t *p,size_t n,size_t cap) {}
static void ifnet_stat_increment_out(ifnet_t p,int n,uint32_t bytes,int errors) { p->sent+=n; p->failed+=errors; }
static void mbuf_freem(mbuf_t p) { free(p); allocations--; }
static int mbuf_allocpacket(int flags,size_t length,unsigned *chunks,mbuf_t *p) {
    *p=calloc(1,sizeof(**p)); (*p)->len=length; allocations++; return 0;
}
static int mbuf_copyback(mbuf_t p,size_t off,size_t n,const void *s,int flags) { if(copy_error)return 1; memcpy(p->bytes+off,s,n);return 0; }
static char *mbuf_data(mbuf_t p) { return (char *)p->bytes; }
static void mbuf_pkthdr_setrcvif(mbuf_t p,ifnet_t f) { p->owner=f; }
static void mbuf_pkthdr_setheader(mbuf_t p,void *h) {}
static void mbuf_pkthdr_adjustlen(mbuf_t p,int n) { p->len+=n; }
static void mbuf_setdata(mbuf_t p,void *d,size_t n) { assert(p->len==n); }
static void ifnet_input(ifnet_t f,mbuf_t p,struct ifnet_stat_increment_param *stats) {
    assert(p->owner==f && stats->packets_in==1 && stats->bytes_in==60); f->received++; mbuf_freem(p);
}
static void net_link_event(ifnet_t p,int event) { p->event=event; }
static int ifnet_allocate_extended(struct ifnet_init_eparams *init,ifnet_t *p) {
    if(allocation_error)return 1;
    assert(init->unit<2 && init->uniqueid_len==strlen(init->uniqueid));
    *p=&interfaces[init->unit]; (*p)->softc=init->softc; (*p)->unit=init->unit;
    snprintf((*p)->id,sizeof((*p)->id),"%s",init->uniqueid);return 0;
}
#define ifnet_set_capabilities_enabled(...) ((void)0)
#define ifnet_set_addrlen(...) ((void)0)
#define ifnet_set_baudrate(...) ((void)0)
#define ifnet_set_mtu(...) ((void)0)
#define ifnet_set_flags(...) ((void)0)
#define ifnet_set_hdrlen(...) ((void)0)
#define ifnet_set_offload(...) ((void)0)
#define ifnet_set_lladdr(...) ((void)0)
#define bpfattach(...) ((void)0)
static int ifnet_attach(ifnet_t p,void *v) { return attach_error; }
static void ifnet_release(ifnet_t p) { releases++; }
static void RedwoodWLANStart(const char *name,const uint8_t *address) { snprintf(wlan_name,sizeof(wlan_name),"%s",name); }
static int kernel_thread_start(void *f,void *p,thread_t *t) { return 1; }
static void thread_deallocate(thread_t t) {}
#include "production.inc"
static unsigned tx[2];
static bool tx0(mbuf_t p,size_t n) { assert(n==60); tx[0]++;return true; }
static bool tx1(mbuf_t p,size_t n) { assert(n==60); tx[1]++;return true; }
static mbuf_t packet(size_t n) { mbuf_t p; unsigned chunks=1;mbuf_allocpacket(0,n,&chunks,&p);return p; }
int main(void) {
    assert(redwood_modem_platform_allowed("redwood",8));
    assert(!redwood_modem_platform_allowed("lemonade",9));
    assert(!redwood_modem_platform_allowed("redwood-extra",14));
    assert(!redwood_modem_platform_allowed("redwood",7));
    assert(!redwood_modem_platform_allowed(NULL,8));
    struct redwood_net_link wifi={.name="wifi",.wireless=true,.up=true,.transmit=tx0};
    struct redwood_net_link cell={.name="cell",.unit=1,.transmit=tx1};
    struct redwood_net_link bad={.name="bad",.unit=2};
    assert(!redwood_net_attach(&bad));
    allocation_error=1; assert(!redwood_net_attach(&wifi) && lock_count==0);
    allocation_error=0;attach_error=1;assert(!redwood_net_attach(&wifi) && lock_count==0 && releases==1);
    attach_error=0;assert(redwood_net_attach(&wifi) && lock_count==1);
    assert(!strcmp(wlan_name,"en0") && !strcmp(interfaces[0].id,"redwood-en0"));
    assert(!redwood_net_attach(&wifi));
    assert(redwood_net_attach(&cell) && lock_count==2);
    net_output(&interfaces[0],packet(60));net_output(&interfaces[1],packet(60));
    assert(tx[0]==1 && tx[1]==0 && interfaces[1].failed==1);
    redwood_net_link_state_unit(1,true);assert(net_ifs[0].up && net_ifs[1].up);
    net_output(&interfaces[1],packet(60));assert(tx[1]==1);
    redwood_net_link_state(false);assert(!net_ifs[0].up && net_ifs[1].up);
    net_output(&interfaces[0],packet(60));assert(tx[0]==1 && interfaces[0].failed==1);
    net_output(&interfaces[1],packet(1515));assert(tx[1]==1 && interfaces[1].failed==2);
    uint8_t frame[60]={0};
    redwood_net_input(frame,60); redwood_net_input_unit(1,frame,60);
    redwood_net_input_unit(2,frame,60); redwood_net_link_state_unit(9,true);
    assert(interfaces[0].received==1 && interfaces[1].received==1);
    redwood_net_input(frame,13);copy_error=1;redwood_net_input_unit(1,frame,60);
    assert(net_ifs[0].count.dropped_in==1 && net_ifs[1].count.dropped_in==1 && allocations==0);
    puts("net: en0/en1 isolation, legacy Wi-Fi name, invalid units, TX/RX ownership, attach failure and board admission passed");
}
