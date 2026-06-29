#include <stddef.h>
#include <stdint.h>

typedef uint32_t HRESULT;
#define S_OK 0
#define E_NOTIMPL 0x80004001

__attribute__((visibility("default")))
HRESULT DirectInput8Create(void *hinst, uint32_t dwVersion, void *riidltf, void **ppvOut, void *punkOuter) {
    if (ppvOut) *ppvOut = NULL;
    return E_NOTIMPL;
}
