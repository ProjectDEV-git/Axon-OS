/* axon-winabi.h — Core data structures for the Axon Windows ABI module.
 *
 * This module provides PE/COFF binary loading and NT syscall translation
 * so that Windows .exe files can run natively on the Axon OS Linux kernel.
 */

#ifndef _AXON_WINABI_H
#define _AXON_WINABI_H

#include <linux/types.h>
#include <linux/fs.h>
#include <linux/mm.h>
#include <linux/sched.h>
#include <linux/binfmts.h>

/* ── PE/COFF Constants ───────────────────────────────────────────────────── */

#define PE_MAGIC            0x00004550  /* "PE\0\0" */
#define MZ_MAGIC            0x5A4D      /* "MZ"     */
#define PE_OPT_MAGIC32      0x10B       /* PE32     */
#define PE_OPT_MAGIC64      0x20B       /* PE32+    */

/* PE machine types */
#define PE_MACHINE_I386     0x014C
#define PE_MACHINE_AMD64    0x8664
#define PE_MACHINE_ARM64    0xAA64

/* PE section flags */
#define PE_SCN_MEM_EXECUTE  0x20000000
#define PE_SCN_MEM_READ     0x40000000
#define PE_SCN_MEM_WRITE    0x80000000
#define PE_SCN_CNT_CODE     0x00000020
#define PE_SCN_CNT_INITDATA 0x00000040

/* PE directory entry indices */
#define PE_DIR_EXPORT       0
#define PE_DIR_IMPORT       1
#define PE_DIR_RESOURCE     2
#define PE_DIR_EXCEPTION    3
#define PE_DIR_SECURITY     4
#define PE_DIR_BASERELOC    5
#define PE_DIR_DEBUG        6
#define PE_DIR_TLS          9
#define PE_DIR_IAT          12
#define PE_DIR_DELAY_IMPORT 13
#define PE_DIR_CLR          14
#define PE_DIR_MAX          16

/* PE relocation types */
#define PE_REL_ABSOLUTE     0
#define PE_REL_HIGH         1
#define PE_REL_LOW          2
#define PE_REL_HIGHLOW      3
#define PE_REL_DIR64        10

/* ── PE Header Structures ────────────────────────────────────────────────── */

/* DOS header — every PE starts with this */
struct pe_dos_header {
    __u16 e_magic;      /* MZ_MAGIC */
    __u16 e_cblp;
    __u16 e_cp;
    __u16 e_crlc;
    __u16 e_cparhdr;
    __u16 e_minalloc;
    __u16 e_maxalloc;
    __u16 e_ss;
    __u16 e_sp;
    __u16 e_csum;
    __u16 e_ip;
    __u16 e_cs;
    __u16 e_lfarlc;
    __u16 e_ovno;
    __u16 e_res[4];
    __u16 e_oemid;
    __u16 e_oeminfo;
    __u16 e_res2[10];
    __s32 e_lfanew;     /* offset to PE header */
} __packed;

/* COFF file header */
struct pe_coff_header {
    __u16 Machine;
    __u16 NumberOfSections;
    __u32 TimeDateStamp;
    __u32 PointerToSymbolTable;
    __u32 NumberOfSymbols;
    __u16 SizeOfOptionalHeader;
    __u16 Characteristics;
} __packed;

/* Data directory entry */
struct pe_data_dir {
    __u32 VirtualAddress;
    __u32 Size;
} __packed;

/* Optional header (PE32+ / 64-bit) */
struct pe_optional_header64 {
    __u16 Magic;                    /* PE_OPT_MAGIC64 */
    __u8  MajorLinkerVersion;
    __u8  MinorLinkerVersion;
    __u32 SizeOfCode;
    __u32 SizeOfInitializedData;
    __u32 SizeOfUninitializedData;
    __u32 AddressOfEntryPoint;
    __u32 BaseOfCode;
    __u64 ImageBase;
    __u32 SectionAlignment;
    __u32 FileAlignment;
    __u16 MajorOperatingSystemVersion;
    __u16 MinorOperatingSystemVersion;
    __u16 MajorImageVersion;
    __u16 MinorImageVersion;
    __u16 MajorSubsystemVersion;
    __u16 MinorSubsystemVersion;
    __u32 Win32VersionValue;
    __u32 SizeOfImage;
    __u32 SizeOfHeaders;
    __u32 CheckSum;
    __u16 Subsystem;
    __u16 DllCharacteristics;
    __u64 SizeOfStackReserve;
    __u64 SizeOfStackCommit;
    __u64 SizeOfHeapReserve;
    __u64 SizeOfHeapCommit;
    __u32 LoaderFlags;
    __u32 NumberOfRvaAndSizes;
    struct pe_data_dir DataDirectory[PE_DIR_MAX];
} __packed;

/* Optional header (PE32 / 32-bit) */
struct pe_optional_header32 {
    __u16 Magic;                    /* PE_OPT_MAGIC32 */
    __u8  MajorLinkerVersion;
    __u8  MinorLinkerVersion;
    __u32 SizeOfCode;
    __u32 SizeOfInitializedData;
    __u32 SizeOfUninitializedData;
    __u32 AddressOfEntryPoint;
    __u32 BaseOfCode;
    __u32 BaseOfData;
    __u32 ImageBase;
    __u32 SectionAlignment;
    __u32 FileAlignment;
    __u16 MajorOperatingSystemVersion;
    __u16 MinorOperatingSystemVersion;
    __u16 MajorImageVersion;
    __u16 MinorImageVersion;
    __u16 MajorSubsystemVersion;
    __u16 MinorSubsystemVersion;
    __u32 Win32VersionValue;
    __u32 SizeOfImage;
    __u32 SizeOfHeaders;
    __u32 CheckSum;
    __u16 Subsystem;
    __u16 DllCharacteristics;
    __u32 SizeOfStackReserve;
    __u32 SizeOfStackCommit;
    __u32 SizeOfHeapReserve;
    __u32 SizeOfHeapCommit;
    __u32 LoaderFlags;
    __u32 NumberOfRvaAndSizes;
    struct pe_data_dir DataDirectory[PE_DIR_MAX];
} __packed;

/* Section header */
struct pe_section_header {
    char  Name[8];
    union {
        __u32 PhysicalAddress;
        __u32 VirtualSize;
    } Misc;
    __u32 VirtualAddress;
    __u32 SizeOfRawData;
    __u32 PointerToRawData;
    __u32 PointerToRelocations;
    __u32 PointerToLinenumbers;
    __u16 NumberOfRelocations;
    __u16 NumberOfLinenumbers;
    __u32 Characteristics;
} __packed;

/* Import directory entry */
struct pe_import_dir {
    __u32 ImportLookupTable;
    __u32 TimeDateStamp;
    __u32 ForwarderChain;
    __u32 Name;
    __u32 ImportAddressTable;
} __packed;

/* Base relocation block */
struct pe_base_reloc_block {
    __u32 PageRVA;
    __u32 BlockSize;
} __packed;

/* ── NT Syscall Definitions ──────────────────────────────────────────────── */

/* Maximum number of NT syscalls we handle */
#define NT_MAX_SYSCALLS 512

/* NT status codes */
#define NT_STATUS_SUCCESS           0x00000000
#define NT_STATUS_ACCESS_VIOLATION  0xC0000005
#define NT_STATUS_NO_MEMORY         0xC0000017
#define NT_STATUS_INVALID_PARAMETER 0xC000000D
#define NT_STATUS_END_OF_FILE       0xC0000011
#define NT_STATUS_NOT_IMPLEMENTED   0xC0000002

/* NT syscall handler signature:
 *   args: pointer to syscall arguments (register-passed on x86-64)
 *   Returns: NT status code
 */
typedef __u32 (*nt_syscall_handler_t)(const __u64 *args);

/* ── Loaded Module (PE image) ────────────────────────────────────────────── */

struct axon_pe_module {
    char name[256];             /* module filename */
    void *base;                 /* mmap'd base address in user space */
    __u64 image_base;           /* preferred base from PE header */
    __u32 size_of_image;        /* total virtual image size */
    __u32 entry_point_rva;      /* entry point relative to image base */
    __u16 machine;              /* PE_MACHINE_* */
    __u16 subsystem;            /* PE subsystem (CUI/GUI) */
    bool is_64bit;              /* PE32+ vs PE32 */
    struct file *file;          /* backing file */
};

/* ── Module-wide State ───────────────────────────────────────────────────── */

/* Per-process Windows ABI state (attached to task_struct via private_data) */
struct axon_task_state {
    struct axon_pe_module *module;  /* the loaded PE image */
    __u64 *syscall_args;            /* scratch buffer for syscall args */
    bool is_winabi;                 /* true if this task runs under ABI */
};

/* Core functions — axon-winabi.c */
extern struct axon_task_state *axon_get_task_state(struct task_struct *tsk);
extern int axon_task_state_alloc(pid_t pid);
extern void axon_task_state_free(pid_t pid);

/* Handle table — nt-sync.c */
#define AXON_HANDLE_EVENT     1
#define AXON_HANDLE_MUTANT    2
#define AXON_HANDLE_SEMAPHORE 3
#define AXON_HANDLE_SECTION   4
#define AXON_HANDLE_REGKEY    5
#define AXON_HANDLE_FILE      6
#define AXON_HANDLE_THREAD    7
#define AXON_HANDLE_PROCESS   8

int axon_handle_table_init(void);
void axon_handle_table_exit(void);
int axon_handle_alloc(int type, void *object);
void *axon_handle_lookup(int handle, int expected_type);
void axon_handle_free(int handle);

/* PE loader — pe-loader.c */
int axon_pe_validate(struct file *file);
int axon_pe_load(struct linux_binprm *bprm, struct axon_pe_module **out_mod);
void axon_pe_unload(struct axon_pe_module *mod);
unsigned long axon_pe_map_user(struct axon_pe_module *mod);

/* Syscall table — syscall_table.c */
int axon_syscall_table_init(void);
void axon_syscall_table_exit(void);
__u32 axon_dispatch_nt_syscall(__u32 syscall_nr, const __u64 *args);

/* Binfmt handler — binfmt_win.c */
int axon_binfmt_init(void);
void axon_binfmt_exit(void);

/* Registry — nt-registry.c */
int axon_registry_init(void);
void axon_registry_exit(void);
__u32 nt_registry_close(u32 handle);
bool nt_is_registry_handle(u32 handle);

/* Section (memory-mapped file) support — nt-section.c */
void axon_section_cleanup(void);

/* DLL loader — nt-dll-loader.c */
struct axon_loaded_dll;
int axon_dll_loader_init(void);
void axon_dll_loader_exit(void);
int axon_load_dll(const char *name, struct axon_loaded_dll **out);
u64 axon_resolve_import(const char *dll_name, const char *func_name);
u64 axon_resolve_import_ordinal(const char *dll_name, u32 ordinal);
int axon_resolve_pe_imports(struct axon_pe_module *mod);

/* NT syscalls — nt-syscalls.c */
__u32 nt_terminate_process(const __u64 *args);
__u32 nt_allocate_virtual_memory(const __u64 *args);
__u32 nt_free_virtual_memory(const __u64 *args);
__u32 nt_write_file(const __u64 *args);
__u32 nt_read_file(const __u64 *args);
__u32 nt_create_file(const __u64 *args);
__u32 nt_close(const __u64 *args);
__u32 nt_query_information_process(const __u64 *args);
__u32 nt_set_information_process(const __u64 *args);
__u32 nt_get_current_process_id(const __u64 *args);
__u32 nt_get_current_thread_id(const __u64 *args);
__u32 nt_get_tick_count(const __u64 *args);
__u32 nt_query_system_time(const __u64 *args);
__u32 nt_delay_execution(const __u64 *args);
__u32 nt_query_information_file(const __u64 *args);
__u32 nt_set_information_file(const __u64 *args);
__u32 nt_flush_buffers_file(const __u64 *args);
__u32 nt_create_section(const __u64 *args);
__u32 nt_open_section(const __u64 *args);
__u32 nt_map_view_of_section(const __u64 *args);
__u32 nt_unmap_view_of_section(const __u64 *args);
__u32 nt_query_section(const __u64 *args);
__u32 nt_query_virtual_memory(const __u64 *args);
__u32 nt_protect_virtual_memory(const __u64 *args);
__u32 nt_open_key(const __u64 *args);
__u32 nt_query_value_key(const __u64 *args);
__u32 nt_query_key(const __u64 *args);
__u32 nt_create_thread(const __u64 *args);
__u32 nt_create_thread_ex(const __u64 *args);
__u32 nt_open_thread(const __u64 *args);
__u32 nt_terminate_thread(const __u64 *args);
__u32 nt_suspend_thread(const __u64 *args);
__u32 nt_resume_thread(const __u64 *args);
__u32 nt_get_context_thread(const __u64 *args);
__u32 nt_set_context_thread(const __u64 *args);
__u32 nt_wait_for_single_object(const __u64 *args);
__u32 nt_set_event(const __u64 *args);
__u32 nt_reset_event(const __u64 *args);
__u32 nt_create_mutant(const __u64 *args);
__u32 nt_release_mutant(const __u64 *args);
__u32 nt_query_system_information(const __u64 *args);

/* NT syscalls — nt-sync.c */
__u32 nt_create_event(const __u64 *args);
__u32 nt_open_event(const __u64 *args);
__u32 nt_pulse_event(const __u64 *args);
__u32 nt_create_semaphore(const __u64 *args);
__u32 nt_release_semaphore(const __u64 *args);
__u32 nt_open_mutant(const __u64 *args);
__u32 nt_wait_for_multiple_objects(const __u64 *args);

#endif /* _AXON_WINABI_H */
