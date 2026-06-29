#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

typedef uint32_t HRESULT;
typedef void *ID3D11Device;
typedef void *ID3D11DeviceContext;
typedef void *IUnknown;

typedef HRESULT (*PFN_D3D11CreateDevice)(void *pAdapter, uint32_t DriverType, void *Software, uint32_t Flags, const uint32_t *pFeatureLevels, uint32_t FeatureLevels, uint32_t SDKVersion, ID3D11Device **ppDevice, uint32_t *pFeatureLevel, ID3D11DeviceContext **ppImmediateContext);

typedef HRESULT (*PFN_D3D11CreateDeviceAndSwapChain)(void *pAdapter, uint32_t DriverType, void *Software, uint32_t Flags, const uint32_t *pFeatureLevels, uint32_t FeatureLevels, uint32_t SDKVersion, void *pSwapChainDesc, void **ppSwapChain, ID3D11Device **ppDevice, uint32_t *pFeatureLevel, ID3D11DeviceContext **ppImmediateContext);

static void *dxvk_d3d11 = NULL;
static PFN_D3D11CreateDevice pfn_D3D11CreateDevice = NULL;
static PFN_D3D11CreateDeviceAndSwapChain pfn_D3D11CreateDeviceAndSwapChain = NULL;
static int d3d11_initialized = 0;

#define S_OK 0
#define E_FAIL 0x80004005

static void d3d11_init(void) {
    if (d3d11_initialized) return;
    d3d11_initialized = 1;
    
    dxvk_d3d11 = dlopen("d3d11.dll.so", RTLD_LAZY);
    if (!dxvk_d3d11) dxvk_d3d11 = dlopen("/usr/lib/dxvk/d3d11.dll.so", RTLD_LAZY);
    if (!dxvk_d3d11) dxvk_d3d11 = dlopen("/usr/lib/x86_64-linux-gnu/dxvk/d3d11.dll.so", RTLD_LAZY);
    
    if (dxvk_d3d11) {
        pfn_D3D11CreateDevice = dlsym(dxvk_d3d11, "D3D11CreateDevice");
        pfn_D3D11CreateDeviceAndSwapChain = dlsym(dxvk_d3d11, "D3D11CreateDeviceAndSwapChain");
    }
}

__attribute__((visibility("default")))
HRESULT D3D11CreateDevice(void *pAdapter, uint32_t DriverType, void *Software, uint32_t Flags, const uint32_t *pFeatureLevels, uint32_t FeatureLevels, uint32_t SDKVersion, ID3D11Device **ppDevice, uint32_t *pFeatureLevel, ID3D11DeviceContext **ppImmediateContext) {
    d3d11_init();
    if (pfn_D3D11CreateDevice) return pfn_D3D11CreateDevice(pAdapter, DriverType, Software, Flags, pFeatureLevels, FeatureLevels, SDKVersion, ppDevice, pFeatureLevel, ppImmediateContext);
    return E_FAIL;
}

__attribute__((visibility("default")))
HRESULT D3D11CreateDeviceAndSwapChain(void *pAdapter, uint32_t DriverType, void *Software, uint32_t Flags, const uint32_t *pFeatureLevels, uint32_t FeatureLevels, uint32_t SDKVersion, void *pSwapChainDesc, void **ppSwapChain, ID3D11Device **ppDevice, uint32_t *pFeatureLevel, ID3D11DeviceContext **ppImmediateContext) {
    d3d11_init();
    if (pfn_D3D11CreateDeviceAndSwapChain) return pfn_D3D11CreateDeviceAndSwapChain(pAdapter, DriverType, Software, Flags, pFeatureLevels, FeatureLevels, SDKVersion, pSwapChainDesc, ppSwapChain, ppDevice, pFeatureLevel, ppImmediateContext);
    return E_FAIL;
}

__attribute__((visibility("default")))
int DllMain(void *hinstDLL, uint32_t fdwReason, void *lpvReserved) {
    if (fdwReason == 1) d3d11_init();
    if (fdwReason == 0 && dxvk_d3d11) { dlclose(dxvk_d3d11); dxvk_d3d11 = NULL; }
    return 1;
}
