# DLL Emulation Layer Bugs Report

**Agent**: Debug Agent 5 (Kernel Module)  
**Date**: 2026-07-02  
**Files Analyzed**: `dlls/kernel32.c`, `dlls/ntdll.c`, `dlls/user32.c`, `dlls/gdi32.c`, `dlls/advapi32.c`, `dlls/shell32.c`, `dlls/d3d9.c`, `dlls/d3d11.c`, `dlls/d3d12.c`, `dlls/dxgi.c`, `dlls/wasapi.c`, `dlls/xinput.c`, `dlls/dinput.c`, `nt-dll-loader.c`

---

## Severity Legend

| Level | Meaning |
|-------|---------|
| CRITICAL | Security vulnerability, data corruption, or will crash common apps |
| BUG | Incorrect behavior that affects real-world usage |
| WARNING | Design issue that limits compatibility or creates edge-case failures |
| INFO | Observation or minor improvement suggestion |

---

## 1. CRITICAL: Syscall Number Mismatch in advapi32.c

**Severity**: CRITICAL  
**File**: `dlls/advapi32.c`

The advapi32 DLL uses completely wrong NT syscall numbers:

```c
// advapi32.c (WRONG):
#define NR_NT_OPEN_KEY        0x0F4
#define NR_NT_QUERY_VALUE_KEY 0x0F7
#define NR_NT_CLOSE           0x00F

// syscall_table.c (CORRECT):
nt_open_key       -> 0x2C
nt_query_value_key -> 0x2D
nt_close          -> 0x09
```

**Impact**: All registry operations through advapi32.dll will hit wrong syscall handlers or return `NT_STATUS_NOT_IMPLEMENTED`. Any application that reads the Windows registry (most do) will fail silently or crash.

**Fix**: Update the `#define` constants in `advapi32.c` to match `syscall_table.c`.

---

## 2. CRITICAL: Thread Safety of DXVK/vkd3d Forwarding DLLs

**Severity**: CRITICAL  
**Files**: `dlls/d3d9.c`, `dlls/d3d11.c`, `dlls/d3d12.c`, `dlls/dxgi.c`

All graphics DLLs use a non-thread-safe initialization pattern:

```c
static void d3d9_init(void) {
    if (d3d9_initialized) return;    // RACE: TOCTOU
    d3d9_initialized = 1;            // RACE: no lock
    dxvk_d3d9 = dlopen(...);         // RACE: multiple threads can dlopen simultaneously
}
```

**Impact**: In a multi-threaded game:
1. Two threads call `Direct3DCreate9` simultaneously.
2. Both read `d3d9_initialized == 0`, both enter `d3d9_init`.
3. Both call `dlopen()`, potentially loading the library twice.
4. One thread gets a dangling pointer when the other calls `dlclose` on exit.

**Fix**: Use `pthread_mutex_once` equivalent, or add a `static bool` guard with `cmpxchg`, or use `_Atomic` with proper ordering.

---

## 3. CRITICAL: shell32.c Buffer Overflow

**Severity**: CRITICAL  
**File**: `dlls/shell32.c`

```c
BOOL SHGetSpecialFolderPathA(HWND hwnd, LPSTR pszPath, int csidl, BOOL fCreate)
{
    if (!pszPath) return FALSE;
    strcpy(pszPath, "/tmp");  // No bounds check on pszPath!
    return TRUE;
}
```

Windows documents that `pszPath` must be at least `MAX_PATH` (260) bytes, but the stub doesn't verify this. More importantly, `strcpy` has no size parameter. If the caller provides a buffer smaller than 5 bytes (theoretical), this is a stack/heap overflow.

**Impact**: Stack corruption if called with undersized buffer (unlikely in practice since apps allocate MAX_PATH buffers, but this is a defensive coding failure).

**Fix**: Use `strncpy(pszPath, "/tmp", MAX_PATH)` or at minimum `strscpy`.

---

## 4. CRITICAL: advapi32.c RegQueryValueExA Logic Bug

**Severity**: CRITICAL  
**File**: `dlls/advapi32.c`

```c
LONG RegQueryValueExA(HKEY hKey, LPCSTR lpValueName, LPDWORD lpReserved,
                      LPDWORD lpType, LPBYTE lpData, LPDWORD lpcbData)
{
    // ... nt_syscall ...
    if (lpData && lpcbData && *lpcbData == 0) {
        memset(lpData, 0, *lpcbData);  // memset with size=0 (no-op!)
        if (lpType) *lpType = 1;
    }
    return ERROR_SUCCESS;
}
```

The condition `*lpcbData == 0` followed by `memset(lpData, 0, *lpcbData)` is a no-op (memset 0 bytes). The intent was probably to handle the "buffer too small" case by returning the required size, but instead it does nothing useful.

**Impact**: Applications that query registry value sizes before allocating buffers get incorrect information. They may allocate 0-byte buffers.

**Fix**: When `lpData == NULL` or buffer is too small, set `*lpcbData` to the required size and return `ERROR_MORE_DATA` (234).

---

## 5. CRITICAL: advapi32.c Ignores Syscall Return Values

**Severity**: CRITICAL  
**File**: `dlls/advapi32.c`

```c
LONG RegOpenKeyExA(HKEY hKey, LPCSTR lpSubKey, DWORD ulOptions,
                   DWORD samDesired, PHKEY phkResult)
{
    // ...
    nt_syscall(NR_NT_OPEN_KEY, args);   // Return value ignored!
    if (*phkResult == NULL)
        *phkResult = (HKEY)(unsigned long long)0x10001;  // Fake handle
    return ERROR_SUCCESS;               // Always returns success
}
```

The NT syscall return status is completely discarded. Even if the key doesn't exist, the function returns `ERROR_SUCCESS` and a fake handle.

**Impact**: Applications cannot detect missing registry keys. This masks errors and leads to subtle data corruption when apps write to non-existent keys and expect the values to persist.

**Fix**: Check syscall return and translate NTSTATUS to Win32 error codes (`RtlNtStatusToDosError`).

---

## 6. BUG: user32.c GetMessageA Always Returns FALSE

**Severity**: BUG  
**File**: `dlls/user32.c`

```c
BOOL GetMessageA(LPMSG lpMsg, HWND hWnd, UINT wMsgFilterMin, UINT wMsgFilterMax)
{
    (void)lpMsg; (void)hWnd; (void)wMsgFilterMin; (void)wMsgFilterMax;
    return FALSE;  // WM_QUIT equivalent
}
```

On Windows, `GetMessageA` returns:
- Non-zero for non-WM_QUIT messages
- 0 for WM_QUIT
- -1 for error

By returning `FALSE` (0) immediately, every Windows GUI app's message loop exits on the first iteration:

```c
while (GetMessageA(&msg, NULL, 0, 0)) {
    TranslateMessage(&msg);
    DispatchMessageA(&msg);
}
```

**Impact**: All GUI applications exit immediately. Only console apps survive. This is expected for Phase 3 stubs but should be clearly documented.

**Fix/Workaround**: For Phase 3, this is acceptable stub behavior. For future phases, wire to a GTK4/Wayland event loop.

---

## 7. BUG: ntdll.c Syscall Thunks Missing Register Preservation

**Severity**: BUG  
**File**: `dlls/ntdll.c`

```c
#define DEFINE_NT_SYSCALL(ret, name, nr)                         \
    NT_SYSCALL ret NTAPI name(void) {                            \
        __asm__ volatile (                                       \
            "mov %%rcx, %%r10\n"                                 \
            "mov $" #nr ", %%eax\n"                              \
            "syscall\n"                                          \
            "ret\n"                                              \
        );                                                       \
    }
```

The Linux x64 ABI clobbers `rcx` and `r11` during `syscall`. While the function returns `rax` correctly, the Windows x64 ABI expects callee-saved registers `rbx`, `rbp`, `rdi`, `rsi`, `r12-r15` to be preserved. Since `naked` means no compiler-generated prologue/epilogue, the compiler might rely on these functions to preserve these registers — and the inline assembly doesn't explicitly save/restore them.

**In practice**: The compiler likely doesn't use callee-saved registers in a `naked` function (no compiler-generated code), so this is probably safe. But it's fragile — any change to the function body could introduce register corruption.

**Impact**: Low risk currently; fragile for maintenance.

---

## 8. BUG: DLL Loading Does Not Handle Partial Failure Cleanup

**Severity**: BUG  
**File**: `nt-dll-loader.c`

In `preload_builtins()`:
```c
for (i = 0; builtins[i]; i++) {
    for (j = 0; j < search_path_count; j++) {
        // ...
        f = filp_open(fullpath, O_RDONLY, 0);
        if (IS_ERR(f)) continue;
        fput(f);  // File is opened then immediately closed — why?
        if (__axon_load_dll(builtins[i], &dll) == 0)
            pr_info("pre-loaded built-in: %s\n", builtins[i]);
        break;
    }
}
```

The file is opened with `filp_open` just to check existence, then closed with `fput`, then `__axon_load_dll` opens it again internally. This is a double-open/double-close race. Between `fput` and `__axon_load_dll`, another process could modify or delete the file.

**Impact**: TOCTOU (time-of-check-time-of-use) race condition. Low risk in practice for system DLLs.

---

## 9. BUG: __apply_relocs No Bounds Check on Patch Target

**Severity**: BUG  
**File**: `nt-dll-loader.c`

```c
void *patch = base + page_rva + rva_off;
// ...
if (type == PE_REL_DIR64 && is_64bit)
    *(u64 *)patch += (u64)delta;
```

The `patch` pointer is computed from PE relocation data (which comes from the DLL file). If a malformed DLL has `page_rva + rva_off` pointing outside the loaded image, this writes to arbitrary kernel memory.

**Impact**: Kernel memory corruption from malformed DLLs. This is exploitable if an attacker can supply a malicious DLL.

**Fix**: Validate that `page_rva + rva_off + sizeof(target)` falls within `[base, base + size_of_image)` before writing.

---

## 10. WARNING: Missing Critical Windows APIs

**Severity**: WARNING  
**Files**: Multiple DLL stubs

Commonly used Windows APIs not implemented:

| API | Required By | Impact |
|-----|------------|--------|
| `GetLastError` / `SetLastError` | Nearly every Win32 app | Error information lost |
| `HeapAlloc` / `HeapFree` | Memory-managed apps | Crash if no CRT |
| `VirtualQuery` | Anti-cheat, memory managers | Returns garbage |
| `LoadLibraryA` / `GetProcAddress` | Dynamic loading | Crash |
| `GetCurrentThreadId` | Thread pool code | Returns wrong value |
| `InitializeCriticalSection` | Multi-threaded apps | Deadlock |
| `InterlockedCompareExchange` | Lock-free code | Data race |
| `FormatMessageA` | Error display | Returns empty string |
| `GetModuleFileNameA` | Path resolution | Returns empty string |
| `CreateFileMappingA` | Shared memory | Returns NULL |

**Impact**: Any application beyond simple console programs will hit missing APIs. The module should at minimum have stubs that log the call and return plausible defaults.

---

## 11. WARNING: DXGI/D3D Forwarding Libraries Use dlopen From Kernel Context

**Severity**: WARNING  
**Files**: `dlls/d3d9.c`, `dlls/d3d11.c`, `dlls/d3d12.c`, `dlls/dxgi.c`

These DLLs use `dlopen()`/`dlsym()` which are user-space functions. This means these `.c` files are compiled as user-space shared libraries (not kernel modules), which is architecturally correct. However:

1. If DXVK/vkd3d-proton is not installed, the functions silently return error codes with no logging.
2. The search paths are hardcoded (`/usr/lib/dxvk/...`) and not configurable.
3. No fallback path for distros that install DXVK to different locations (e.g., `/usr/lib64/`, Flatpak paths).

**Fix**: Add `pr_err`/`fprintf(stderr)` logging when libraries are not found. Consider reading a config file for custom paths.

---

## 12. WARNING: WASAPI CoTaskMemFree Uses Mismatched Allocator

**Severity**: WARNING  
**File**: `dlls/wasapi.c`

```c
void CoTaskMemFree(void *pv) { if (pv) free(pv); }
void *CoTaskMemAlloc(uint32_t cb) { return malloc(cb); }
```

If the caller received a pointer from the real Windows COM allocator (via forwarding to a native library) and passes it to `CoTaskMemFree`, it would be freed with `free()` instead of the original allocator. This is heap corruption.

**Impact**: Crashes if COM objects are shared between the stub and a forwarded library.

---

## Summary Table

| ID | Severity | File | Description |
|----|----------|------|-------------|
| DLL-1 | CRITICAL | advapi32.c | Wrong syscall numbers (0x0F4/0x0F7/0x00F vs 0x2C/0x2D/0x09) |
| DLL-2 | CRITICAL | d3d9/d3d11/d3d12/dxgi | Thread-unsafe DXVK initialization |
| DLL-3 | CRITICAL | shell32.c | `strcpy` buffer overflow in `SHGetSpecialFolderPathA` |
| DLL-4 | CRITICAL | advapi32.c | `RegQueryValueExA` memset no-op logic bug |
| DLL-5 | CRITICAL | advapi32.c | Syscall return values ignored, always returns success |
| DLL-6 | BUG | user32.c | `GetMessageA` returns FALSE immediately |
| DLL-7 | BUG | ntdll.c | Naked syscall thunks rely on fragile register assumptions |
| DLL-8 | BUG | nt-dll-loader.c | TOCTOU race in DLL existence check |
| DLL-9 | BUG | nt-dll-loader.c | Relocation patching has no bounds check on target address |
| DLL-10 | WARNING | multiple | Missing critical APIs (GetLastError, HeapAlloc, LoadLibrary, etc.) |
| DLL-11 | WARNING | d3d/dxgi DLLs | No logging when DXVK/vkd3d-proton not found |
| DLL-12 | WARNING | wasapi.c | CoTaskMemFree/Alloc mismatched allocator |
