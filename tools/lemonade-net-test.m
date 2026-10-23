/* RAM-only OnePlus 9 acceptance: Apple80211 join, configd DHCP, DNS and
 * three HTTP transfers bound to en0. Never print network identity or keys. */
#import <Foundation/Foundation.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <net/if.h>
#include <netdb.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <unistd.h>

extern mach_port_t bootstrap_port;
extern kern_return_t bootstrap_look_up(mach_port_t, const char *, mach_port_t *);

static double now(void) {
    static mach_timebase_info_data_t base;
    if (!base.denom) mach_timebase_info(&base);
    return (double)mach_absolute_time() * base.numer / base.denom / 1e9;
}
static bool enabled(void) {
    char arguments[2048]; size_t length = sizeof(arguments);
    if (sysctlbyname("kern.bootargs", arguments, &length, NULL, 0)) return false;
    arguments[sizeof(arguments)-1] = 0;
    for (char *word = strtok(arguments, " "); word; word = strtok(NULL, " "))
        if (!strcmp(word, "lemonade_wifi=24")) return true;
    return false;
}
struct apple80211req {
    char name[16]; int32_t type, value; uint32_t length;
    void *data __attribute__((aligned(8)));
};
static int association(void) {
    uint8_t secret[101] = {0}, assoc[592] = {0}; struct stat st;
    int fd = open("/redwood/fw/lemonade-network", O_RDONLY | O_NOFOLLOW);
    ssize_t length = -1; bool valid = false;
    if (fd >= 0 && !fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_uid == 0 &&
        (st.st_mode & 0777) == 0600 && st.st_size >= 15 && st.st_size <= 101) {
        length = read(fd, secret, sizeof(secret));
        valid = length == st.st_size && !memcmp(secret, "LNW1", 4) &&
            secret[4] > 0 && secret[4] <= 32 && secret[5] >= 8 && secret[5] <= 63 &&
            length == 6 + secret[4] + secret[5];
    }
    if (fd >= 0) close(fd);
    if (!valid) { memset_s(secret, sizeof(secret), 0, sizeof(secret)); return EINVAL; }
    uint32_t version = 1, size = secret[4], key_size = secret[5], cipher = 10;
    uint16_t one = 1, auth = 8;
    memcpy(assoc + 0x00, &version, 4); memcpy(assoc + 0x04, &one, 2);
    memcpy(assoc + 0x06, &one, 2); memcpy(assoc + 0x08, &auth, 2);
    memcpy(assoc + 0x0c, &size, 4); memcpy(assoc + 0x10, secret + 6, size);
    memcpy(assoc + 0x38, &version, 4); memcpy(assoc + 0x3c, &key_size, 4);
    memcpy(assoc + 0x40, &cipher, 4); memcpy(assoc + 0x48, secret + 6 + size, key_size);
    memset_s(secret, sizeof(secret), 0, sizeof(secret));
    int s = socket(AF_INET, SOCK_DGRAM, 0), error = 0;
    struct apple80211req request = { .name = "en0", .type = 20, .length = sizeof(assoc), .data = assoc };
    double start = now();
    /* en0 is attached before the station worker becomes available. */
    while (s >= 0 && ioctl(s, 0x802869c8u, &request) != 0) {
        error = errno;
        if ((error != ENXIO && error != EBUSY && error != EAGAIN) || now() - start > 45) break;
        usleep(250000); error = 0;
    }
    memset_s(assoc, sizeof(assoc), 0, sizeof(assoc));
    if (s < 0) return errno;
    if (error) { close(s); return error; }
    printf("lemonade-net: association requested through Apple80211\n");
    start = now();
    int result = 0;
    while (now() - start < 65) {
        request = (struct apple80211req){ .name = "en0", .type = 21 };
        if (ioctl(s, 0xc02869c9u, &request)) { result = -errno; break; }
        if ((result = request.value) != 0) break;
        usleep(200000);
    }
    close(s);
    printf("lemonade-net: association result %d after %.1f s\n", result, now() - start);
    return result == 1 ? 0 : result ? EACCES : ETIMEDOUT;
}

/* configd IPConfiguration's ipconfig_set_service, same ABI as net_test.m. */
#pragma pack(push, 4)
struct set_request {
    mach_msg_header_t head; mach_msg_body_t body; mach_msg_ool_descriptor_t method;
    NDR_record_t ndr; char name[16]; mach_msg_type_number_t method_length;
};
struct set_reply {
    mach_msg_header_t head; NDR_record_t ndr; kern_return_t result;
    char service[128]; int status; mach_msg_max_trailer_t trailer;
};
#pragma pack(pop)
static bool dhcp(void) {
    NSData *data = [NSPropertyListSerialization dataWithPropertyList:@{@"ConfigMethod": @"DHCP"}
        format:NSPropertyListBinaryFormat_v1_0 options:0 error:NULL];
    mach_port_t server = MACH_PORT_NULL, reply = mig_get_reply_port();
    union { struct set_request request; struct set_reply reply; } msg = {0};
    kern_return_t result = bootstrap_look_up(bootstrap_port, "com.apple.network.IPConfiguration", &server);
    if (result) { printf("lemonade-net: DHCP service unavailable %d\n", result); return false; }
    msg.request.head.msgh_bits = MACH_MSGH_BITS_COMPLEX | MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    msg.request.head.msgh_size = sizeof(msg.request); msg.request.head.msgh_remote_port = server;
    msg.request.head.msgh_local_port = reply; msg.request.head.msgh_id = 20011;
    msg.request.body.msgh_descriptor_count = 1;
    msg.request.method.address = (void *)data.bytes; msg.request.method.size = (mach_msg_size_t)data.length;
    msg.request.method.copy = MACH_MSG_VIRTUAL_COPY; msg.request.method.type = MACH_MSG_OOL_DESCRIPTOR;
    msg.request.ndr = NDR_record; strlcpy(msg.request.name, "en0", sizeof(msg.request.name));
    msg.request.method_length = (mach_msg_type_number_t)data.length;
    result = mach_msg(&msg.request.head, MACH_SEND_MSG | MACH_RCV_MSG | MACH_SEND_TIMEOUT | MACH_RCV_TIMEOUT,
        sizeof(msg.request), sizeof(msg), reply, 5000, MACH_PORT_NULL);
    mach_port_deallocate(mach_task_self(), server);
    bool ok = !result && msg.reply.head.msgh_id == 20111 &&
        msg.reply.head.msgh_size >= offsetof(struct set_reply, status) + sizeof(msg.reply.status) &&
        !msg.reply.result && !msg.reply.status;
    printf("lemonade-net: DHCP request accepted %u transport %d result %d status %d\n",
        ok, result, result ? -1 : msg.reply.result, result ? -1 : msg.reply.status);
    return ok;
}
static bool ipv4(void) {
    struct ifaddrs *all = NULL; bool found = false;
    if (getifaddrs(&all)) return false;
    for (struct ifaddrs *a = all; a; a = a->ifa_next) {
        if (!a->ifa_addr || strcmp(a->ifa_name, "en0") || a->ifa_addr->sa_family != AF_INET) continue;
        uint32_t ip = ntohl(((struct sockaddr_in *)a->ifa_addr)->sin_addr.s_addr);
        if ((a->ifa_flags & IFF_UP) && ip && (ip >> 24) != 127 && (ip >> 16) != 0xa9fe && (ip >> 28) < 14)
            found = true;
    }
    freeifaddrs(all); return found;
}
static bool http(unsigned attempt) {
    struct addrinfo hints = { .ai_family = AF_INET, .ai_socktype = SOCK_STREAM }, *list = NULL;
    int error = getaddrinfo("captive.apple.com", "80", &hints, &list);
    if (error) { printf("lemonade-net: DNS failed %d\n", error); return false; }
    printf("lemonade-net: DNS resolved IPv4\n");
    bool ok = false; size_t received = 0; int status = 0;
    for (struct addrinfo *a = list; a && !ok; a = a->ai_next) {
        int s = socket(AF_INET, SOCK_STREAM, 0); if (s < 0) continue;
        unsigned index = if_nametoindex("en0"); struct timeval timeout = { .tv_sec = 8 };
        if (!index || setsockopt(s, IPPROTO_IP, IP_BOUND_IF, &index, sizeof(index))) { close(s); break; }
        setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        int flags = fcntl(s, F_GETFL, 0); fcntl(s, F_SETFL, flags | O_NONBLOCK);
        int result = connect(s, a->ai_addr, a->ai_addrlen);
        if (result && errno == EINPROGRESS) {
            struct pollfd fd = { .fd = s, .events = POLLOUT }; socklen_t n = sizeof(error);
            result = poll(&fd, 1, 10000) > 0 && !getsockopt(s, SOL_SOCKET, SO_ERROR, &error, &n) && !error ? 0 : -1;
        }
        fcntl(s, F_SETFL, flags);
        if (result) { close(s); continue; }
        static const char request[] = "GET /hotspot-detect.html HTTP/1.0\r\nHost: captive.apple.com\r\nConnection: close\r\n\r\n";
        size_t sent = 0; while (sent < sizeof(request)-1) {
            ssize_t n = send(s, request + sent, sizeof(request)-1-sent, 0); if (n <= 0) break; sent += n;
        }
        char body[8192]; received = 0;
        while (sent == sizeof(request)-1 && received < sizeof(body)-1) {
            ssize_t n = recv(s, body + received, sizeof(body)-1-received, 0); if (n <= 0) break; received += n;
        }
        body[received] = 0; close(s);
        char *payload = strstr(body, "\r\n\r\n");
        ok = sscanf(body, "HTTP/%*u.%*u %d", &status) == 1 && status == 200 && payload && strstr(payload+4, "Success");
    }
    freeaddrinfo(list);
    printf("lemonade-net: HTTP attempt %u en0_bound 1 status %d bytes %zu expected_body %u\n", attempt, status, received, ok);
    return ok;
}
static int network_round(void) {
    double start = now(); while (!if_nametoindex("en0") && now()-start < 60) usleep(250000);
    if (!if_nametoindex("en0")) { printf("lemonade-net: no en0\n"); return 1; }
    int error = association();
    if (error) { printf("lemonade-net: association failed %d\n", error); return 2; }
    bool accepted = false;
    for (unsigned i = 0; i < 5 && !(accepted = dhcp()); i++) sleep(2);
    if (!accepted) return 3;
    start = now(); while (!ipv4() && now()-start < 65) usleep(250000);
    bool address = ipv4();
    printf("lemonade-net: en0 usable IPv4 %u after %.1f s\n", address, now()-start);
    if (!address) return 4;
    unsigned successes = 0;
    for (unsigned i = 1; i <= 3; i++) { successes += http(i); sleep(2); }
    printf("lemonade-net: acceptance association 1 dhcp 1 ipv4 1 http %u/3\n", successes);
    return successes == 3 ? 0 : 5;
}
static bool leave_network(void) {
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct apple80211req request = { .name = "en0", .type = 22 };
    if (s < 0) return false;
    if (ioctl(s, 0x802869c8u, &request)) { close(s); return false; }
    bool left = false; double start = now();
    while (now() - start < 15) {
        request = (struct apple80211req){ .name = "en0", .type = 13 };
        if (ioctl(s, 0xc02869c9u, &request)) break;
        if (request.value == 0) { left = true; break; }
        usleep(200000);
    }
    close(s);
    printf("lemonade-net: intentional disconnect reached idle %u\n", left);
    return left;
}
int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    @autoreleasepool {
        if (!enabled()) return 0;
        printf("lemonade-net: RAM-only acceptance started\n");
        int error = network_round();
        if (error) return error;
        sleep(5);
        if (!leave_network()) return 6;
        printf("lemonade-net: reconnect acceptance started\n");
        if ((error = network_round()) != 0) return error;
        printf("lemonade-net: reconnect complete association 1 dhcp 1 ipv4 1 http 3/3\n");
        return 0;
    }
}
