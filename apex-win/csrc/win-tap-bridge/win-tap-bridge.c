/*
 * win-tap-bridge — Linux TAP device daemon for Wine networking
 *
 * Allocates a TAP device via /dev/net/tun, listens on an AF_UNIX socket,
 * and bridges Ethernet frames bidirectionally between the Wine process
 * (via the sys_netmp DLL) and the TAP device.
 *
 * Requires: CAP_NET_ADMIN
 *
 * Usage: win-tap-bridge [-f]
 *   -f  stay in the foreground (implied when started by systemd)
 *
 * Session model: one client at a time. The TAP carries every frame for the
 * attached session, so a new connection must never silently replace an
 * existing one: that let any local user who connected last receive all of
 * another user's traffic. A new client is accepted only when no session is
 * active, or when it belongs to the same uid as the current session (a
 * reconnect after a crash). Others are refused until the session ends.
 */

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include "bridge.h"
#include "socket.h"
#include "tap.h"

#define TAP_DEVICE_NAME "winrunner-tap0"
#define SOCKET_PATH     "/var/run/win-tap-bridge.sock"
#define MAX_EVENTS      4
#define EPOLL_TIMEOUT   1000 /* ms */

static volatile sig_atomic_t g_running = 1;

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

static int install_signals(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    /* No SA_RESTART: epoll_wait must return EINTR so the loop sees g_running. */
    if (sigaction(SIGINT, &sa, NULL) < 0 || sigaction(SIGTERM, &sa, NULL) < 0)
        return -1;
    /* A client that disappears mid-write must not kill the daemon. */
    signal(SIGPIPE, SIG_IGN);
    return 0;
}

static int daemonize(void)
{
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return -1;
    }
    if (pid > 0)
        _exit(0); /* Parent exits */
    if (setsid() < 0) {
        perror("setsid");
        return -1;
    }
    if (chdir("/") < 0)
        return -1;
    return 0;
}

static void drop_client(int epfd, int *client_fd)
{
    if (*client_fd >= 0) {
        epoll_ctl(epfd, EPOLL_CTL_DEL, *client_fd, NULL);
        close(*client_fd);
        *client_fd = -1;
    }
}

int main(int argc, char *argv[])
{
    int foreground = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-f") == 0) {
            foreground = 1;
        } else {
            fprintf(stderr, "usage: %s [-f]\n", argv[0]);
            return 2;
        }
    }
    /* Under systemd (Type=simple) forking makes the unit look dead and
     * systemd then kills the whole cgroup. */
    if (getenv("INVOCATION_ID") != NULL)
        foreground = 1;

    if (install_signals() < 0) {
        perror("sigaction");
        return 1;
    }

    if (!foreground && daemonize() < 0)
        return 1;

    /* Allocate TAP device */
    int tap_fd = tap_alloc(TAP_DEVICE_NAME);
    if (tap_fd < 0) {
        fprintf(stderr, "Failed to allocate TAP device\n");
        return 1;
    }

    /* Bring TAP interface up */
    if (tap_bring_up(TAP_DEVICE_NAME) < 0) {
        fprintf(stderr, "Failed to bring up TAP device\n");
        close(tap_fd);
        return 1;
    }

    /* Sandboxed programs run as ordinary users and must be able to connect
     * (connect(2) on a Unix socket needs write permission). Access is then
     * governed by the session policy above, using SO_PEERCRED. */
    int listen_fd = socket_listen(SOCKET_PATH, 0666);
    if (listen_fd < 0) {
        fprintf(stderr, "Failed to create socket listener\n");
        close(tap_fd);
        return 1;
    }

    int client_fd = -1;
    uid_t client_uid = (uid_t)-1;
    int rc = 1;

    int epfd = epoll_create1(EPOLL_CLOEXEC);
    if (epfd < 0) {
        perror("epoll_create1");
        goto cleanup;
    }

    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.events = EPOLLIN;
    ev.data.fd = tap_fd;
    if (epoll_ctl(epfd, EPOLL_CTL_ADD, tap_fd, &ev) < 0) {
        perror("epoll_ctl tap_fd");
        goto cleanup;
    }

    ev.events = EPOLLIN;
    ev.data.fd = listen_fd;
    if (epoll_ctl(epfd, EPOLL_CTL_ADD, listen_fd, &ev) < 0) {
        perror("epoll_ctl listen_fd");
        goto cleanup;
    }

    printf("win-tap-bridge: listening on %s, TAP=%s\n", SOCKET_PATH, TAP_DEVICE_NAME);
    fflush(stdout);

    struct epoll_event events[MAX_EVENTS];
    rc = 0;

    while (g_running) {
        int nfds = epoll_wait(epfd, events, MAX_EVENTS, EPOLL_TIMEOUT);
        if (nfds < 0) {
            if (errno == EINTR)
                continue;
            perror("epoll_wait");
            rc = 1;
            break;
        }

        for (int i = 0; i < nfds; i++) {
            int fd = events[i].data.fd;

            if (fd == listen_fd) {
                uid_t uid;
                int new_fd = socket_accept(listen_fd, &uid);
                if (new_fd < 0)
                    continue;

                if (client_fd >= 0 && uid != client_uid) {
                    fprintf(stderr,
                            "win-tap-bridge: refusing uid %u, session held by uid %u\n",
                            (unsigned)uid, (unsigned)client_uid);
                    close(new_fd);
                    continue;
                }

                /* Same user reconnecting: replace the stale session. */
                drop_client(epfd, &client_fd);

                client_fd = new_fd;
                client_uid = uid;
                ev.events = EPOLLIN | EPOLLRDHUP;
                ev.data.fd = client_fd;
                if (epoll_ctl(epfd, EPOLL_CTL_ADD, client_fd, &ev) < 0) {
                    perror("epoll_ctl client_fd");
                    close(client_fd);
                    client_fd = -1;
                }
            } else if (fd == tap_fd) {
                if (client_fd < 0) {
                    /* Level-triggered: unread TAP data would make epoll
                     * return immediately forever (100% CPU). */
                    bridge_drain_tap(tap_fd);
                    continue;
                }
                int r = bridge_tap_to_sock(tap_fd, client_fd);
                if (r == -1)
                    drop_client(epfd, &client_fd);
                else if (r == -2)
                    bridge_drain_tap(tap_fd);
            } else if (fd == client_fd) {
                if (events[i].events & (EPOLLERR | EPOLLHUP)) {
                    drop_client(epfd, &client_fd);
                    continue;
                }
                if (bridge_sock_to_tap(client_fd, tap_fd) < 0)
                    drop_client(epfd, &client_fd);
            }
        }
    }

cleanup:
    if (epfd >= 0)
        close(epfd);
    if (client_fd >= 0)
        close(client_fd);
    close(listen_fd);
    close(tap_fd);
    unlink(SOCKET_PATH);
    return rc;
}
