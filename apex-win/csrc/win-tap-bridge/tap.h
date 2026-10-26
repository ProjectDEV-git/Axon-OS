/*
 * win-tap-bridge — TAP device allocation
 */

#ifndef WIN_TAP_BRIDGE_TAP_H
#define WIN_TAP_BRIDGE_TAP_H

/* Allocate a TAP device with the given name.
 * Returns the file descriptor (non-blocking), or -1 on error.
 * Requires CAP_NET_ADMIN. */
int tap_alloc(const char *dev_name);

/* Bring the TAP interface up (IFF_UP | IFF_RUNNING).
 * Returns 0 on success, -1 on error. */
int tap_bring_up(const char *dev_name);

#endif /* WIN_TAP_BRIDGE_TAP_H */
