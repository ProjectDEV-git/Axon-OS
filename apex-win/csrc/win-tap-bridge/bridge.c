/*
 * win-tap-bridge — Bidirectional frame bridge
 *
 * Frames are length-prefixed with a 4-byte big-endian header
 * on the Unix socket side (matching the sys_netmp DLL format).
 * The TAP side receives raw Ethernet frames.
 *
 * Every byte from the socket is attacker-controlled (it comes from a
 * sandboxed Windows program). Lengths are bounds-checked before use, short
 * reads/writes are retried, and the client socket has I/O timeouts so a
 * client that stalls mid-frame is disconnected instead of freezing the
 * single-threaded event loop for everyone.
 */

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>
#include "bridge.h"

/* Ethernet header (14) + 1500 MTU + 802.1Q tag (4). */
#define MAX_FRAME_SIZE 1518
/* Smallest meaningful Ethernet frame: the 14-byte header. */
#define MIN_FRAME_SIZE 14

/* Read exactly n bytes. Returns 0 on success, -1 on error/EOF/timeout. */
static int read_exact(int fd, void *buf, size_t n)
{
    size_t done = 0;
    while (done < n) {
        ssize_t r = read(fd, (char *)buf + done, n - done);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            return -1;
        done += (size_t)r;
    }
    return 0;
}

/* Write exactly n bytes to a socket without raising SIGPIPE. */
static int send_all(int fd, const void *buf, size_t n)
{
    size_t done = 0;
    while (done < n) {
        ssize_t w = send(fd, (const char *)buf + done, n - done, MSG_NOSIGNAL);
        if (w < 0 && errno == EINTR)
            continue;
        if (w <= 0)
            return -1;
        done += (size_t)w;
    }
    return 0;
}

int bridge_sock_to_tap(int sock_fd, int tap_fd)
{
    uint8_t header[4];
    if (read_exact(sock_fd, header, sizeof(header)) < 0)
        return -1;

    uint32_t len = ((uint32_t)header[0] << 24) |
                   ((uint32_t)header[1] << 16) |
                   ((uint32_t)header[2] << 8)  |
                   ((uint32_t)header[3]);

    if (len < MIN_FRAME_SIZE || len > MAX_FRAME_SIZE)
        return -1; /* protocol violation: drop the client */

    uint8_t frame[MAX_FRAME_SIZE];
    if (read_exact(sock_fd, frame, len) < 0)
        return -1;

    /* TAP writes are atomic per frame. EAGAIN (queue full) drops this
     * frame like a congested link would; it is not the client's fault. */
    ssize_t w;
    do {
        w = write(tap_fd, frame, len);
    } while (w < 0 && errno == EINTR);
    if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        return 0;
    if (w != (ssize_t)len)
        return -1;

    return 0;
}

int bridge_tap_to_sock(int tap_fd, int sock_fd)
{
    uint8_t frame[MAX_FRAME_SIZE];
    ssize_t n;
    do {
        n = read(tap_fd, frame, sizeof(frame));
    } while (n < 0 && errno == EINTR);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        return 0; /* spurious wakeup */
    if (n <= 0)
        return -2; /* TAP error: not the client's fault */

    uint8_t header[4];
    header[0] = (uint8_t)((n >> 24) & 0xFF);
    header[1] = (uint8_t)((n >> 16) & 0xFF);
    header[2] = (uint8_t)((n >> 8) & 0xFF);
    header[3] = (uint8_t)(n & 0xFF);

    if (send_all(sock_fd, header, sizeof(header)) < 0)
        return -1;
    if (send_all(sock_fd, frame, (size_t)n) < 0)
        return -1;

    return 0;
}

/* Drain whatever is queued on the TAP when no client is attached, so the
 * kernel queue does not fill and a later client does not receive stale
 * traffic that belonged to a previous session. */
void bridge_drain_tap(int tap_fd)
{
    uint8_t frame[MAX_FRAME_SIZE];
    for (int i = 0; i < 64; i++) {
        ssize_t n = read(tap_fd, frame, sizeof(frame));
        if (n <= 0)
            break;
    }
}
