/*
 * win-tap-bridge — Unix socket listener
 */

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>
#include "socket.h"

int socket_listen(const char *path, mode_t mode)
{
    int fd;
    struct sockaddr_un addr;

    if (strlen(path) >= sizeof(addr.sun_path)) {
        fprintf(stderr, "socket path too long: %s\n", path);
        return -1;
    }

    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        perror("socket");
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    memcpy(addr.sun_path, path, strlen(path) + 1);

    /* Remove a stale socket, but never anything else that happens to be
     * sitting at this path (unlink on a symlink removes the link only, but
     * refuse regular files/dirs so a misconfiguration is visible). */
    struct stat st;
    if (lstat(path, &st) == 0) {
        if (!S_ISSOCK(st.st_mode)) {
            fprintf(stderr, "%s exists and is not a socket; refusing\n", path);
            close(fd);
            return -1;
        }
        unlink(path);
    }

    /* Bind with a restrictive umask, then set the intended mode explicitly,
     * so the result never depends on the umask we were started with. */
    mode_t old = umask(0177);
    int rc = bind(fd, (struct sockaddr *)&addr, sizeof(addr));
    umask(old);
    if (rc < 0) {
        perror("bind");
        close(fd);
        return -1;
    }
    if (chmod(path, mode) < 0) {
        perror("chmod socket");
        close(fd);
        unlink(path);
        return -1;
    }

    if (listen(fd, 4) < 0) {
        perror("listen");
        close(fd);
        unlink(path);
        return -1;
    }

    return fd;
}

int socket_accept(int listen_fd, uid_t *peer_uid)
{
    int client_fd;
    do {
        client_fd = accept4(listen_fd, NULL, NULL, SOCK_CLOEXEC);
    } while (client_fd < 0 && errno == EINTR);
    if (client_fd < 0) {
        perror("accept");
        return -1;
    }

    struct ucred cred;
    socklen_t len = sizeof(cred);
    if (getsockopt(client_fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) < 0 ||
        len != sizeof(cred)) {
        perror("SO_PEERCRED");
        close(client_fd);
        return -1;
    }
    *peer_uid = cred.uid;

    /* A client that stalls mid-frame must not freeze the event loop. */
    struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
    if (setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) < 0 ||
        setsockopt(client_fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) < 0) {
        perror("setsockopt timeout");
        close(client_fd);
        return -1;
    }

    return client_fd;
}
