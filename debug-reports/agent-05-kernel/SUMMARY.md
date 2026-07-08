# axon-winabi Kernel Module — Debug Summary

**Agent**: Debug Agent 5 (Kernel Module)  
**Date**: 2026-07-02  
**Module Version**: 0.1.0 / 0.2.0  
**Lines of Code**: ~3,900 (kernel module + user-space DLL stubs)  
**Files Analyzed**: 24 source files

---

## Executive Summary

The axon-winabi module is an ambitious and structurally sound Windows ABI compatibility layer. The architecture (binfmt handler + PE loader + NT syscall translation + DLL emulation) is correct in principle. However, the module has **9 CRITICAL bugs**, **13 BUG-level issues**, and **14 WARNINGs** that must be addressed before any production use. The most severe issues are: a use-after-free in task state management, wrong syscall numbers in advapi32, incomplete memory protection mapping, a deadlock in pulse event handling, and severely inadequate test coverage.

---

## CRITICAL Bugs (Must Fix Before Deployment)

| # | File | Description | Impact |
|---|------|-------------|--------|
| C1 | `module-main.c` | **Use-after-free in `axon_get_task_state`** — reads hash table without spinlock; concurrent `axon_task_state_free` can free the entry while it's being read | Kernel crash / exploitable |
| C2 | `nt-thread.c` | **Thread creation uses wrong mm context** — `kthread_use_mm(current->mm)` called from parent instead of child; new threads may not have correct address space | Thread crashes on first user-space access |
| C3 | `advapi32.c` | **Wrong syscall numbers** — `NR_NT_OPEN_KEY=0x0F4`, `NR_NT_CLOSE=0x00F` vs correct `0x2C`, `0x09` | All registry operations broken |
| C4 | `nt-sync.c` | **`nt_pulse_event` deadlock** — `wake_up_all` called inside spinlock, followed by immediate `signaled=false`; waiters see false and re-sleep | Pulse events never work |
| C5 | `nt-syscalls.c` | **Memory protection always grants write** — `PAGE_READONLY`, `PAGE_EXECUTE`, `PAGE_NOACCESS` all get `PROT_READ\|PROT_WRITE` | W^X violation, security hole |
| C6 | `binfmt_win.c` | **`begin_new_exec` ordering** — called before user-space mapping is complete; failure after this point leaves process in corrupted state | Orphaned/corrupted processes |
| C7 | `nt-dll-loader.c` | **Relocation patching no bounds check** — `page_rva + rva_off` not validated against image bounds before write | Kernel memory corruption from malformed DLL |
| C8 | `advapi32.c` | **Syscall return values ignored** — `RegOpenKeyExA` always returns `ERROR_SUCCESS` even for missing keys | Silent data loss |
| C9 | `shell32.c` | **Buffer overflow** — `strcpy(pszPath, "/tmp")` with no size check | Stack corruption |

---

## High-Priority Bugs

| # | File | Description |
|---|------|-------------|
| B1 | `nt-syscalls.c` | `nt_terminate_process` kills thread, not process group |
| B2 | `nt-thread.c` | `start_routine` address not validated against user-space range |
| B3 | `nt-thread.c` | Thread handle counter is global, cross-process collisions possible |
| B4 | `pe-loader.c` | `NumberOfRvaAndSizes` not clamped to `PE_DIR_MAX` (OOB risk) |
| B5 | `pe-loader.c` | Section `VirtualAddress`/`SizeOfRawData` not validated for integer overflow |
| B6 | `binfmt_win.c` | Error path after `begin_new_exec` cannot roll back |
| B7 | `advapi32.c` | `RegQueryValueExA` memset-0 no-op logic bug |
| B8 | `d3d9/d3d11/d3d12/dxgi` | Thread-unsafe DXVK initialization (TOCTOU on `d3d9_initialized`) |
| B9 | `nt-syscalls.c` | File handle IDR: no `fput` on removal, handle reuse race |
| B10 | `nt-section.c` | Mapping list is global (not per-process) — cross-process unmap possible |
| B11 | `nt-syscalls.c` | Protection flags incomplete (no PAGE_GUARD, PAGE_WRITECOPY) |
| B12 | `user32.c` | `GetMessageA` returns FALSE immediately — all GUI apps exit |
| B13 | `axon-winabi-test.c` | Only 3 real tests; 2 are stubs; 0% coverage of PE/memory/thread/file/subsystem paths |

---

## Architecture Assessment

### What's Working Well
- **Module lifecycle management** — init/exit cascading is clean and correct
- **PE header parsing** — basic validation is solid (MZ magic, PE signature, machine type, section count)
- **Syscall dispatch** — table-based dispatch with bounds checking is clean
- **Registry emulation** — in-memory tree with proper reference counting
- **COM stubs** — `wasapi.c` COM initialization/uninitialization is correct
- **Graphics DLL forwarding** — DXVK/vkd3d-proton via dlopen is the right architecture
- **Event/mutant/semaphore primitives** — basic set/reset/wait logic is correct (except pulse)

### What Needs Fundamental Redesign

1. **Thread Lifecycle**: The `kthread_create` + `kthread_use_mm` + `vm_mmap` + pt_regs pattern for user-space threads is fragile. Consider using `kernel_clone` with proper flags or a dedicated trampoline that enters via `ret_from_fork`.

2. **Handle Management**: There are 3 separate handle systems (file IDR in nt-syscalls.c, section IDR in nt-section.c, sync IDR in nt-sync.c) with inconsistent locking patterns. Unify into a single per-process handle table with consistent reference counting.

3. **Memory Protection**: Implement a complete Windows protection flag translation table and respect W^X. Add `mprotect` calls for `NtProtectVirtualMemory`.

4. **Test Infrastructure**: The 5-test KUnit suite is insufficient for a kernel module of this complexity. Need at minimum:
   - A binary test fixture (minimal valid PE)
   - Fuzzing harness for PE validation
   - Per-syscall integration tests
   - Concurrency stress tests

---

## Recommended Fix Priority

### Phase 0 (Immediate — Security/Correctness)
1. Fix use-after-free in `axon_get_task_state` (C1) — add RCU or spinlock
2. Fix advapi32 syscall numbers (C3) — trivial one-line fix
3. Fix memory protection mapping (C5) — implement full flag table
4. Fix relocation bounds checking (C7) — add range validation
5. Fix `nt_pulse_event` deadlock (C4) — restructure wake/reset sequence
6. Fix thread creation mm context (C2) — defer mm setup to child trampoline

### Phase 1 (Before Alpha)
7. Fix `begin_new_exec` ordering (C6) — move user mapping before exec
8. Fix `nt_terminate_process` (B1) — use `do_group_exit`
9. Fix shell32 buffer overflow (C9) — use `strscpy`
10. Fix advapi32 return values (C8) — check and propagate syscall status
11. Add PE section validation (B4, B5)
12. Fix file handle lifecycle (B9)

### Phase 2 (Before Beta)
13. Thread-safe DXVK initialization (B8)
14. Per-process mapping list (B10)
15. Complete protection flag support (B11)
16. Write comprehensive test suite (B13)
17. Centralize NTSTATUS codes

---

## Files Generated

| File | Description |
|------|-------------|
| `01-pe-parsing.md` | PE/COFF header parsing analysis (7 findings) |
| `02-dll-emulation.md` | DLL emulation layer analysis (12 findings) |
| `03-syscall-memory.md` | Syscall translation & memory safety analysis (13 findings) |
| `SUMMARY.md` | This file |

**Total findings**: 9 CRITICAL, 13 BUG, 14 WARNING, 3 INFO
