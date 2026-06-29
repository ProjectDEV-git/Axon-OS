/* wasapi.c — WASAPI / Core Audio stubs for Axon Windows ABI (Phase 4) */
/* Future phase: wire to PipeWire for real audio I/O */

#include <stdint.h>
#include <string.h>
#include <stdlib.h>

typedef uint32_t HRESULT;
typedef void *IMMDeviceEnumerator;
typedef void *IMMDevice;
typedef void *IAudioClient;
typedef void *IAudioRenderClient;
typedef void *IAudioCaptureClient;

typedef uint32_t UINT;
typedef int BOOL;
typedef wchar_t WCHAR;
typedef const WCHAR *LPCWSTR;

typedef struct {
    uint32_t Data1;
    uint16_t Data2;
    uint16_t Data3;
    uint8_t  Data4[8];
} GUID;

typedef struct {
    uint32_t cbSize;
    GUID riid;
} PROPERTYKEY;

typedef struct {
    uint32_t nChannels;
    uint32_t nSamplesPerSec;
    uint32_t nAvgBytesPerSec;
    uint32_t nBlockAlign;
    uint32_t wBitsPerSample;
    uint32_t cbSize;
} WAVEFORMATEX;

typedef struct {
    WAVEFORMATEX Format;
    uint32_t dwChannelMask;
    GUID SubFormat;
} WAVEFORMATEXTENSIBLE;

#define S_OK              0
#define S_FALSE           1
#define E_NOTIMPL         0x80004001
#define E_NOINTERFACE     0x80004002
#define E_POINTER         0x80004003
#define AUDCLNT_E_NOT_INITIALIZED 0x88890001

#define COINIT_APARTMENTTHREADED  0x2
#define CLSCTX_ALL                0x17
#define eRender                   0
#define eConsole                  0

/* ------------------------------------------------------------------ */
/*  COM runtime stubs                                                  */
/* ------------------------------------------------------------------ */

__attribute__((visibility("default")))
HRESULT CoInitializeEx(void *pvReserved, uint32_t dwCoInit) {
    (void)pvReserved;
    (void)dwCoInit;
    return S_OK;
}

__attribute__((visibility("default")))
void CoUninitialize(void) {
    /* no-op */
}

__attribute__((visibility("default")))
HRESULT CoCreateInstance(void *rclsid, void *pUnkOuter,
                         uint32_t dwClsContext, void *riid, void **ppv) {
    (void)rclsid;
    (void)pUnkOuter;
    (void)dwClsContext;
    (void)riid;
    if (ppv) *ppv = NULL;
    return E_NOINTERFACE;
}

__attribute__((visibility("default")))
void CoTaskMemFree(void *pv) {
    if (pv) free(pv);
}

__attribute__((visibility("default")))
void *CoTaskMemAlloc(uint32_t cb) {
    return malloc(cb);
}

/* ------------------------------------------------------------------ */
/*  Core Audio API stubs                                               */
/* ------------------------------------------------------------------ */

__attribute__((visibility("default")))
HRESULT AudioSes_CreateSessionEnumerator(void **ppEnumerator) {
    if (ppEnumerator) *ppEnumerator = NULL;
    return E_NOTIMPL;
}

__attribute__((visibility("default")))
void PropVariantInit(void *pvar) {
    if (pvar) memset(pvar, 0, 24);
}

/* ------------------------------------------------------------------ */
/*  mmdevapi stubs                                                     */
/* ------------------------------------------------------------------ */

__attribute__((visibility("default")))
HRESULT MMDevCreate(void **ppEnumerator) {
    if (ppEnumerator) *ppEnumerator = NULL;
    return E_NOTIMPL;
}
