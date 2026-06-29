// kernel32.c — Win32 kernel32.dll stub for the Axon Windows ABI.
//
// User-space shared library that wraps NT syscalls from ntdll.dll into
// the familiar Win32 API that every Windows program expects.

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

// ── Windows Types ──────────────────────────────────────────────────────────

typedef unsigned int       UINT;
typedef unsigned long      DWORD;
typedef int                BOOL;
typedef unsigned char      BYTE;
typedef unsigned short     WORD;
typedef unsigned short     WCHAR;
typedef long               LONG;
typedef unsigned long long ULONG_PTR;
typedef ULONG_PTR          SIZE_T;
typedef long long          LONGLONG;
typedef unsigned long long ULONGLONG;

typedef const char        *LPCSTR;
typedef const wchar_t     *LPCWSTR;
typedef char              *LPSTR;
typedef wchar_t           *LPWSTR;
typedef void              *LPVOID;
typedef const void        *LPCVOID;
typedef DWORD             *LPDWORD;
typedef LONG              *LPLONG;
typedef BOOL              *LPBOOL;

typedef void              *HANDLE;
typedef void              *HMODULE;
typedef void              *HINSTANCE;
typedef long long          HRESULT;
typedef ULONG_PTR          DWORD_PTR;

typedef union _LARGE_INTEGER {
    struct {
        DWORD LowPart;
        LONG  HighPart;
    };
    struct {
        DWORD LowPart;
        LONG  HighPart;
    } u;
    LONGLONG QuadPart;
} LARGE_INTEGER;

typedef struct _FILETIME {
    DWORD dwLowDateTime;
    DWORD dwHighDateTime;
} FILETIME, *LPFILETIME;

typedef struct _OVERLAPPED {
    ULONG_PTR Internal;
    ULONG_PTR InternalHigh;
    union {
        struct {
            DWORD Offset;
            DWORD OffsetHigh;
        };
        LPVOID Pointer;
    };
    HANDLE hEvent;
} OVERLAPPED, *LPOVERLAPPED;

typedef struct _SECURITY_ATTRIBUTES {
    DWORD  nLength;
    LPVOID lpSecurityDescriptor;
    BOOL   bInheritHandle;
} SECURITY_ATTRIBUTES, *LPSECURITY_ATTRIBUTES;

typedef void *FARPROC;

#define TRUE  1
#define FALSE 0

#define INVALID_HANDLE_VALUE   ((HANDLE)(long long)-1)
#define STD_INPUT_HANDLE       ((DWORD)-10)
#define STD_OUTPUT_HANDLE      ((DWORD)-11)
#define STD_ERROR_HANDLE       ((DWORD)-12)

// NT status codes
#define NT_STATUS_SUCCESS      0x00000000

// Memory allocation types
#define MEM_COMMIT      0x1000
#define MEM_RESERVE     0x2000
#define MEM_RELEASE     0x8000

// Memory protection
#define PAGE_READWRITE   0x04
#define PAGE_EXECUTE_READWRITE 0x40

// File creation dispositions
#define CREATE_NEW        1
#define CREATE_ALWAYS     2
#define OPEN_EXISTING     3
#define OPEN_ALWAYS       4
#define TRUNCATE_EXISTING 5

// File access rights
#define GENERIC_READ     0x80000000
#define GENERIC_WRITE    0x40000000

// File share mode
#define FILE_SHARE_READ   0x00000001
#define FILE_SHARE_WRITE  0x00000002

// File attributes
#define FILE_ATTRIBUTE_NORMAL 0x80

// Code pages
#define CP_ACP  0
#define CP_UTF8 65001

// ── NT Syscall Interface ──────────────────────────────────────────────────
//
// The Axon ABI dispatches NT syscalls via syscall instruction with:
//   rax = syscall number
//   rdi = pointer to __u64 args array (up to 12 args)
//
// Syscall numbers from kernel/axon-winabi/syscall_table.c:

#define NR_NT_TERMINATE_PROCESS       0x01
#define NR_NT_ALLOCATE_VIRTUAL_MEMORY 0x03
#define NR_NT_FREE_VIRTUAL_MEMORY     0x05
#define NR_NT_WRITE_FILE              0x06
#define NR_NT_READ_FILE               0x07
#define NR_NT_CREATE_FILE             0x08
#define NR_NT_CLOSE                   0x09
#define NR_NT_QUERY_INFORMATION_FILE  0x10
#define NR_NT_GET_CURRENT_PROCESS_ID  0x100
#define NR_NT_GET_CURRENT_THREAD_ID   0x101
#define NR_NT_GET_TICK_COUNT          0x102
#define NR_NT_QUERY_SYSTEM_TIME       0x103
#define NR_NT_DELAY_EXECUTION         0x104

// Raw NT syscall — invokes the Axon kernel dispatcher.
// Returns NT status code (0 = success).
static inline uint32_t nt_syscall(uint32_t nr, const uint64_t *args)
{
    uint32_t status;
    register uint64_t r10 __asm__("r10") = (uint64_t)args;
    __asm__ volatile(
        "syscall"
        : "=a"(status)
        : "a"((uint64_t)nr), "r"(r10)
        : "rcx", "r11", "memory");
    return status;
}

// ── Process / Thread ───────────────────────────────────────────────────────

HANDLE GetStdHandle(DWORD nStdHandle)
{
    if (nStdHandle == STD_INPUT_HANDLE)
        return (HANDLE)0;
    if (nStdHandle == STD_OUTPUT_HANDLE)
        return (HANDLE)1;
    if (nStdHandle == STD_ERROR_HANDLE)
        return (HANDLE)2;
    return INVALID_HANDLE_VALUE;
}

BOOL WriteFile(HANDLE hFile, LPCVOID lpBuffer, DWORD nBytesToWrite,
               LPDWORD lpBytesWritten, LPOVERLAPPED lpOverlapped)
{
    uint64_t args[9] = {0};
    uint32_t status;

    args[0] = (uint64_t)(uintptr_t)hFile;       // FileHandle
    args[5] = (uint64_t)(uintptr_t)lpBuffer;    // Buffer
    args[6] = (uint64_t)nBytesToWrite;           // Length

    status = nt_syscall(NR_NT_WRITE_FILE, args);

    if (lpBytesWritten)
        *lpBytesWritten = (status == NT_STATUS_SUCCESS) ? nBytesToWrite : 0;

    return (status == NT_STATUS_SUCCESS) ? TRUE : FALSE;
}

BOOL ReadFile(HANDLE hFile, LPVOID lpBuffer, DWORD nBytesToRead,
              LPDWORD lpBytesRead, LPOVERLAPPED lpOverlapped)
{
    uint64_t args[9] = {0};
    uint32_t status;

    args[0] = (uint64_t)(uintptr_t)hFile;
    args[5] = (uint64_t)(uintptr_t)lpBuffer;
    args[6] = (uint64_t)nBytesToRead;

    status = nt_syscall(NR_NT_READ_FILE, args);

    if (lpBytesRead)
        *lpBytesRead = (status == NT_STATUS_SUCCESS) ? nBytesToRead : 0;

    return (status == NT_STATUS_SUCCESS) ? TRUE : FALSE;
}

DWORD GetCurrentProcessId(void)
{
    uint64_t args[1] = {0};
    return (DWORD)nt_syscall(NR_NT_GET_CURRENT_PROCESS_ID, args);
}

DWORD GetCurrentThreadId(void)
{
    uint64_t args[1] = {0};
    return (DWORD)nt_syscall(NR_NT_GET_CURRENT_THREAD_ID, args);
}

HANDLE GetCurrentProcess(void)
{
    return (HANDLE)(long long)-1;
}

HANDLE GetCurrentThread(void)
{
    return (HANDLE)(long long)-2;
}

void ExitProcess(UINT uExitCode)
{
    uint64_t args[2] = {0};
    args[1] = (uint64_t)uExitCode;
    nt_syscall(NR_NT_TERMINATE_PROCESS, args);
    __builtin_unreachable();
}

void Sleep(DWORD dwMilliseconds)
{
    uint64_t args[2] = {0};
    // Negative value = relative delay in 100ns units
    int64_t interval = -(int64_t)dwMilliseconds * 10000;
    args[1] = (uint64_t)(uintptr_t)&interval;
    nt_syscall(NR_NT_DELAY_EXECUTION, args);
}

DWORD GetTickCount(void)
{
    uint64_t args[1] = {0};
    return (DWORD)nt_syscall(NR_NT_GET_TICK_COUNT, args);
}

BOOL QueryPerformanceCounter(LARGE_INTEGER *lpPerformanceCount)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    // Convert to 100ns units (same scale as NT)
    lpPerformanceCount->QuadPart = (int64_t)ts.tv_sec * 10000000LL +
                                   (int64_t)ts.tv_nsec / 100LL;
    return TRUE;
}

BOOL QueryPerformanceFrequency(LARGE_INTEGER *lpFrequency)
{
    // 100ns resolution — same as NT
    lpFrequency->QuadPart = 10000000LL;
    return TRUE;
}

// ── Memory ─────────────────────────────────────────────────────────────────

LPVOID VirtualAlloc(LPVOID lpAddress, SIZE_T dwSize, DWORD flAllocationType,
                    DWORD flProtect)
{
    uint64_t args[6] = {0};
    uintptr_t base = (uintptr_t)lpAddress;
    SIZE_T size = dwSize;

    args[0] = (uint64_t)(uintptr_t)-1;          // ProcessHandle = current
    args[1] = (uint64_t)(uintptr_t)&base;
    args[3] = (uint64_t)(uintptr_t)&size;
    args[4] = (uint64_t)flAllocationType;
    args[5] = (uint64_t)flProtect;

    if (nt_syscall(NR_NT_ALLOCATE_VIRTUAL_MEMORY, args) == NT_STATUS_SUCCESS)
        return (LPVOID)(uintptr_t)base;
    return NULL;
}

BOOL VirtualFree(LPVOID lpAddress, SIZE_T dwSize, DWORD dwFreeType)
{
    uint64_t args[4] = {0};
    uintptr_t base = (uintptr_t)lpAddress;

    args[0] = (uint64_t)(uintptr_t)-1;
    args[1] = (uint64_t)(uintptr_t)&base;
    args[3] = (uint64_t)dwFreeType;

    return (nt_syscall(NR_NT_FREE_VIRTUAL_MEMORY, args) == NT_STATUS_SUCCESS)
           ? TRUE : FALSE;
}

LPVOID HeapAlloc(HANDLE hHeap, DWORD dwFlags, SIZE_T dwBytes)
{
    return malloc((size_t)dwBytes);
}

BOOL HeapFree(HANDLE hHeap, DWORD dwFlags, LPVOID lpMem)
{
    free(lpMem);
    return TRUE;
}

HANDLE GetProcessHeap(void)
{
    return (HANDLE)1;
}

SIZE_T HeapSize(HANDLE hHeap, DWORD dwFlags, LPCVOID lpMem)
{
    // Stub — no portable way to query malloc size
    return 0;
}

// ── File I/O ───────────────────────────────────────────────────────────────

HANDLE CreateFileA(LPCSTR lpFileName, DWORD dwDesiredAccess,
                   DWORD dwShareMode, LPSECURITY_ATTRIBUTES lpSecurityAttributes,
                   DWORD dwCreationDisposition, DWORD dwFlagsAndAttributes,
                   HANDLE hTemplateFile)
{
    // Build NT-style OBJECT_ATTRIBUTES + UNICODE_STRING on the stack.
    // The kernel's NtCreateFile reads:
    //   obj_attr + 0x10 -> UNICODE_STRING pointer
    //   UNICODE_STRING + 0x00 = Length (bytes)
    //   UNICODE_STRING + 0x08 = Buffer (wchar_t*)
    uint64_t args[11] = {0};
    uint64_t handle_out = 0;
    uint16_t name_buf[256];
    uint16_t name_len = 0;
    struct {
        uint32_t length;
        uint32_t pad;
        uint64_t root_dir;
        uint64_t object_name_ptr;
        uint32_t attributes;
        uint32_t pad2;
    } obj_attr;
    struct {
        uint16_t length;
        uint16_t max_length;
        uint32_t pad;
        uint64_t buffer_ptr;
    } unicode_str;
    int i;

    if (!lpFileName)
        return INVALID_HANDLE_VALUE;

    // Convert ASCII to wide string
    for (i = 0; lpFileName[i] && i < 255; i++)
        name_buf[i] = (uint16_t)(unsigned char)lpFileName[i];
    name_buf[i] = 0;
    name_len = (uint16_t)(i * 2);

    unicode_str.length = name_len;
    unicode_str.max_length = name_len + 2;
    unicode_str.pad = 0;
    unicode_str.buffer_ptr = (uint64_t)(uintptr_t)name_buf;

    obj_attr.length = sizeof(obj_attr);
    obj_attr.pad = 0;
    obj_attr.root_dir = 0;
    obj_attr.object_name_ptr = (uint64_t)(uintptr_t)&unicode_str;
    obj_attr.attributes = 0x40; // OBJ_CASE_INSENSITIVE
    obj_attr.pad2 = 0;

    args[0] = (uint64_t)(uintptr_t)&handle_out;  // HandleOut
    args[1] = (uint64_t)dwDesiredAccess;          // DesiredAccess
    args[2] = (uint64_t)(uintptr_t)&obj_attr;     // ObjectAttributes
    args[7] = (uint64_t)dwCreationDisposition;    // CreateDisposition

    if (nt_syscall(NR_NT_CREATE_FILE, args) != NT_STATUS_SUCCESS)
        return INVALID_HANDLE_VALUE;

    return (HANDLE)(uintptr_t)handle_out;
}

HANDLE CreateFileW(LPCWSTR lpFileName, DWORD dwDesiredAccess,
                   DWORD dwShareMode, LPSECURITY_ATTRIBUTES lpSecurityAttributes,
                   DWORD dwCreationDisposition, DWORD dwFlagsAndAttributes,
                   HANDLE hTemplateFile)
{
    char ansi[512];
    int i;

    if (!lpFileName)
        return INVALID_HANDLE_VALUE;

    // Convert wide string to ASCII
    for (i = 0; lpFileName[i] && i < 511; i++)
        ansi[i] = (char)(lpFileName[i] & 0x7F);
    ansi[i] = '\0';

    return CreateFileA(ansi, dwDesiredAccess, dwShareMode,
                       lpSecurityAttributes, dwCreationDisposition,
                       dwFlagsAndAttributes, hTemplateFile);
}

BOOL CloseHandle(HANDLE hObject)
{
    uint64_t args[1] = {0};
    args[0] = (uint64_t)(uintptr_t)hObject;
    return (nt_syscall(NR_NT_CLOSE, args) == NT_STATUS_SUCCESS) ? TRUE : FALSE;
}

DWORD GetFileSize(HANDLE hFile, LPDWORD lpFileSizeHigh)
{
    // Stub — return 0
    if (lpFileSizeHigh)
        *lpFileSizeHigh = 0;
    return 0;
}

DWORD SetFilePointer(HANDLE hFile, LONG lDistanceToMove,
                     LPLONG lpDistanceToMoveHigh, DWORD dwMoveMethod)
{
    // Stub — return 0
    return 0;
}

BOOL GetFileTime(HANDLE hFile, LPFILETIME lpCreationTime,
                 LPFILETIME lpLastAccessTime, LPFILETIME lpLastWriteTime)
{
    // Stub — return FALSE
    return FALSE;
}

// ── String Conversion ──────────────────────────────────────────────────────

int MultiByteToWideChar(UINT CodePage, DWORD dwFlags, LPCSTR lpMultiByteStr,
                        int cbMultiByte, LPWSTR lpWideCharStr, int cchWideChar)
{
    int len = 0;
    int i;

    if (!lpMultiByteStr)
        return 0;

    // Calculate string length if cbMultiByte is -1
    if (cbMultiByte < 0) {
        for (len = 0; lpMultiByteStr[len]; len++)
            ;
        cbMultiByte = len + 1;
    }

    // If output buffer is 0, return required size
    if (cchWideChar == 0)
        return cbMultiByte;

    // Convert ASCII to UTF-16
    for (i = 0; i < cbMultiByte && i < cchWideChar; i++)
        lpWideCharStr[i] = (WCHAR)(unsigned char)lpMultiByteStr[i];

    return i;
}

int WideCharToMultiByte(UINT CodePage, DWORD dwFlags, LPCWSTR lpWideCharStr,
                        int cchWideChar, LPSTR lpMultiByteStr, int cbMultiByte,
                        LPCSTR lpDefaultChar, LPBOOL lpUsedDefaultChar)
{
    int len = 0;
    int i;

    if (!lpWideCharStr)
        return 0;

    // Calculate string length if cchWideChar is -1
    if (cchWideChar < 0) {
        for (len = 0; lpWideCharStr[len]; len++)
            ;
        cchWideChar = len + 1;
    }

    // If output buffer is 0, return required size
    if (cbMultiByte == 0)
        return cchWideChar;

    // Convert UTF-16 to ASCII (truncate to low byte)
    for (i = 0; i < cchWideChar && i < cbMultiByte; i++)
        lpMultiByteStr[i] = (char)(lpWideCharStr[i] & 0x7F);

    return i;
}

LPSTR GetCommandLineA(void)
{
    static char empty[] = "";
    return empty;
}

LPWSTR GetCommandLineW(void)
{
    static wchar_t empty[] = L"";
    return empty;
}

// ── Environment ────────────────────────────────────────────────────────────

DWORD GetEnvironmentVariableA(LPCSTR lpName, LPSTR lpBuffer, DWORD nSize)
{
    // Stub — no environment support yet
    return 0;
}

BOOL SetEnvironmentVariableA(LPCSTR lpName, LPCSTR lpValue)
{
    // Stub
    return FALSE;
}

DWORD GetLastError(void)
{
    // Stub — always return 0
    return 0;
}

void SetLastError(DWORD dwErrCode)
{
    // Stub — no per-thread error storage yet
}

// ── Module ─────────────────────────────────────────────────────────────────

HMODULE GetModuleHandleA(LPCSTR lpModuleName)
{
    // Stub — return NULL (no module loading support yet)
    return NULL;
}

HMODULE LoadLibraryA(LPCSTR lpLibFileName)
{
    // Stub
    return NULL;
}

FARPROC GetProcAddress(HMODULE hModule, LPCSTR lpProcName)
{
    // Stub
    return NULL;
}

BOOL FreeLibrary(HMODULE hLibModule)
{
    // Stub
    return FALSE;
}
