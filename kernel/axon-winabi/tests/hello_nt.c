// Minimal NT syscall test — no DLL dependencies
// This makes direct NT syscalls via the syscall instruction

#include <stdint.h>

// NT syscall numbers (matching our kernel module)
#define NT_WRITE_FILE 0x06
#define NT_TERMINATE_PROCESS 0x01

// NT status codes
#define NT_STATUS_SUCCESS 0x00000000

// NT IO_STATUS_BLOCK
struct io_status_block {
    uint32_t status;
    uint32_t information;
};

// Write to stdout via NtWriteFile syscall
static uint32_t nt_write_file(uint64_t handle, const void *buf, uint32_t len) {
    struct io_status_block iosb = {0, 0};
    uint64_t args[9];
    args[0] = handle;           // FileHandle
    args[1] = 0;                // Event
    args[2] = 0;                // ApcRoutine
    args[3] = 0;                // ApcContext
    args[4] = (uint64_t)&iosb;  // IoStatusBlock
    args[5] = (uint64_t)buf;    // Buffer
    args[6] = len;              // Length
    args[7] = 0;                // ByteOffset
    args[8] = 0;                // Key

    uint32_t result;
    // x86-64 NT syscall: rax = syscall number, r10 = args pointer
    __asm__ volatile (
        "mov %[nr], %%eax\n"
        "mov %[args_ptr], %%r10\n"
        "syscall\n"
        : "=a" (result)
        : [nr] "r" ((uint32_t)NT_WRITE_FILE), [args_ptr] "r" (args)
        : "r10", "rcx", "r11", "memory"
    );
    return result;
}

static void nt_terminate(uint32_t exit_code) {
    uint64_t args[2] = { (uint64_t)-1, exit_code };
    __asm__ volatile (
        "mov %[nr], %%eax\n"
        "mov %[args_ptr], %%r10\n"
        "syscall\n"
        : : [nr] "r" ((uint32_t)NT_TERMINATE_PROCESS), [args_ptr] "r" (args)
        : "eax", "r10", "rcx", "r11", "memory"
    );
    __builtin_unreachable();
}

void _start(void) {
    const char msg[] = "Hello from Windows ABI!\n";
    nt_write_file(1, msg, sizeof(msg) - 1);
    nt_terminate(0);
}
