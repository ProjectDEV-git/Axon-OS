/*
 * sys_netmp — Wine DLL for TAP bridge networking
 *
 * This DLL translates Windows NDIS IOCTLs into IPC messages
 * over a Wine named pipe to the win-tap-bridge daemon running
 * on the Linux host.
 *
 * Cross-compiled with MinGW: x86_64-w64-mingw32-gcc
 */

#include <windows.h>
#include "ioctl.h"
#include "pipe.h"
#include "ndis.h"

static HANDLE volatile g_pipe = INVALID_HANDLE_VALUE;

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved)
{
    (void)hinstDLL;
    (void)lpvReserved;

    switch (fdwReason) {
    case DLL_PROCESS_ATTACH:
        /* Do NOT connect here. DllMain runs under the loader lock, where
         * blocking I/O (pipe_connect retries for up to ~5 s) can deadlock
         * the process or stall every other thread's DLL loading. Connect
         * lazily on the first IOCTL instead. */
        DisableThreadLibraryCalls(hinstDLL);
        break;

    case DLL_PROCESS_DETACH:
        if (g_pipe != INVALID_HANDLE_VALUE) {
            CloseHandle(g_pipe);
            g_pipe = INVALID_HANDLE_VALUE;
        }
        break;
    }

    return TRUE;
}

/*
 * Handle NDIS IOCTL requests from the Windows networking stack.
 * Translates to pipe IPC with the Linux TAP bridge daemon.
 */
DWORD WINAPI HandleIoctl(DWORD code, void *in_buf, DWORD in_len,
                         void *out_buf, DWORD out_len, DWORD *bytes_returned)
{
    if (g_pipe == INVALID_HANDLE_VALUE) {
        HANDLE h = pipe_connect("\\\\.\\pipe\\win_tap_bridge");
        if (h != INVALID_HANDLE_VALUE &&
            InterlockedCompareExchangePointer((PVOID *)&g_pipe, h,
                                              INVALID_HANDLE_VALUE) != INVALID_HANDLE_VALUE) {
            CloseHandle(h); /* another thread won the race */
        }
    }
    if (g_pipe == INVALID_HANDLE_VALUE) {
        return ERROR_NOT_CONNECTED;
    }

    return ioctl_dispatch(g_pipe, code, in_buf, in_len,
                          out_buf, out_len, bytes_returned);
}
