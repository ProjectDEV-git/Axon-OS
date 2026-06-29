/*
 * d3d9.dll wrapper for Axon Windows ABI
 * Loads system DXVK d3d9 shared library and forwards DirectX 9 calls
 */

#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

/* ── Type definitions ── */

typedef void *IDirect3D9;
typedef void *IDirect3DDevice9;
typedef void *IDirect3D9Ex;
typedef void *HMODULE;
typedef uint32_t HRESULT;

#define DLL_PROCESS_ATTACH 1
#define DLL_PROCESS_DETACH 0
#define D3DERR_NOTAVAILABLE ((HRESULT)0x8876086A)

/* ── DXVK library handle ── */

static void *dxvk_d3d9 = NULL;
static int d3d9_initialized = 0;

/* ── DXVK function pointers ── */

typedef IDirect3D9* (*PFN_Direct3DCreate9)(uint32_t SDKVersion);
typedef int (*PFN_Direct3DCreate9Ex)(uint32_t SDKVersion, IDirect3D9Ex **ppD3D);

static PFN_Direct3DCreate9 pfn_Direct3DCreate9 = NULL;
static PFN_Direct3DCreate9Ex pfn_Direct3DCreate9Ex = NULL;

/* ── Initialization ── */

static void d3d9_init(void)
{
    if (d3d9_initialized)
        return;
    d3d9_initialized = 1;

    /* Try common DXVK d3d9 library locations */
    dxvk_d3d9 = dlopen("d3d9-native.so", RTLD_LAZY);
    if (!dxvk_d3d9)
        dxvk_d3d9 = dlopen("/usr/lib/dxvk/d3d9.dll.so", RTLD_LAZY);
    if (!dxvk_d3d9)
        dxvk_d3d9 = dlopen("/usr/lib/x86_64-linux-gnu/dxvk/d3d9.dll.so", RTLD_LAZY);

    if (dxvk_d3d9) {
        pfn_Direct3DCreate9  = (PFN_Direct3DCreate9)dlsym(dxvk_d3d9, "Direct3DCreate9");
        pfn_Direct3DCreate9Ex = (PFN_Direct3DCreate9Ex)dlsym(dxvk_d3d9, "Direct3DCreate9Ex");
    }
}

/* ── Exported DirectX 9 entry points ── */

__attribute__((visibility("default")))
IDirect3D9* Direct3DCreate9(uint32_t SDKVersion)
{
    d3d9_init();
    if (pfn_Direct3DCreate9)
        return pfn_Direct3DCreate9(SDKVersion);
    return (IDirect3D9*)0;
}

__attribute__((visibility("default")))
int Direct3DCreate9Ex(uint32_t SDKVersion, IDirect3D9Ex **ppD3D)
{
    d3d9_init();
    if (pfn_Direct3DCreate9Ex)
        return pfn_Direct3DCreate9Ex(SDKVersion, ppD3D);
    return (int)D3DERR_NOTAVAILABLE;
}

/* ── DLL entry point ── */

__attribute__((visibility("default")))
int DllMain(void *hinstDLL, uint32_t fdwReason, void *lpvReserved)
{
    (void)hinstDLL;
    (void)lpvReserved;

    switch (fdwReason) {
    case DLL_PROCESS_ATTACH:
        d3d9_init();
        break;
    case DLL_PROCESS_DETACH:
        if (dxvk_d3d9) {
            dlclose(dxvk_d3d9);
            dxvk_d3d9 = NULL;
        }
        break;
    }
    return 1;
}
