/*
 * sys_netmp — Wine named pipe IPC
 *
 * Frames on the pipe are a 4-byte big-endian length followed by the frame.
 * The stream must stay in sync: every error path that has consumed part of
 * a frame either finishes consuming it or reports the pipe as broken. The
 * old code returned ERROR_INSUFFICIENT_BUFFER after reading only a header,
 * leaving the body in the pipe to be misread as the next length (and a
 * short ReadFile was reported as a complete frame).
 */

#include <windows.h>
#include "pipe.h"
#include "ioctl.h"

#define PIPE_CONNECT_RETRIES  50
#define PIPE_CONNECT_DELAY_MS 100
/* Must match the bridge's MAX_FRAME_SIZE. */
#define MAX_FRAME_SIZE        1518

HANDLE pipe_connect(const char *pipe_path)
{
    HANDLE h;

    /* Retry loop: wait for the daemon to create the pipe */
    for (int i = 0; i < PIPE_CONNECT_RETRIES; i++) {
        h = CreateFileA(
            pipe_path,
            GENERIC_READ | GENERIC_WRITE,
            0, NULL, OPEN_EXISTING, 0, NULL);

        if (h != INVALID_HANDLE_VALUE)
            return h;

        if (GetLastError() != ERROR_PIPE_BUSY)
            break;

        /* Wait for pipe to become available */
        if (!WaitNamedPipeA(pipe_path, PIPE_CONNECT_DELAY_MS))
            continue;
    }

    return INVALID_HANDLE_VALUE;
}

/* Write all `len` bytes, looping over partial writes. */
static DWORD write_all(HANDLE pipe, const void *data, DWORD len)
{
    const unsigned char *p = (const unsigned char *)data;
    while (len > 0) {
        DWORD written = 0;
        if (!WriteFile(pipe, p, len, &written, NULL))
            return GetLastError();
        if (written == 0)
            return ERROR_BROKEN_PIPE;
        p += written;
        len -= written;
    }
    return ERROR_SUCCESS;
}

/* Read exactly `len` bytes, looping over short reads. */
static DWORD read_all(HANDLE pipe, void *buf, DWORD len)
{
    unsigned char *p = (unsigned char *)buf;
    while (len > 0) {
        DWORD got = 0;
        if (!ReadFile(pipe, p, len, &got, NULL)) {
            DWORD err = GetLastError();
            return err ? err : ERROR_BROKEN_PIPE;
        }
        if (got == 0)
            return ERROR_BROKEN_PIPE;
        p += got;
        len -= got;
    }
    return ERROR_SUCCESS;
}

DWORD pipe_send_frame(HANDLE pipe, const void *data, DWORD len)
{
    /* The bridge drops the whole connection on an out-of-range length, so
     * reject it here with a clean error instead. */
    if (len == 0 || len > MAX_FRAME_SIZE)
        return ERROR_INVALID_PARAMETER;

    unsigned char header[FRAME_LEN_SIZE];
    header[0] = (len >> 24) & 0xFF;
    header[1] = (len >> 16) & 0xFF;
    header[2] = (len >> 8)  & 0xFF;
    header[3] = (len)       & 0xFF;

    DWORD err = write_all(pipe, header, FRAME_LEN_SIZE);
    if (err != ERROR_SUCCESS)
        return err;
    return write_all(pipe, data, len);
}

DWORD pipe_recv_frame(HANDLE pipe, void *buf, DWORD buf_len, DWORD *out_len)
{
    *out_len = 0;

    unsigned char header[FRAME_LEN_SIZE];
    DWORD err = read_all(pipe, header, FRAME_LEN_SIZE);
    if (err != ERROR_SUCCESS)
        return err;

    DWORD frame_len = ((DWORD)header[0] << 24) |
                      ((DWORD)header[1] << 16) |
                      ((DWORD)header[2] << 8)  |
                      ((DWORD)header[3]);

    /* A length the bridge can never produce means the stream is corrupt. */
    if (frame_len == 0 || frame_len > MAX_FRAME_SIZE)
        return ERROR_INVALID_DATA;

    if (frame_len > buf_len) {
        /* Consume and discard the body so the next read starts on a
         * header boundary, then tell the caller its buffer was too small. */
        unsigned char scratch[MAX_FRAME_SIZE];
        err = read_all(pipe, scratch, frame_len);
        if (err != ERROR_SUCCESS)
            return err;
        return ERROR_INSUFFICIENT_BUFFER;
    }

    err = read_all(pipe, buf, frame_len);
    if (err != ERROR_SUCCESS)
        return err;

    *out_len = frame_len;
    return ERROR_SUCCESS;
}
