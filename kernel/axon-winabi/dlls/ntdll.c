/* ntdll.dll stub — Axon Windows ABI
 * User-space syscall thunks for NT kernel interface.
 * Compiled as a shared library (.so) and shipped as ntdll.dll.
 */

#include <wchar.h>

/* NT type definitions */
typedef unsigned int       NTSTATUS;
typedef unsigned long      ULONG;
typedef unsigned long long ULONG_PTR;
typedef ULONG_PTR          SIZE_T;
typedef unsigned short     USHORT;
typedef unsigned short     WCHAR;
typedef char              *PSTR;
typedef const char        *PCSTR;
typedef wchar_t           *PWSTR;
typedef const wchar_t     *PCWSTR;
typedef void              *PVOID;

#define NTAPI           __attribute__((ms_abi))
#define NT_SYSCALL      __attribute__((naked, visibility("default")))

#define STATUS_SUCCESS  0

/* UNICODE_STRING layout used by NT APIs */
typedef struct _UNICODE_STRING {
    USHORT Length;
    USHORT MaximumLength;
    PWSTR  Buffer;
} UNICODE_STRING, *PUNICODE_STRING;

/* ---------- syscall thunk macro ----------
 *
 * The Windows x64 ABI passes the first arg in rcx; the Linux x64 syscall
 * convention expects it in r10.  We move rcx -> r10, load the syscall
 * number into rax, execute `syscall`, and return.
 */
#define DEFINE_NT_SYSCALL(ret, name, nr)                         \
    NT_SYSCALL ret NTAPI name(void) {                            \
        __asm__ volatile (                                       \
            "mov %%rcx, %%r10\n"                                 \
            "mov $" #nr ", %%eax\n"                              \
            "syscall\n"                                          \
            "ret\n"                                              \
        );                                                       \
    }

/* ======== Process / thread ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtTerminateProcess,          0x01)
DEFINE_NT_SYSCALL(NTSTATUS, NtQueryInformationProcess,   0x0A)
DEFINE_NT_SYSCALL(NTSTATUS, NtSetInformationProcess,     0x0B)
DEFINE_NT_SYSCALL(NTSTATUS, NtCreateThreadEx,            0x4E)
DEFINE_NT_SYSCALL(ULONG,    NtGetCurrentProcessId,       0x100)
DEFINE_NT_SYSCALL(ULONG,    NtGetCurrentThreadId,        0x101)

/* ======== Virtual memory ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtAllocateVirtualMemory,     0x03)
DEFINE_NT_SYSCALL(NTSTATUS, NtFreeVirtualMemory,         0x05)
DEFINE_NT_SYSCALL(NTSTATUS, NtQueryVirtualMemory,        0x28)
DEFINE_NT_SYSCALL(NTSTATUS, NtProtectVirtualMemory,      0x29)

/* ======== Sections ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtCreateSection,             0x23)
DEFINE_NT_SYSCALL(NTSTATUS, NtMapViewOfSection,          0x25)
DEFINE_NT_SYSCALL(NTSTATUS, NtUnmapViewOfSection,        0x27)

/* ======== File I/O ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtCreateFile,                0x08)
DEFINE_NT_SYSCALL(NTSTATUS, NtClose,                     0x09)
DEFINE_NT_SYSCALL(NTSTATUS, NtReadFile,                  0x07)
DEFINE_NT_SYSCALL(NTSTATUS, NtWriteFile,                 0x06)
DEFINE_NT_SYSCALL(NTSTATUS, NtQueryInformationFile,      0x10)

/* ======== Registry ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtOpenKey,                   0x2C)
DEFINE_NT_SYSCALL(NTSTATUS, NtQueryValueKey,             0x2D)

/* ======== Synchronization ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtCreateEvent,               0x3A)
DEFINE_NT_SYSCALL(NTSTATUS, NtSetEvent,                  0x3B)
DEFINE_NT_SYSCALL(NTSTATUS, NtResetEvent,                0x3C)
DEFINE_NT_SYSCALL(NTSTATUS, NtCreateMutant,              0x40)
DEFINE_NT_SYSCALL(NTSTATUS, NtReleaseMutant,             0x41)
DEFINE_NT_SYSCALL(NTSTATUS, NtCreateSemaphore,           0x43)
DEFINE_NT_SYSCALL(NTSTATUS, NtReleaseSemaphore,          0x44)
DEFINE_NT_SYSCALL(NTSTATUS, NtWaitForSingleObject,       0x38)

/* ======== Time / tick ======== */
DEFINE_NT_SYSCALL(NTSTATUS, NtQuerySystemTime,           0x103)
DEFINE_NT_SYSCALL(ULONG,    NtGetTickCount,              0x102)
DEFINE_NT_SYSCALL(NTSTATUS, NtDelayExecution,            0x104)

/* ======== Runtime helpers ======== */

/* Initialise a UNICODE_STRING from a wide-char source.
 * Only copies the pointer and computes byte-length; no allocation.
 */
__attribute__((visibility("default")))
void RtlInitUnicodeString(PVOID dst, PCWSTR src) {
    UNICODE_STRING *us = (UNICODE_STRING *)dst;
    if (!src) {
        us->Length        = 0;
        us->MaximumLength = 0;
        us->Buffer        = NULL;
        return;
    }
    USHORT len = 0;
    while (src[len]) len++;
    us->Length        = len * sizeof(WCHAR);
    us->MaximumLength = (len + 1) * sizeof(WCHAR);
    us->Buffer        = (PWSTR)src;
}

/* Map an NTSTATUS to a Win32 error code (trivial pass-through stub). */
__attribute__((visibility("default")))
ULONG RtlNtStatusToDosError(NTSTATUS status) {
    return (ULONG)status;
}

/* Debug print — no-op in this stub. */
__attribute__((visibility("default")))
void DbgPrint(const char *fmt, ...) {
    (void)fmt;
}
