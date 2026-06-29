/*
 * d3d12.dll wrapper for Axon Windows ABI
 * Loads vkd3d-proton at runtime via dlopen/dlsym
 * Falls back gracefully if vkd3d-proton is not installed
 */

#include <dlfcn.h>
#include <stdint.h>

typedef void *ID3D12Device;
typedef void *ID3D12CommandQueue;

typedef int (*PFN_D3D12CreateDevice)(void *pAdapter, uint32_t MinimumFeatureLevel,
                                     void *riid, void **ppDevice);
typedef int (*PFN_D3D12GetDebugInterface)(void *riid, void **ppDebug);

static void *vkd3d_d3d12 = NULL;
static PFN_D3D12CreateDevice pfn_D3D12CreateDevice = NULL;
static PFN_D3D12GetDebugInterface pfn_D3D12GetDebugInterface = NULL;
static int d3d12_initialized = 0;

static void d3d12_init(void)
{
    if (d3d12_initialized) return;
    d3d12_initialized = 1;

    vkd3d_d3d12 = dlopen("d3d12.dll.so", RTLD_LAZY);
    if (!vkd3d_d3d12)
        vkd3d_d3d12 = dlopen("/usr/lib/vkd3d-proton/d3d12.dll.so", RTLD_LAZY);
    if (!vkd3d_d3d12)
        vkd3d_d3d12 = dlopen("/usr/lib/x86_64-linux-gnu/vkd3d-proton/d3d12.dll.so", RTLD_LAZY);

    if (vkd3d_d3d12) {
        pfn_D3D12CreateDevice = dlsym(vkd3d_d3d12, "D3D12CreateDevice");
        pfn_D3D12GetDebugInterface = dlsym(vkd3d_d3d12, "D3D12GetDebugInterface");
    }
}

__attribute__((visibility("default")))
int D3D12CreateDevice(void *pAdapter, uint32_t MinimumFeatureLevel,
                      void *riid, void **ppDevice)
{
    d3d12_init();
    if (pfn_D3D12CreateDevice)
        return pfn_D3D12CreateDevice(pAdapter, MinimumFeatureLevel, riid, ppDevice);
    return -1;
}

__attribute__((visibility("default")))
int D3D12GetDebugInterface(void *riid, void **ppDebug)
{
    d3d12_init();
    if (pfn_D3D12GetDebugInterface)
        return pfn_D3D12GetDebugInterface(riid, ppDebug);
    return -1;
}

__attribute__((visibility("default")))
int D3D12EnableExperimentalFeatures(uint32_t NumFeatures, void *pIIDs,
                                    void *pConfigurationStructs,
                                    uint32_t *pConfigurationStructSizes)
{
    (void)NumFeatures; (void)pIIDs;
    (void)pConfigurationStructs; (void)pConfigurationStructSizes;
    return -1;
}

__attribute__((visibility("default")))
int DllMain(void *hinstDLL, uint32_t fdwReason, void *lpvReserved)
{
    (void)hinstDLL; (void)lpvReserved;
    if (fdwReason == 1)
        d3d12_init();
    if (fdwReason == 0 && vkd3d_d3d12) {
        dlclose(vkd3d_d3d12);
        vkd3d_d3d12 = NULL;
    }
    return 1;
}
