/*
 * win-tap-bridge — Unix socket listener
 */

#ifndef WIN_TAP_BRIDGE_SOCKET_H
#define WIN_TAP_BRIDGE_SOCKET_H

#include <sys/types.h>

/* Create and bind an AF_UNIX socket at the given path with exactly `mode`
 * permissions. Refuses to replace anything that is not a stale socket.
 * Returns the listening file descriptor, or -1 on error. */
int socket_listen(const char *path, mode_t mode);

/* Accept a new connection, report the peer's kernel-verified uid, and set
 * I/O timeouts on it. Returns the client fd, or -1 on error. */
int socket_accept(int listen_fd, uid_t *peer_uid);

#endif /* WIN_TAP_BRIDGE_SOCKET_H */
