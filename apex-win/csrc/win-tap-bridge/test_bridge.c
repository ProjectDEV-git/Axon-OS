/*
 * Adversarial tests for bridge.c (the code that parses bytes coming from a
 * sandboxed, untrusted Windows program). No TAP or root needed: a pipe
 * stands in for the TAP device, a socketpair for the client.
 *
 * Build/run: make test   (compiled with ASan/UBSan)
 */

#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include "bridge.h"

static void hdr(uint8_t out[4], uint32_t n)
{
    out[0] = (uint8_t)(n >> 24);
    out[1] = (uint8_t)(n >> 16);
    out[2] = (uint8_t)(n >> 8);
    out[3] = (uint8_t)n;
}

static void pair(int sv[2])
{
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    struct timeval tv = { .tv_sec = 0, .tv_usec = 200000 };
    setsockopt(sv[1], SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

/* Send `len` in the header followed by `body` bytes; return bridge result
 * and how many bytes reached the fake TAP. */
static int run_sock_to_tap(uint32_t len, const uint8_t *body, size_t body_n, ssize_t *tapped)
{
    int sv[2], tap[2];
    pair(sv);
    assert(pipe(tap) == 0);
    fcntl(tap[0], F_SETFL, O_NONBLOCK);
    uint8_t h[4];
    hdr(h, len);
    assert(write(sv[0], h, 4) == 4);
    if (body_n)
        assert(write(sv[0], body, body_n) == (ssize_t)body_n);
    shutdown(sv[0], SHUT_WR);
    int r = bridge_sock_to_tap(sv[1], tap[1]);
    uint8_t buf[4096];
    *tapped = read(tap[0], buf, sizeof(buf));
    close(sv[0]);
    close(sv[1]);
    close(tap[0]);
    close(tap[1]);
    return r;
}

int main(void)
{
    uint8_t frame[1600];
    memset(frame, 0xAB, sizeof(frame));
    ssize_t t;

    /* Valid frames at both bounds are forwarded intact. */
    assert(run_sock_to_tap(14, frame, 14, &t) == 0 && t == 14);
    assert(run_sock_to_tap(1518, frame, 1518, &t) == 0 && t == 1518);

    /* Oversize, huge, and zero/undersize lengths drop the client and never
     * touch the TAP (and never read past the stack buffer). */
    assert(run_sock_to_tap(1519, frame, 1518, &t) == -1 && t <= 0);
    assert(run_sock_to_tap(0xFFFFFFFFu, frame, 16, &t) == -1 && t <= 0);
    assert(run_sock_to_tap(0x80000000u, frame, 16, &t) == -1 && t <= 0);
    assert(run_sock_to_tap(0, NULL, 0, &t) == -1 && t <= 0);
    assert(run_sock_to_tap(13, frame, 13, &t) == -1 && t <= 0);

    /* Truncated body (client lies about length, then closes). */
    assert(run_sock_to_tap(100, frame, 50, &t) == -1 && t <= 0);

    /* Truncated header. */
    {
        int sv[2], tap[2];
        pair(sv);
        assert(pipe(tap) == 0);
        assert(write(sv[0], "\x00\x00", 2) == 2);
        shutdown(sv[0], SHUT_WR);
        assert(bridge_sock_to_tap(sv[1], tap[1]) == -1);
        close(sv[0]); close(sv[1]); close(tap[0]); close(tap[1]);
    }

    /* Stalled client (header then silence) times out instead of hanging. */
    {
        int sv[2], tap[2];
        pair(sv);
        assert(pipe(tap) == 0);
        uint8_t h[4];
        hdr(h, 60);
        assert(write(sv[0], h, 4) == 4);
        assert(bridge_sock_to_tap(sv[1], tap[1]) == -1);
        close(sv[0]); close(sv[1]); close(tap[0]); close(tap[1]);
    }

    /* Random garbage: never crashes, never forwards an out-of-range frame. */
    srand(12345);
    for (int i = 0; i < 2000; i++) {
        uint32_t len = (uint32_t)rand() ^ ((uint32_t)rand() << 16);
        if (i % 3 == 0)
            len %= 2000;
        size_t body = (size_t)(rand() % 1600);
        for (size_t k = 0; k < body; k++)
            frame[k] = (uint8_t)rand();
        int r = run_sock_to_tap(len, frame, body, &t);
        if (r == 0)
            assert(len >= 14 && len <= 1518 && t == (ssize_t)len);
        else
            assert(t <= 0);
    }

    /* TAP -> socket: frame is length-prefixed; a closed peer returns -1
     * (drop client) rather than killing the process with SIGPIPE. */
    {
        int sv[2], tap[2];
        pair(sv);
        assert(pipe(tap) == 0);
        assert(write(tap[1], frame, 60) == 60);
        assert(bridge_tap_to_sock(tap[0], sv[0]) == 0);
        uint8_t got[64];
        assert(read(sv[1], got, 64) == 64);
        assert(got[0] == 0 && got[1] == 0 && got[2] == 0 && got[3] == 60);
        assert(memcmp(got + 4, frame, 60) == 0);
        close(sv[1]);
        assert(write(tap[1], frame, 60) == 60);
        assert(bridge_tap_to_sock(tap[0], sv[0]) == -1);
        close(sv[0]); close(tap[0]); close(tap[1]);
    }

    /* Drain empties the fake TAP without blocking. */
    {
        int tap[2];
        assert(pipe(tap) == 0);
        fcntl(tap[0], F_SETFL, O_NONBLOCK);
        for (int i = 0; i < 5; i++)
            assert(write(tap[1], frame, 60) == 60);
        bridge_drain_tap(tap[0]);
        uint8_t b[8];
        assert(read(tap[0], b, sizeof(b)) < 0);
        close(tap[0]); close(tap[1]);
    }

    printf("bridge tests: all passed\n");
    return 0;
}
