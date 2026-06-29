#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

typedef uint32_t HRESULT;

typedef HRESULT (*PFN_CreateDXGIFactory)(void *riid, void **ppFactory);
typedef HRESULT (*PFN_CreateDXGIFactory1)(void *riid, void **ppFactory);
typedef HRESULT (*PFN_CreateDXGIFactory2)(uint32_t flags, void *riid, void **ppFactory);

static void *dxgi_lib = NULL;
static PFN_CreateDXGIFactory pfn_CreateDXGIFactory = NULL;
static PFN_CreateDXGIFactory1 pfn_CreateDXGIFactory1 = NULL;
static PFN_CreateDXGIFactory2 pfn_CreateDXGIFactory2 = NULL;
static int dxgi_initialized = 0;

#define S_OK 0
#define E_FAIL 0x80004005

static void dxgi_init(void) {
    if (dxgi_initialized) return;
    dxgi_initialized = 1;
    
    dxgi_lib = dlopen("dxgi.dll.so", RTLD_LAZY);
    if (!dxgi_lib) dxgi_lib = dlopen("/usr/lib/dxvk/dxgi.dll.so", RTLD_LAZY);
    if (!dxgi_lib) dxgi_lib = dlopen("/usr/lib/x86_64-linux-gnu/dxvk/dxgi.dll.so", RTLD_LAZY);
    
    if (dxgi_lib) {
        pfn_CreateDXGIFactory = dlsym(dxgi_lib, "CreateDXGIFactory");
        pfn_CreateDXGIFactory1 = dlsym(dxgi_lib, "CreateDXGIFactory1");
        pfn_CreateDXGIFactory2 = dlsym(dxgi_lib, "CreateDXGIFactory2");
    }
}

__attribute__((visibility("default")))
HRESULT CreateDXGIFactory(void *riid, void **ppFactory) {
    dxgi_init();
    if (pfn_CreateDXGIFactory) return pfn_CreateDXGIFactory(riid, ppFactory);
    return E_FAIL;
}

__attribute__((visibility("default")))
HRESULT CreateDXGIFactory1(void *riid, void **ppFactory) {
    dxgi_init();
    if (pfn_CreateDXGIFactory1) return pfn_CreateDXGIFactory1(riid, ppFactory);
    return E_FAIL;
}

__attribute__((visibility("default")))
HRESULT CreateDXGIFactory2(uint32_t flags, void *riid, void **ppFactory) {
    dxgi_init();
    if (pfn_CreateDXGIFactory2) return pfn_CreateDXGIFactory2(flags, riid, ppFactory);
    return E_FAIL;
}

__attribute__((visibility("default")))
int DllMain(void *hinstDLL, uint32_t fdwReason, void *lpvReserved) {
    if (fdwReason == 1) dxgi_init();
    if (fdwReason == 0 && dxgi_lib) { dlclose(dxgi_lib); dxgi_lib = NULL; }
    return 1;
}
