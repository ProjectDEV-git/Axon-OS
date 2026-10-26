/*
 * win-tap-bridge — Bidirectional frame bridge
 */

#ifndef WIN_TAP_BRIDGE_BRIDGE_H
#define WIN_TAP_BRIDGE_BRIDGE_H

/* Bridge a single Ethernet frame from the socket to the TAP device.
 * Returns 0 on success, -1 if the client must be dropped (EOF, timeout,
 * or protocol violation such as an out-of-range length). */
int bridge_sock_to_tap(int sock_fd, int tap_fd);

/* Bridge a single Ethernet frame from the TAP device to the socket.
 * Returns 0 on success (or nothing to do), -1 if the client must be
 * dropped, -2 on a TAP-side error. */
int bridge_tap_to_sock(int tap_fd, int sock_fd);

/* Discard frames queued on the TAP while no client is attached. */
void bridge_drain_tap(int tap_fd);

#endif /* WIN_TAP_BRIDGE_BRIDGE_H */
