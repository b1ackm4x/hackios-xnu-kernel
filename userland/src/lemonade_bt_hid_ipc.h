/* Bounded local-only framing. A connection owns exactly one keyboard service;
 * EOF destroys it and releases held keys. No report is persisted or logged. */
#ifndef LEMONADE_BT_HID_IPC_H
#define LEMONADE_BT_HID_IPC_H
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/socket.h>
#include <unistd.h>
#define LBH_SOCKET "/var/run/lemonade-bt-hid.sock"
#define LBH_IPC_MAGIC 0x4c424831u
#define LBH_IPC_CREATE 1u
#define LBH_IPC_INPUT 2u
#define LBH_IPC_CLOSE 3u
#define LBH_IPC_PROPERTIES 16384u
struct lbh_message { uint32_t magic,version,operation,length; uint64_t timestamp; };
struct lbh_reply { int32_t status; uint32_t reports,events,failures; };
_Static_assert(sizeof(struct lbh_message)==24,"IPC header ABI");
_Static_assert(sizeof(struct lbh_reply)==16,"IPC response ABI");
static inline bool lbh_io(int fd,void *buffer,size_t length,bool sending) {
    uint8_t *p=buffer;
    while (length) {
        ssize_t n=sending?send(fd,p,length,0):recv(fd,p,length,0);
        if (n<0 && errno==EINTR) continue;
        if (n<=0) return false;
        p+=n;length-=(size_t)n;
    }
    return true;
}
static inline bool lbh_header_valid(const struct lbh_message *h) {
    return h->magic==LBH_IPC_MAGIC && h->version==1 &&
        ((h->operation==LBH_IPC_CREATE && h->length>0 && h->length<=LBH_IPC_PROPERTIES) ||
         (h->operation==LBH_IPC_INPUT && h->length>0 && h->length<=257) ||
         (h->operation==LBH_IPC_CLOSE && !h->length));
}
static inline bool lbh_send(int fd,uint32_t operation,uint64_t timestamp,const void *data,uint32_t length) {
    struct lbh_message h={LBH_IPC_MAGIC,1,operation,length,timestamp};
    return lbh_header_valid(&h) && lbh_io(fd,&h,sizeof(h),true) &&
        (!length || lbh_io(fd,(void *)data,length,true));
}
#endif
