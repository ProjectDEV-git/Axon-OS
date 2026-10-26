/*
 * sys_netmp — Wine named pipe IPC
 */

#ifndef SYS_NETMP_PIPE_H
#define SYS_NETMP_PIPE_H

#include <windows.h>

/* Connect to a named pipe at the given path */
HANDLE pipe_connect(const char *pipe_path);

/* Send a length-prefixed frame over the pipe */
DWORD pipe_send_frame(HANDLE pipe, const void *data, DWORD len);

/* Receive a length-prefixed frame from the pipe */
DWORD pipe_recv_frame(HANDLE pipe, void *buf, DWORD buf_len, DWORD *out_len);

#endif /* SYS_NETMP_PIPE_H */
