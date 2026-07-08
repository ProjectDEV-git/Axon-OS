# PE/COFF Header Parsing Correctness Report

**Agent**: Debug Agent 5 (Kernel Module)  
**Date**: 2026-07-02  
**Files Analyzed**: `axon-winabi.h`, `pe-loader.c`, `binfmt_win.c`

---

## Severity Legend

| Level | Meaning |
|-------|---------|
| CRITICAL | Security vulnerability or data corruption risk; must fix before any deployment |
| BUG | Incorrect behavior that will cause crashes or wrong results under normal use |
| WARNING | Design issue or edge case that may cause problems under adversarial conditions |
| INFO | Observation or minor improvement suggestion |

---

## 1. Struct Packing and Alignment

### Status: WARNING

All PE header structs (`pe_dos_header`, `pe_coff_header`, `pe_optional_header64`, `pe_optional_header32`, `pe_section_header`) use `__packed`.

**The Good:**
- Correctly matches the on-disk PE/COFF binary layout.
- `e_lfanew` is `__s32` matching the Windows `LONG` type.

**The Bad:**
- On ARM64 (which the module explicitly supports via `#ifdef CONFIG_ARM64`), unaligned reads from `__packed` structs cause alignment faults or silent performance degradation. The code does direct struct reads (`dos.e_magic`, `dos.e_lfanew`, etc.) without `get_unaligned()`.
- x86_64 silently handles unaligned access, masking the portability bug.

**Recommendation:** Use `get_le16()`, `get_le32()`, `get_le64()` accessor macros (from `<linux/byteorder/generic.h>`) for all packed struct field reads, or at minimum use `get_unaligned()` on ARM64.

---

## 2. MZ_MAGIC Validation and e_lfanew Bounds

### Status: GOOD (with minor issues)

**Validation flow:**
1. `read_mz_magic()` in `binfmt_win.c` reads 2 bytes, checks against `MZ_MAGIC` (0x5A4D) — **correct**.
2. `axon_pe_validate()` reads full DOS header, then validates `e_lfanew`:
   ```c
   pe_off = dos.e_lfanew;
   if (pe_off < (loff_t)sizeof(dos) || pe_off > 0x10000) {
   ```
   - Negative `e_lfanew` is caught by `< sizeof(dos)` since `loff_t` is signed.
   - Upper bound of 64KB is reasonable — real PE headers are always < 1KB from start.

**No buffer overflow risk on MZ validation:** The `read_mz_magic` function reads exactly 2 bytes before accessing `e_lfanew`, and `e_lfanew` is at offset 60 within the DOS header which is fully read by `pe_read_at(file, &dos, sizeof(dos), 0)`.

**Minor Issue:** `e_lfanew` is `__s32` (signed). On a file where `e_lfanew` has bit 31 set, assigning to `loff_t` produces a large negative number. The comparison `pe_off < (loff_t)sizeof(dos)` catches this correctly since negative < positive.

---

## 3. PE32 vs PE32+ Discrimination

### Status: GOOD

`pe_read_headers()` correctly:
1. Reads the optional header magic (2 bytes) at offset `pe_off + 4 + 20`.
2. Branches on `PE_OPT_MAGIC64` (0x20B) vs `PE_OPT_MAGIC32` (0x10B).
3. Checks `SizeOfOptionalHeader >= sizeof(struct)` before reading the full optional header.
4. Returns `-ENOEXEC` for unknown magic values.

Both optional header structs (`pe_optional_header32`, `pe_optional_header64`) are correctly defined with `__packed` and match the PE specification layouts, including the 64-bit `ImageBase` and pointer-sized stack/heap sizes for PE32+.

---

## 4. NumberOfSections vs PE_DIR_MAX

### Status: GOOD (no confusion found)

- `NumberOfSections` is validated in `axon_pe_validate()` against `== 0 || > 96`.
- `PE_DIR_MAX` (16) is the data directory count, not sections. These are separate concepts and not conflated.
- Section allocation uses `kcalloc(num_sections, sizeof(*sects), GFP_KERNEL)` which correctly prevents integer overflow.

---

## 5. Malformed/Corrupted PE Handling

### Status: BUG (3 issues found)

#### BUG-1: NumberOfRvaAndSizes Not Validated Against PE_DIR_MAX
**Severity**: BUG  
**File**: `pe-loader.c` (indirectly via optional header processing)  
**File**: `axon-winabi.h` (struct definition)

The `NumberOfRvaAndSizes` field in the optional header specifies how many data directory entries are valid. The struct defines `DataDirectory[PE_DIR_MAX]` (16 entries). If a malicious PE has `NumberOfRvaAndSizes = 32`, any code iterating over data directories based on this field would access beyond the array bounds.

```c
// In pe_optional_header64:
__u32 NumberOfRvaAndSizes;
struct pe_data_dir DataDirectory[PE_DIR_MAX]; // 16 entries
```

**Impact**: Out-of-bounds read when processing import directories, relocations, etc.  
**Fix**: Clamp `NumberOfRvaAndSizes` to `min(value, PE_DIR_MAX)` before use.

#### BUG-2: Section VirtualAddress/SizeOfRawData Not Validated
**Severity**: BUG  
**File**: `pe-loader.c`

Section headers contain `VirtualAddress` and `SizeOfRawData` which are used for address calculations in `pe_map_sections()`. A malformed PE could have:
- `VirtualAddress = 0xFFFFF000` and `SizeOfRawData = 0x2000`, causing arithmetic overflow when computing the end address.
- `PointerToRawData = 0xFFFFFFFF`, causing reads from invalid file offsets.

The current validation only checks `NumberOfSections` range, not the values within each section header.

**Impact**: Integer overflow in address calculations, potential kernel memory corruption.  
**Fix**: Validate that `VirtualAddress + VirtualSize` does not overflow, and that `PointerToRawData + SizeOfRawData` does not exceed file size.

#### BUG-3: No File Size Validation Against Headers
**Severity**: WARNING  
**File**: `pe-loader.c`

The code never checks that the actual file size is large enough to contain the headers it reads. While `pe_read_at` catches short reads (returns -EIO), a 200-byte file with `e_lfanew = 0x10000` would trigger 64KB+ of failed reads instead of being rejected upfront.

**Fix**: After reading the DOS header, check `file_size >= e_lfanew + sizeof(coff) + sizeof(optional_header)`.

---

## 6. Endianness Concerns

### Status: WARNING

All PE structures are little-endian. The Axon-OS code runs on x86_64 (native LE) and ARM64 (configurable endianness, usually LE).

**Risk**: If the module is ever compiled for big-endian ARM64 (aarch64_be), all struct reads would be byte-swapped. No `le16_to_cpu()` / `le32_to_cpu()` conversions are used anywhere.

**Current Impact**: Low (ARM64 Linux is almost always LE).  
**Future Impact**: High if big-endian support is needed.

---

## 7. `pe_read_at()` Robustness

### Status: GOOD

```c
static int pe_read_at(struct file *file, void *buf, size_t count, loff_t pos)
{
    loff_t p = pos;
    ssize_t rd;
    rd = kernel_read(file, buf, count, &p);
    if (rd < 0) return (int)rd;
    if ((size_t)rd != count) return -EIO;
    return 0;
}
```

- Handles negative `kernel_read` return (propagates errno).
- Handles short reads (returns -EIO).
- Uses local `loff_t` to avoid modifying caller's position.

**Minor**: Does not handle `count == 0` specially, but all callers pass non-zero counts, so this is fine.

---

## 8. `binfmt_win.c` Loading Flow

### Status: BUG

#### BUG-4: begin_new_exec() Called After PE Load But Before User Mapping
**Severity**: BUG  
**File**: `binfmt_win.c`, line 100-119

```c
ret = axon_pe_load(bprm, &mod);     // Load PE into kernel memory
ret = begin_new_exec(bprm);          // Replace current process image
// ...
entry_addr = axon_pe_map_user(mod);  // Map into user space
```

`begin_new_exec()` replaces the current process's address space. After this call, the old mm is gone. If `axon_pe_map_user()` then tries to access the PE data through the old mm, it would fault. The PE data must be preserved in kernel memory (via `mod`) independently of the process address space.

**Impact**: Potential crash during PE loading if kernel PE data is stored in the old address space.

#### BUG-5: Error Path Does Not Restore Process State
**Severity**: BUG  
**File**: `binfmt_win.c`

After `begin_new_exec()` succeeds but subsequent steps fail (e.g., `axon_pe_map_user` fails), the error path goes to `err_unload` which calls `axon_pe_unload(mod)`. However, `begin_new_exec()` has already replaced the process image — there's no way to cleanly roll back. The process is in a corrupted state.

**Impact**: Process left in undefined state on partial load failure.  
**Fix**: Move all validation and kernel-space loading before `begin_new_exec()`. Only call `begin_new_exec()` when everything else is ready.

---

## Summary Table

| ID | Severity | File | Description |
|----|----------|------|-------------|
| WARN-1 | WARNING | axon-winabi.h | `__packed` structs cause unaligned access on ARM64 |
| BUG-1 | BUG | pe-loader.c | `NumberOfRvaAndSizes` not clamped to `PE_DIR_MAX` |
| BUG-2 | BUG | pe-loader.c | Section `VirtualAddress`/`SizeOfRawData` not validated for overflow |
| WARN-2 | WARNING | pe-loader.c | No upfront file-size-vs-headers check |
| WARN-3 | WARNING | axon-winabi.h | No endianness conversion for big-endian targets |
| BUG-4 | BUG | binfmt_win.c | `begin_new_exec` ordering with PE user mapping |
| BUG-5 | BUG | binfmt_win.c | No rollback after `begin_new_exec` succeeds but later steps fail |
