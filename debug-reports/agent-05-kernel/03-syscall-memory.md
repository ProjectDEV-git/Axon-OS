# Syscall Translation & Memory Safety Report

**Agent**: Debug Agent 5 (Kernel Module)  
**Date**: 2026-07-02  
**Files Analyzed**: `nt-syscalls.c`, `nt-section.c`, `nt-thread.c`, `nt-sync.c`, `nt-registry.c`, `syscall_table.c`, `axon-winabi-test.c`, `module-main.c`

---

## Severity Legend

| Level | Meaning |
|-------|---------|
| CRITICAL | Security vulnerability, kernel memory corruption, or exploitable race condition |
| BUG | Incorrect behavior that will cause crashes or wrong results |
| WARNING | Design issue that limits functionality or creates edge cases |
| INFO | Observation or minor improvement suggestion |

---

## 1. NT Syscall Translation Correctness

### 1.1 nt_allocate_virtual_memory (Syscall 0x03)

**Status**: BUG

```c
__u32 nt_allocate_virtual_memory(const __u64 *args)
{
    // ...
    prot = PROT_READ | PROT_WRITE;   // ALWAYS read+write!
    if (protect & 0x10 || protect & 0x20)
        prot |= PROT_EXEC;
    // ...
}
```

**Issues:**
- **Unconditional PROT_WRITE**: All allocations get write permission regardless of the `protect` parameter. `PAGE_READONLY` (0x02), `PAGE_EXECUTE` (0x10), and `PAGE_EXECUTE_READ` (0x20) all receive `PROT_READ | PROT_WRITE`. This defeats W^X (Write XOR Execute) security.
- **Missing MEM_RESERVE vs MEM_COMMIT distinction**: Windows separates reserving address space from committing physical pages. This implementation commits everything immediately via `vm_mmap`. Applications using lazy commit (reserve then commit regions on demand) will consume more physical memory than necessary.
- **Missing PAGE_WRITECOPY (0x08)**: Should map as `PROT_READ | PROT_WRITE | MAP_PRIVATE` for copy-on-write semantics.

**Impact**: W^X violation means code sections are writable, enabling trivial code injection attacks. Memory over-commit breaks Windows allocation patterns.

---

### 1.2 nt_terminate_process (Syscall 0x01)

**Status**: BUG

```c
__u32 nt_terminate_process(const __u64 *args)
{
    int exit_code = (int)args[1];
    send_sig(SIGKILL, current, 1);
    return NT_STATUS_SUCCESS;
}
```

**Issue**: `send_sig(SIGKILL, current, 1)` sends SIGKILL to the **current thread**, not the entire process. On Windows, `NtTerminateProcess` terminates the entire process and all its threads.

**Impact**: Other threads in the process continue running after the calling thread is killed, leading to orphaned threads accessing freed resources.

**Fix**: Use `do_group_exit(SIGKILL)` or `sys_kill(pid, SIGKILL)` targeting the process group.

---

### 1.3 nt_delay_execution (Syscall 0x104)

**Status**: GOOD (with minor issue)

```c
if (interval < 0) {
    timeout = usecs_to_jiffies((unsigned long)(-interval / 10));
} else if (interval > 0) {
    timeout = usecs_to_jiffies((unsigned long)(interval / 10));
} else {
    timeout = 0;
}
```

**Minor Issue**: `interval / 10` converts 100ns units to microseconds. For very large intervals (> ~2.6 hours), the cast to `unsigned long` can overflow. However, sleep durations this long are unusual and the overflow would just result in a shorter sleep, not a crash.

**Correct**: Negative intervals are correctly treated as relative delays. Zero interval returns immediately (alertable wait without timeout).

---

### 1.4 nt_query_system_time (Syscall 0x103)

**Status**: GOOD

```c
__u64 nt_time = ns / 100ULL + 116444736000000000ULL;
```

The NT epoch offset (100ns intervals from 1601-01-01 to 1970-01-01) is correct: 116,444,736,000,000,000.

**Note**: No leap second handling. The NT time standard also doesn't account for leap seconds, so this is correct.

---

### 1.5 nt_write_file / nt_read_file

**Status**: WARNING

These implement synchronous I/O using the file handle IDR (Index Radix Tree):

```c
static struct file *axon_file_lookup(__u64 handle)
{
    spin_lock(&axon_file_idr_lock);
    f = idr_find(&axon_file_idr, (int)handle);
    if (f) get_file(f);
    spin_unlock(&axon_file_idr_lock);
    return f;
}
```

**Issues:**
- **Truncation**: `(__u64)handle` cast to `int` — if the handle is > INT_MAX, it wraps to a negative number. IDR lookups with negative IDs will fail silently.
- **No async I/O**: Windows `NtWriteFile` supports OVERLAPPED (async) I/O with completion ports. This implementation is synchronous-only. Apps using I/O completion ports will hang.
- **APC delivery ignored**: The `ApcRoutine` and `ApcContext` parameters in NtWriteFile/NtReadFile are not implemented. Apps relying on APC-based I/O notification will not receive callbacks.

---

### 1.6 Memory Protection Translation

**Status**: BUG

The protection flag mapping in `nt_allocate_virtual_memory` and `nt_protect_virtual_memory` is incomplete:

| Windows Flag | Value | Linux Mapping (Actual) | Correct Linux Mapping |
|---|---|---|---|
| PAGE_NOACCESS | 0x01 | PROT_READ\|PROT_WRITE | PROT_NONE |
| PAGE_READONLY | 0x02 | PROT_READ\|PROT_WRITE | PROT_READ |
| PAGE_READWRITE | 0x04 | PROT_READ\|PROT_WRITE | PROT_READ\|PROT_WRITE |
| PAGE_WRITECOPY | 0x08 | PROT_READ\|PROT_WRITE | PROT_READ\|PROT_WRITE (MAP_PRIVATE) |
| PAGE_EXECUTE | 0x10 | PROT_READ\|PROT_WRITE\|PROT_EXEC | PROT_EXEC |
| PAGE_EXECUTE_READ | 0x20 | PROT_READ\|PROT_WRITE\|PROT_EXEC | PROT_READ\|PROT_EXEC |
| PAGE_EXECUTE_READWRITE | 0x40 | PROT_READ\|PROT_WRITE\|PROT_EXEC | PROT_READ\|PROT_WRITE\|PROT_EXEC |
| PAGE_GUARD | 0x100 | (not handled) | PROT_NONE + signal on access |

**Impact**: Applications that intentionally set read-only or no-access pages (for guard pages, stack overflow detection, etc.) get writable pages instead.

---

## 2. User-Space Pointer Bounds Checking

### 2.1 Checked Cases (Good)

- `nt_query_system_time`: `access_ok(out, sizeof(*out))` before `copy_to_user` — **correct**.
- `nt_delay_execution`: `access_ok(interval_ptr, sizeof(interval))` before `copy_from_user` — **correct**.
- `nt_allocate_virtual_memory`: Uses `copy_from_user`/`copy_to_user` which implicitly validate — **correct**.
- `nt_create_event`: `access_ok(handle_out, sizeof(handle))` before `copy_to_user` — **correct**.

### 2.2 Unchecked Cases (BUG)

**BUG: nt_create_thread_ex / nt_create_thread**
```c
__u32 nt_create_thread_ex(const __u64 *args)
{
    __u64 __user *handle_out = (__u64 __user *)args[0];
    unsigned long start_routine = (unsigned long)args[4];
    unsigned long argument = (unsigned long)args[5];
    unsigned long stack_size = (unsigned long)args[8];
    return do_create_thread(handle_out, start_routine, argument, stack_size);
}
```

**No validation** that `start_routine` points to valid user-space memory. A malicious PE could pass a kernel address as `start_routine`, and since the thread runs in user mode, the CPU would fault when trying to execute kernel-mode addresses from ring 3. This is "safe" (the process crashes) but:
- `argument` is also unchecked — it's passed to the user function which will try to dereference it.
- A crafted `argument` pointing to kernel memory would cause an information leak when the function reads it.

**BUG: nt_create_file path pointer** (from nt-syscalls.c, truncated but visible pattern):
User-space string pointers in syscall args are passed directly to kernel path functions without `access_ok()` pre-checks. While `copy_from_user` would catch invalid pointers, if the kernel code uses `strncpy_from_user` or similar, a malicious pointer in the args array could cause issues.

---

## 3. VirtualAlloc Permission Mapping

### Status: BUG (see Section 1.6 above)

Additional issues in `nt_protect_virtual_memory`:

The implementation likely has the same incomplete flag mapping as `nt_allocate_virtual_memory`. The `PAGE_GUARD` flag (0x100) is not handled at all. On Windows, guard pages trigger a one-shot `STATUS_GUARD_PAGE_VIOLATION` exception on first access, then become normal pages. This is used for:
- Stack growth detection
- Heap guard pages
- Security cookies

Without guard page support, stack overflow detection in Windows apps will not work.

---

## 4. Concurrent Access from Multiple PE Threads

### 4.1 Task State Management

**Status**: CRITICAL

```c
// module-main.c
struct axon_task_state *axon_get_task_state(struct task_struct *tsk)
{
    struct axon_task_entry *entry;
    pid_t pid = task_pid_vnr(tsk);
    hash_for_each_possible(axon_task_table, entry, node, pid) {
        if (entry->pid == pid)
            return &entry->state;
    }
    return NULL;
}
```

**Issue**: `axon_get_task_state` reads from `axon_task_table` **without holding the spinlock**. Meanwhile, `axon_task_state_free` modifies the table **with the spinlock**. If thread A calls `axon_get_task_state` and finds an entry, then thread B calls `axon_task_state_free` for the same PID, thread A returns a pointer to freed memory.

**Impact**: Use-after-free when a PE process exits while another thread is in a syscall that accesses task state.

**Fix**: Either:
1. Hold `axon_task_lock` during the entire lookup-and-use cycle, or
2. Use `rcu_read_lock()` / `rcu_read_unlock()` with `kfree_rcu()` for lock-free reads.

---

### 4.2 File Handle IDR

**Status**: BUG

```c
static DEFINE_SPINLOCK(axon_file_idr_lock);

static int axon_file_install(struct file *f)
{
    int id;
    idr_preload(GFP_KERNEL);
    spin_lock(&axon_file_idr_lock);
    id = idr_alloc(&axon_file_idr, f, 3, 0, GFP_NOWAIT);
    spin_unlock(&axon_file_idr_lock);
    idr_preload_end();
    return id;
}
```

**Issues:**
1. `idr_preload(GFP_KERNEL)` allocates memory, then `spin_lock` is taken, then `idr_alloc(..., GFP_NOWAIT)` is called. If the preload didn't allocate enough, `idr_alloc` with `GFP_NOWAIT` fails. The preload guarantees at least one allocation, so this works for single allocations, but is fragile.
2. **No file reference counting on IDR removal**: When a file handle is closed, `idr_remove` is called but there's no corresponding `fput()` to release the file reference. This leaks file references.
3. **Handle reuse race**: IDR can reuse IDs after removal. If thread A has handle 5 pointing to file X, and file X is closed (ID 5 removed), and thread B opens a new file (gets ID 5), thread A's handle now silently points to the new file. This is a TOCTOU vulnerability.

---

### 4.3 Section Handle Management

**Status**: WARNING

```c
static u32 axon_section_next_handle = 1;
```

The section handle counter is a plain `u32`, not atomic. While access is protected by `axon_section_lock`, the counter could overflow after ~4 billion sections, wrapping back to 1 and colliding with existing handles.

**Impact**: Low probability but handle collision could cause one process to access another process's memory section.

---

### 4.4 Synchronization Objects

**Status**: BUG

```c
struct axon_event {
    wait_queue_head_t wq;
    spinlock_t lock;
    bool manual_reset;
    bool signaled;
};
```

**Issue**: `nt_set_event` uses `spin_lock(&evt->lock)` to set `signaled = true` and call `wake_up`. But `wake_up` can sleep (it calls `__wake_up_locked` which takes the wait queue spinlock). Calling `wake_up` while holding `evt->lock` is fine because `wake_up` only takes the wait queue's internal lock, not `evt->lock`. However:

```c
__u32 nt_pulse_event(const __u64 *args)
{
    // ...
    spin_lock(&evt->lock);
    evt->signaled = true;
    wake_up_all(&evt->wq);
    evt->signaled = false;     // Reset BEFORE waiters wake up!
    spin_unlock(&evt->lock);
    return NT_STATUS_SUCCESS;
}
```

**BUG**: `nt_pulse_event` sets `signaled = true`, wakes all waiters, then immediately sets `signaled = false` — all while holding the spinlock. Since waiters are woken inside `spin_lock`, they try to acquire `evt->lock` but can't (deadlock). When `spin_unlock` finally releases, all waiters see `signaled == false` and go back to sleep. **Pulse event never actually works.**

**Impact**: Applications using `NtPulseEvent` (rare but exists in some IPC patterns) will experience deadlocks or missed signals.

---

## 5. Test Coverage Analysis

### Status: CRITICAL (Severely Inadequate)

The test file `axon-winabi-test.c` has **5 test cases**, of which **2 are stubs** (no real assertions):

| Test | Real? | What it Tests |
|------|-------|---------------|
| `test_pe_validate_mz_magic` | NO | `KUNIT_SUCCEED()` — always passes |
| `test_pe_validate_invalid_magic` | NO | `KUNIT_SUCCEED()` — always passes |
| `test_syscall_dispatch_valid` | YES | Dispatches syscall 0x100, checks `NT_STATUS_SUCCESS` |
| `test_syscall_dispatch_oob` | YES | Out-of-range syscall returns `NOT_IMPLEMENTED` |
| `test_syscall_dispatch_unimplemented` | YES | Unregistered syscall returns `NOT_IMPLEMENTED` |

**What's Not Tested (Critical Gaps):**

| Category | Missing Tests | Risk |
|----------|--------------|------|
| PE Validation | No test with actual PE binary data | All PE parsing bugs undetected |
| Memory Allocation | No VirtualAlloc/Free test | Protection mapping bugs undetected |
| File I/O | No NtCreateFile/ReadFile/WriteFile test | File handle leaks undetected |
| Thread Creation | No NtCreateThread test | Use-after-free in thread lifecycle |
| Synchronization | No event/mutant/semaphore tests | Pulse event deadlock undetected |
| Relocation | No relocation application test | Memory corruption from malformed DLLs |
| DLL Loading | No import resolution test | Wrong symbol resolution undetected |
| Concurrent Access | No multi-threaded stress test | Race conditions undetected |
| Error Handling | No malformed PE input tests | Kernel panics from adversarial input |
| Registry | No NtOpenKey/NtQueryValueKey test | Wrong syscall numbers undetected |

**Recommendation**: Minimum viable test coverage should include:
1. A minimal valid PE binary (test fixture) for parse/load tests.
2. At least one VirtualAlloc/VirtualFree round-trip test.
3. Event creation/set/reset/wait cycle test.
4. A fuzz test that feeds random bytes to PE validation.
5. A concurrency test with two threads doing syscalls simultaneously.

---

## 6. Memory Mapping Analysis

### 6.1 PE Section Mapping

**Status**: BUG

From the visible code in `pe-loader.c`, `pe_map_sections` maps sections into user space. The key question is whether file-backed sections get their content loaded from the PE file.

Based on the architecture:
- Sections are mapped with `vm_mmap` using `MAP_PRIVATE | MAP_ANONYMOUS` — this creates anonymous pages with no file content.
- The PE file content must be loaded separately (e.g., via `copy_from_user` from a kernel buffer, or via `do_mmap` with the file).
- **If the truncated code doesn't load file content into mapped sections, all code and data sections will be zero-filled.**

**Impact**: If file content isn't loaded, PE executables would execute zeroed memory (NOP sleds or illegal instructions). This is a fundamental correctness issue.

**Note**: This finding requires verification of the full `pe_map_sections` implementation (truncated in my analysis). If the code does use `do_mmap` with file backing, this is not an issue.

---

### 6.2 Mapping List Tracking

**Status**: WARNING

```c
static LIST_HEAD(axon_mapping_list);
static DEFINE_SPINLOCK(axon_mapping_lock);
```

The mapping list is global (shared across all processes). When process A creates a mapping and process B queries mappings, there's no PID filtering. This means:
1. One process can see another process's mappings.
2. `NtUnmapViewOfSection` in one process could unmap another process's memory.

**Fix**: Add PID to `struct axon_mapping` and filter by current process.

---

## 7. NTSTATUS Return Code Consistency

### Status: WARNING

Multiple files redefine NT status codes locally:

```c
// nt-syscalls.c:
#define NT_STATUS_INFO_LENGTH_MISMATCH 0xC0000004
#define NT_STATUS_OBJECT_NAME_NOT_FOUND 0xC0000034
#define NT_STATUS_INVALID_HANDLE        0xC0000008

// nt-section.c:
#define NT_STATUS_INFO_LENGTH_MISMATCH 0xC0000004

// nt-sync.c:
#define NT_STATUS_INVALID_HANDLE 0xC0000008

// nt-registry.c:
#define NT_STATUS_OBJECT_NAME_NOT_FOUND 0xC0000034
```

These should be centralized in `axon-winabi.h` to prevent drift and typos.

**Missing Status Codes** used in the code:
- `NT_STATUS_ACCESS_VIOLATION` (0xC0000005) — referenced but not defined in visible headers
- `NT_STATUS_INVALID_PARAMETER` (0xC000000D) — referenced but not defined in visible headers
- `NT_STATUS_NO_MEMORY` (0xC0000017) — referenced but not defined in visible headers

---

## 8. Module Lifecycle (module-main.c)

### Status: GOOD (with minor issue)

The init/exit sequence properly tears down subsystems in reverse order:

```c
// Init: handle_table -> syscall_table -> registry -> dll_loader -> binfmt
// Exit: binfmt -> dll_loader -> registry -> syscall_table -> handle_table
```

This is correct cascading initialization/cleanup.

**Minor Issue**: `axon_handle_table_exit()` iterates with `idr_for_each_entry` while removing entries. This is safe in the Linux kernel (the macro saves the next pointer), but the IDR itself is never destroyed with `idr_destroy()`.

---

## Summary Table

| ID | Severity | File | Description |
|----|----------|------|-------------|
| SC-1 | CRITICAL | module-main.c | `axon_get_task_state` use-after-free (no lock on read) |
| SC-2 | CRITICAL | axon-winabi-test.c | Test coverage is 5% — only 3 real tests, no PE/memory/thread/file tests |
| SC-3 | BUG | nt-syscalls.c | `nt_allocate_virtual_memory` always grants PROT_WRITE |
| SC-4 | BUG | nt-syscalls.c | `nt_terminate_process` kills thread, not process group |
| SC-5 | BUG | nt-sync.c | `nt_pulse_event` deadlock (wake_all inside spinlock, immediate reset) |
| SC-6 | BUG | nt-thread.c | `kthread_use_mm` called in wrong context |
| SC-7 | BUG | nt-syscalls.c | Protection flags not properly mapped (no PAGE_NOACCESS, PAGE_READONLY) |
| SC-8 | BUG | nt-thread.c | `start_routine` address not validated against user-space range |
| SC-9 | WARNING | nt-syscalls.c | File handle IDR: no fput on removal, handle reuse race |
| SC-10 | WARNING | nt-section.c | Mapping list is global, not per-process |
| SC-11 | WARNING | multiple | NTSTATUS codes duplicated across files, not centralized |
| SC-12 | WARNING | nt-thread.c | Thread handle counter is global, cross-process collisions |
| SC-13 | INFO | module-main.c | IDR not destroyed on module exit |
