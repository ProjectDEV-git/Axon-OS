/*
 * sys_netmp — NDIS IOCTL translation
 */

#ifndef SYS_NETMP_IOCTL_H
#define SYS_NETMP_IOCTL_H

#include <windows.h>

/* Custom IOCTL codes for TAP bridge communication */
#define IOCTL_NETMP_SEND_PACKET  0x00010001
#define IOCTL_NETMP_RECV_PACKET  0x00010002
#define IOCTL_NETMP_GET_STATUS   0x00010003

/* 4-byte big-endian length prefix for frames on the pipe */
#define FRAME_LEN_SIZE 4

/*
 * Dispatch an IOCTL to the TAP bridge via the pipe.
 * Returns ERROR_SUCCESS on success, Win32 error code on failure.
 */
DWORD ioctl_dispatch(HANDLE pipe, DWORD code, void *in_buf, DWORD in_len,
                     void *out_buf, DWORD out_len, DWORD *bytes_returned);

#endif /* SYS_NETMP_IOCTL_H */
