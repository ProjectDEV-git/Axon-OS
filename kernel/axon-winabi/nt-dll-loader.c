// nt-dll-loader.c — PE DLL loader for the Axon Windows ABI.
//
// Loads DLLs referenced by PE import directories, parses their exports,
// and patches the Import Address Table (IAT) in user-space executables.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/fs.h>
#include <linux/file.h>
#include <linux/mm.h>
#include <linux/uaccess.h>
#include <linux/mman.h>
#include <linux/vmalloc.h>
#include <linux/list.h>
#include <linux/hashtable.h>
#include <linux/string.h>
#include <linux/mutex.h>

#include "axon-winabi.h"

// ── Data Structures ───────────────────────────────────────────────────────────

#define DLL_HASH_BITS 6

struct axon_dll_export {
	char name[128];
	u32 ordinal;
	u64 address;
	struct hlist_node node;
};

struct axon_loaded_dll {
	char name[256];
	void *base;
	u64 image_base;
	u32 size_of_image;
	struct hlist_head exports[1 << DLL_HASH_BITS];
	struct list_head node;
};

#define DLL_SEARCH_MAX 4

struct dll_search_path {
	char path[256];
};

// ── Module State ──────────────────────────────────────────────────────────────

static LIST_HEAD(loaded_dlls);
static DEFINE_MUTEX(dll_lock);
static struct dll_search_path search_paths[DLL_SEARCH_MAX];
static int search_path_count;

// ── Forward Declarations ──────────────────────────────────────────────────────

static int __axon_load_dll(const char *name, struct axon_loaded_dll **out);
static u64 __axon_resolve_import(const char *dll_name, const char *func_name);
static u64 __axon_resolve_import_ordinal(const char *dll_name, u32 ordinal);
static void __apply_relocs(void *reloc_buf, u32 reloc_size, void *base,
			   s64 delta, bool is_64bit);

// ── Helpers ───────────────────────────────────────────────────────────────────

static int dll_pe_read_at(struct file *file, void *buf, size_t count,
			  loff_t pos)
{
	loff_t p = pos;
	ssize_t rd;

	rd = kernel_read(file, buf, count, &p);
	if (rd < 0)
		return (int)rd;
	if ((size_t)rd != count)
		return -EIO;
	return 0;
}

static inline u32 djb2_hash(const char *str)
{
	u32 hash = 5381;
	int c;

	while ((c = *str++))
		hash = ((hash << 5) + hash) + c;
	return hash;
}

// ── Relocation Application (kernel-buffer variant) ────────────────────────────

static void __apply_relocs(void *reloc_buf, u32 reloc_size, void *base,
			   s64 delta, bool is_64bit)
{
	u32 off = 0;

	while (off + sizeof(struct pe_base_reloc_block) <= reloc_size) {
		struct pe_base_reloc_block *blk;
		u32 blk_size, num_entries, page_rva, j;

		blk = (struct pe_base_reloc_block *)(reloc_buf + off);
		blk_size = blk->BlockSize;
		page_rva = blk->PageRVA;

		if (blk_size < sizeof(*blk) || off + blk_size > reloc_size)
			break;

		num_entries = (blk_size - sizeof(*blk)) / sizeof(u16);

		for (j = 0; j < num_entries; j++) {
			u16 entry = ((u16 *)(blk + 1))[j];
			u16 type = entry >> 12;
			u16 rva_off = entry & 0x0FFF;
			void *patch = base + page_rva + rva_off;

			if (type == PE_REL_ABSOLUTE)
				continue;

			if (type == PE_REL_DIR64 && is_64bit)
				*(u64 *)patch += (u64)delta;
			else if (type == PE_REL_HIGHLOW && !is_64bit)
				*(u32 *)patch += (u32)delta;
			else if (type == PE_REL_HIGH)
				*(u16 *)patch += (u16)((u32)delta >> 16);
			else if (type == PE_REL_LOW)
				*(u16 *)patch += (u16)delta;
		}

		off += blk_size;
	}
}

// ── Search Path Init ──────────────────────────────────────────────────────────

static void init_search_paths(void)
{
	strscpy(search_paths[0].path, "/usr/lib/axon-winabi/dlls/",
		sizeof(search_paths[0].path));
	strscpy(search_paths[1].path, "./", sizeof(search_paths[1].path));
	strscpy(search_paths[2].path, "/usr/lib/axon-winabi/dlls/system32/",
		sizeof(search_paths[2].path));
	strscpy(search_paths[3].path, "/usr/lib/axon-winabi/dlls/",
		sizeof(search_paths[3].path));
	search_path_count = 4;
}

static void preload_builtins(void)
{
	static const char * const builtins[] = {
		"kernel32.dll", "ntdll.dll", "user32.dll",
		"gdi32.dll", "advapi32.dll", "msvcrt.dll", NULL
	};
	char fullpath[512];
	struct file *f;
	struct axon_loaded_dll *dll;
	int i, j;

	mutex_lock(&dll_lock);

	for (i = 0; builtins[i]; i++) {
		for (j = 0; j < search_path_count; j++) {
			snprintf(fullpath, sizeof(fullpath), "%s%s",
				 search_paths[j].path, builtins[i]);

			f = filp_open(fullpath, O_RDONLY, 0);
			if (IS_ERR(f))
				continue;
			fput(f);

			if (__axon_load_dll(builtins[i], &dll) == 0)
				pr_info("pre-loaded built-in: %s\n",
					builtins[i]);
			break;
		}
	}

	mutex_unlock(&dll_lock);
}

// ── Init / Exit ───────────────────────────────────────────────────────────────

int axon_dll_loader_init(void)
{
	pr_info("DLL loader initializing\n");

	init_search_paths();
	preload_builtins();

	pr_info("DLL loader ready (%d search paths)\n", search_path_count);
	return 0;
}

void axon_dll_loader_exit(void)
{
	struct axon_loaded_dll *dll, *tmp;
	struct axon_dll_export *exp;
	struct hlist_node *tmp2;
	u32 bkt;

	mutex_lock(&dll_lock);

	list_for_each_entry_safe(dll, tmp, &loaded_dlls, node) {
		pr_info("unloading DLL: %s\n", dll->name);

		hash_for_each_safe(dll->exports, bkt, tmp2, exp, node) {
			hash_del(&exp->node);
			kfree(exp);
		}

		vfree(dll->base);
		list_del(&dll->node);
		kfree(dll);
	}

	mutex_unlock(&dll_lock);
	pr_info("DLL loader unloaded\n");
}

// ── Export Directory Parsing ──────────────────────────────────────────────────

struct pe_export_dir {
	u32 Characteristics;
	u32 TimeDateStamp;
	u16 MajorVersion;
	u16 MinorVersion;
	u32 Name;
	u32 Base;
	u32 NumberOfFunctions;
	u32 NumberOfNames;
	u32 AddressOfFunctions;
	u32 AddressOfNames;
	u32 AddressOfNameOrdinals;
} __packed;

static int pe_parse_exports(struct file *file,
			    struct pe_optional_header64 *opt64,
			    struct pe_optional_header32 *opt32,
			    bool is_64bit, struct axon_loaded_dll *dll)
{
	struct pe_export_dir exp_dir;
	u32 export_rva = 0, export_size = 0;
	u32 *name_rvas = NULL;
	u32 *func_rvas = NULL;
	u16 *ordinals = NULL;
	u32 i;
	int ret;

	if (is_64bit) {
		if (opt64->NumberOfRvaAndSizes > PE_DIR_EXPORT) {
			export_rva = opt64->DataDirectory[PE_DIR_EXPORT]
					     .VirtualAddress;
			export_size = opt64->DataDirectory[PE_DIR_EXPORT]
					      .Size;
		}
	} else {
		if (opt32->NumberOfRvaAndSizes > PE_DIR_EXPORT) {
			export_rva = opt32->DataDirectory[PE_DIR_EXPORT]
					     .VirtualAddress;
			export_size = opt32->DataDirectory[PE_DIR_EXPORT]
					      .Size;
		}
	}

	if (!export_rva || !export_size) {
		pr_warn("%s: no export directory\n", dll->name);
		return 0;
	}

	ret = dll_pe_read_at(file, &exp_dir, sizeof(exp_dir), export_rva);
	if (ret) {
		pr_err("%s: failed to read export dir\n", dll->name);
		return ret;
	}

	pr_info("%s: %u exports, %u names, base=%u\n", dll->name,
		exp_dir.NumberOfFunctions, exp_dir.NumberOfNames, exp_dir.Base);

	// Read name RVAs and ordinal table
	if (exp_dir.AddressOfNames && exp_dir.NumberOfNames > 0) {
		name_rvas = kvmalloc(exp_dir.NumberOfNames * sizeof(u32),
				     GFP_KERNEL);
		ordinals = kvmalloc(exp_dir.NumberOfNames * sizeof(u16),
				    GFP_KERNEL);
		if (!name_rvas || !ordinals)
			goto enomem;

		ret = dll_pe_read_at(file, name_rvas,
				     exp_dir.NumberOfNames * sizeof(u32),
				     exp_dir.AddressOfNames);
		if (ret)
			goto out;

		ret = dll_pe_read_at(file, ordinals,
				     exp_dir.NumberOfNames * sizeof(u16),
				     exp_dir.AddressOfNameOrdinals);
		if (ret)
			goto out;
	}

	// Read function RVA table
	if (exp_dir.NumberOfFunctions > 0) {
		func_rvas = kvmalloc(exp_dir.NumberOfFunctions * sizeof(u32),
				     GFP_KERNEL);
		if (!func_rvas)
			goto enomem;

		ret = dll_pe_read_at(file, func_rvas,
				     exp_dir.NumberOfFunctions * sizeof(u32),
				     exp_dir.AddressOfFunctions);
		if (ret)
			goto out;
	}

	// Add named exports to hash table
	if (name_rvas) {
		for (i = 0; i < exp_dir.NumberOfNames; i++) {
			struct axon_dll_export *exp;
			char name_buf[128];
			u32 func_idx;

			memset(name_buf, 0, sizeof(name_buf));
			if (dll_pe_read_at(file, name_buf,
					   sizeof(name_buf) - 1,
					   name_rvas[i]))
				continue;

			func_idx = ordinals[i];
			if (func_idx >= exp_dir.NumberOfFunctions)
				continue;

			exp = kzalloc(sizeof(*exp), GFP_KERNEL);
			if (!exp)
				goto enomem;

			strscpy(exp->name, name_buf, sizeof(exp->name));
			exp->ordinal = func_idx + exp_dir.Base;
			exp->address = func_rvas[func_idx];
			hash_add(dll->exports, &exp->node,
				 djb2_hash(exp->name));
		}
	}

	// Add ordinal-only exports (not already covered by name)
	if (func_rvas) {
		for (i = 0; i < exp_dir.NumberOfFunctions; i++) {
			struct axon_dll_export *exp;
			u32 j;
			bool by_name = false;

			if (!func_rvas[i])
				continue;

			if (name_rvas) {
				for (j = 0; j < exp_dir.NumberOfNames; j++) {
					if (ordinals[j] == i) {
						by_name = true;
						break;
					}
				}
			}
			if (by_name)
				continue;

			exp = kzalloc(sizeof(*exp), GFP_KERNEL);
			if (!exp)
				goto enomem;

			exp->ordinal = i + exp_dir.Base;
			exp->address = func_rvas[i];
			snprintf(exp->name, sizeof(exp->name),
				 "ordinal_%u", exp->ordinal);
			hash_add(dll->exports, &exp->node,
				 djb2_hash(exp->name));
		}
	}

	ret = 0;
	goto out;

enomem:
	ret = -ENOMEM;
out:
	kvfree(name_rvas);
	kvfree(func_rvas);
	kvfree(ordinals);
	return ret;
}

// ── DLL File Search ───────────────────────────────────────────────────────────

static struct file *dll_search_file(const char *name)
{
	char fullpath[512];
	struct file *f;
	int i;

	for (i = 0; i < search_path_count; i++) {
		snprintf(fullpath, sizeof(fullpath), "%s%s",
			 search_paths[i].path, name);

		f = filp_open(fullpath, O_RDONLY, 0);
		if (!IS_ERR(f))
			return f;
	}
	return ERR_PTR(-ENOENT);
}

// ── DLL Header Reading ────────────────────────────────────────────────────────

static int dll_read_headers(struct file *file, struct pe_dos_header *dos,
			    struct pe_coff_header *coff,
			    struct pe_optional_header64 *opt64,
			    struct pe_optional_header32 *opt32, bool *is_64bit)
{
	u32 pe_off;
	u16 opt_magic;
	int ret;

	ret = dll_pe_read_at(file, dos, sizeof(*dos), 0);
	if (ret)
		return ret;

	if (dos->e_magic != MZ_MAGIC)
		return -ENOEXEC;

	pe_off = dos->e_lfanew;
	if (pe_off < sizeof(*dos) || pe_off > 0x10000)
		return -ENOEXEC;

	ret = dll_pe_read_at(file, coff, sizeof(*coff),
			     pe_off + sizeof(u32));
	if (ret)
		return ret;

	ret = dll_pe_read_at(file, &opt_magic, sizeof(opt_magic),
			     pe_off + sizeof(u32) + sizeof(*coff));
	if (ret)
		return ret;

	if (opt_magic == PE_OPT_MAGIC64) {
		ret = dll_pe_read_at(file, opt64, sizeof(*opt64),
				     pe_off + sizeof(u32) + sizeof(*coff));
		if (ret)
			return ret;
		*is_64bit = true;
	} else if (opt_magic == PE_OPT_MAGIC32) {
		ret = dll_pe_read_at(file, opt32, sizeof(*opt32),
				     pe_off + sizeof(u32) + sizeof(*coff));
		if (ret)
			return ret;
		*is_64bit = false;
	} else {
		return -ENOEXEC;
	}

	return 0;
}

// ── DLL Loading (caller must hold dll_lock) ───────────────────────────────────

static struct axon_loaded_dll *find_dll(const char *name)
{
	struct axon_loaded_dll *dll;

	list_for_each_entry(dll, &loaded_dlls, node) {
		if (strcasecmp(dll->name, name) == 0)
			return dll;
	}
	return NULL;
}

static int __axon_load_dll(const char *name, struct axon_loaded_dll **out)
{
	struct file *file = NULL;
	struct pe_dos_header dos;
	struct pe_coff_header coff;
	struct pe_optional_header64 opt64 = { 0 };
	struct pe_optional_header32 opt32 = { 0 };
	struct pe_section_header *sects = NULL;
	struct axon_loaded_dll *dll = NULL;
	bool is_64bit = false;
	u64 image_base;
	u32 size_of_image, section_align;
	u32 section_hdr_off;
	u16 num_sects, i;
	int ret;

	if (!name || !out)
		return -EINVAL;

	// Check if already loaded
	dll = find_dll(name);
	if (dll) {
		*out = dll;
		return 0;
	}

	file = dll_search_file(name);
	if (IS_ERR(file)) {
		pr_err("DLL not found: %s\n", name);
		return -ENOENT;
	}

	ret = dll_read_headers(file, &dos, &coff, &opt64, &opt32, &is_64bit);
	if (ret) {
		pr_err("DLL header parse failed: %s\n", name);
		goto err;
	}

	num_sects = coff.NumberOfSections;

	if (is_64bit) {
		image_base = opt64.ImageBase;
		size_of_image = opt64.SizeOfImage;
		section_align = opt64.SectionAlignment;
	} else {
		image_base = opt32.ImageBase;
		size_of_image = opt32.SizeOfImage;
		section_align = opt32.SectionAlignment;
	}

	if (size_of_image == 0 || size_of_image > 0x40000000U) {
		ret = -ENOEXEC;
		goto err;
	}

	sects = kcalloc(num_sects, sizeof(*sects), GFP_KERNEL);
	if (!sects) {
		ret = -ENOMEM;
		goto err;
	}

	section_hdr_off = dos.e_lfanew + sizeof(u32) + sizeof(coff) +
			  coff.SizeOfOptionalHeader;
	ret = dll_pe_read_at(file, sects,
			     num_sects * sizeof(*sects), section_hdr_off);
	if (ret)
		goto err;

	dll = kzalloc(sizeof(*dll), GFP_KERNEL);
	if (!dll) {
		ret = -ENOMEM;
		goto err;
	}

	strscpy(dll->name, name, sizeof(dll->name));
	dll->image_base = image_base;
	dll->size_of_image = PAGE_ALIGN(size_of_image);
	hash_init(dll->exports);

	// Allocate kernel VA for the DLL image
	dll->base = vmalloc(dll->size_of_image);
	if (!dll->base) {
		ret = -ENOMEM;
		goto err;
	}
	memset(dll->base, 0, dll->size_of_image);

	// Copy sections from file into kernel buffer
	for (i = 0; i < num_sects; i++) {
		struct pe_section_header *s = &sects[i];
		u64 sec_va = ALIGN(s->VirtualAddress, section_align);
		u64 sec_size = max_t(u32, s->Misc.VirtualSize,
				     s->SizeOfRawData);

		if (sec_va + sec_size > dll->size_of_image)
			continue;
		if (s->SizeOfRawData == 0 || s->PointerToRawData == 0)
			continue;

		ret = dll_pe_read_at(file, dll->base + sec_va,
				     min_t(u64, s->SizeOfRawData, sec_size),
				     s->PointerToRawData);
		if (ret)
			pr_warn("DLL %s: section %.8s read failed\n",
				name, s->Name);
	}

	// Apply base relocations
	{
		u64 actual_base = (u64)(unsigned long)dll->base;
		s64 delta = (s64)(actual_base - image_base);

		if (delta != 0) {
			u32 reloc_rva = 0, reloc_size = 0;
			struct pe_data_dir *dd;

			if (is_64bit) {
				dd = opt64.DataDirectory;
				if (opt64.NumberOfRvaAndSizes > PE_DIR_BASERELOC) {
					reloc_rva = dd[PE_DIR_BASERELOC].VirtualAddress;
					reloc_size = dd[PE_DIR_BASERELOC].Size;
				}
			} else {
				dd = opt32.DataDirectory;
				if (opt32.NumberOfRvaAndSizes > PE_DIR_BASERELOC) {
					reloc_rva = dd[PE_DIR_BASERELOC].VirtualAddress;
					reloc_size = dd[PE_DIR_BASERELOC].Size;
				}
			}

			if (reloc_rva && reloc_size &&
			    reloc_rva + reloc_size <= dll->size_of_image) {
				void *reloc_buf;

				reloc_buf = kvmalloc(reloc_size, GFP_KERNEL);
				if (reloc_buf) {
					memcpy(reloc_buf,
					       dll->base + reloc_rva,
					       reloc_size);
					__apply_relocs(reloc_buf, reloc_size,
						       dll->base, delta,
						       is_64bit);
					kvfree(reloc_buf);
				}
			}
		}
	}

	// Parse export directory
	ret = pe_parse_exports(file, &opt64, &opt32, is_64bit, dll);
	if (ret)
		pr_warn("DLL %s: export parse had errors\n", name);

	// Call DllMain if present and DLL has a valid entry point
	{
		u32 entry_rva;

		entry_rva = is_64bit ? opt64.AddressOfEntryPoint :
				       opt32.AddressOfEntryPoint;

		if (entry_rva && (coff.Characteristics & 0x2000)) {
			bool (*dll_main)(void *, u32, void *);

			dll_main = dll->base + entry_rva;
			if (!dll_main(NULL, 1, NULL))
				pr_warn("DLL %s: DllMain returned false\n",
					name);
		}
	}

	list_add(&dll->node, &loaded_dlls);
	*out = dll;
	kfree(sects);
	fput(file);

	pr_info("DLL loaded: %s base=%p size=0x%x\n",
		dll->name, dll->base, dll->size_of_image);
	return 0;

err:
	if (dll) {
		vfree(dll->base);
		kfree(dll);
	}
	kfree(sects);
	if (!IS_ERR_OR_NULL(file))
		fput(file);
	return ret;
}

// ── Public DLL Load (acquires dll_lock) ───────────────────────────────────────

int axon_load_dll(const char *name, struct axon_loaded_dll **out)
{
	int ret;

	mutex_lock(&dll_lock);
	ret = __axon_load_dll(name, out);
	mutex_unlock(&dll_lock);
	return ret;
}

// ── Import Resolution (caller must hold dll_lock) ─────────────────────────────

static u64 __axon_resolve_import(const char *dll_name, const char *func_name)
{
	struct axon_loaded_dll *dll;
	struct axon_dll_export *exp;
	u32 hash;

	if (!dll_name || !func_name)
		return 0;

	dll = find_dll(dll_name);
	if (!dll) {
		if (__axon_load_dll(dll_name, &dll) != 0)
			return 0;
	}

	hash = djb2_hash(func_name);
	hash_for_each_possible(dll->exports, exp, node, hash) {
		if (strcmp(exp->name, func_name) == 0)
			return (u64)(unsigned long)dll->base + exp->address;
	}

	pr_err("import not found: %s!%s\n", dll_name, func_name);
	return 0;
}

static u64 __axon_resolve_import_ordinal(const char *dll_name, u32 ordinal)
{
	struct axon_loaded_dll *dll;
	struct axon_dll_export *exp;
	u32 bkt;

	if (!dll_name)
		return 0;

	dll = find_dll(dll_name);
	if (!dll) {
		if (__axon_load_dll(dll_name, &dll) != 0)
			return 0;
	}

	hash_for_each(dll->exports, bkt, exp, node) {
		if (exp->ordinal == ordinal)
			return (u64)(unsigned long)dll->base + exp->address;
	}

	pr_err("ordinal not found: %s!%u\n", dll_name, ordinal);
	return 0;
}

// ── Public Import Resolution (acquires dll_lock) ──────────────────────────────

u64 axon_resolve_import(const char *dll_name, const char *func_name)
{
	u64 addr;

	mutex_lock(&dll_lock);
	addr = __axon_resolve_import(dll_name, func_name);
	mutex_unlock(&dll_lock);
	return addr;
}

u64 axon_resolve_import_ordinal(const char *dll_name, u32 ordinal)
{
	u64 addr;

	mutex_lock(&dll_lock);
	addr = __axon_resolve_import_ordinal(dll_name, ordinal);
	mutex_unlock(&dll_lock);
	return addr;
}

// ── IAT Patching (caller must hold dll_lock) ──────────────────────────────────

static int patch_iat(struct axon_pe_module *mod, const char *dll_name,
		     u64 *thunk_array, u64 *iat_array, u32 num_thunks,
		     bool is_64bit)
{
	u32 i;

	for (i = 0; i < num_thunks; i++) {
		u64 thunk_val = thunk_array[i];
		u64 resolved = 0;

		if (!thunk_val)
			break;

		if (is_64bit) {
			if (thunk_val & (1ULL << 63)) {
				resolved = __axon_resolve_import_ordinal(
					dll_name, thunk_val & 0xFFFF);
			} else {
				char name_buf[256];
				u32 hint_rva = (u32)thunk_val;

				memset(name_buf, 0, sizeof(name_buf));
				if (copy_from_user(name_buf,
					(void __user *)((unsigned long)mod->base +
							hint_rva + 2),
					sizeof(name_buf) - 1))
					continue;

				resolved = __axon_resolve_import(dll_name,
								 name_buf);
			}
		} else {
			u32 thunk32 = (u32)thunk_val;

			if (thunk32 & (1U << 31)) {
				resolved = __axon_resolve_import_ordinal(
					dll_name, thunk32 & 0xFFFF);
			} else {
				char name_buf[256];

				memset(name_buf, 0, sizeof(name_buf));
				if (copy_from_user(name_buf,
					(void __user *)((unsigned long)mod->base +
							thunk32 + 2),
					sizeof(name_buf) - 1))
					continue;

				resolved = __axon_resolve_import(dll_name,
								 name_buf);
			}
		}

		if (resolved) {
			if (put_user(resolved, (u64 __user *)&iat_array[i])) {
				pr_err("IAT patch failed at index %u\n", i);
				return -EFAULT;
			}
		} else {
			pr_warn("unresolved: %s thunk[%u]\n", dll_name, i);
		}
	}

	return 0;
}

// ── PE Import Resolution ──────────────────────────────────────────────────────

int axon_resolve_pe_imports(struct axon_pe_module *mod)
{
	struct pe_dos_header dos;
	struct pe_optional_header64 opt64 = { 0 };
	struct pe_optional_header32 opt32 = { 0 };
	struct pe_import_dir *imports = NULL;
	char *dll_name_buf = NULL;
	u32 import_rva = 0, import_size = 0;
	u32 num_imports, thunk_size;
	u64 *thunk_buf = NULL;
	u64 *iat_buf = NULL;
	bool is_64bit;
	u32 i;
	int ret;

	if (!mod || !mod->file || !mod->base)
		return -EINVAL;

	ret = dll_pe_read_at(mod->file, &dos, sizeof(dos), 0);
	if (ret)
		return ret;

	is_64bit = mod->is_64bit;

	if (is_64bit) {
		ret = dll_pe_read_at(mod->file, &opt64, sizeof(opt64),
				     dos.e_lfanew + sizeof(u32) +
				     sizeof(struct pe_coff_header));
		if (ret)
			return ret;

		if (opt64.NumberOfRvaAndSizes > PE_DIR_IMPORT) {
			import_rva = opt64.DataDirectory[PE_DIR_IMPORT]
					     .VirtualAddress;
			import_size = opt64.DataDirectory[PE_DIR_IMPORT]
					      .Size;
		}
		thunk_size = sizeof(u64);
	} else {
		ret = dll_pe_read_at(mod->file, &opt32, sizeof(opt32),
				     dos.e_lfanew + sizeof(u32) +
				     sizeof(struct pe_coff_header));
		if (ret)
			return ret;

		if (opt32.NumberOfRvaAndSizes > PE_DIR_IMPORT) {
			import_rva = opt32.DataDirectory[PE_DIR_IMPORT]
					     .VirtualAddress;
			import_size = opt32.DataDirectory[PE_DIR_IMPORT]
					      .Size;
		}
		thunk_size = sizeof(u32);
	}

	if (!import_rva || !import_size) {
		pr_info("%s: no imports\n", mod->name);
		return 0;
	}

	num_imports = import_size / sizeof(struct pe_import_dir);
	if (!num_imports || num_imports > 512)
		return -EINVAL;

	imports = kvmalloc(num_imports * sizeof(*imports), GFP_KERNEL);
	dll_name_buf = kvmalloc(256, GFP_KERNEL);
	thunk_buf = kvmalloc(256 * thunk_size, GFP_KERNEL);
	iat_buf = kvmalloc(256 * sizeof(u64), GFP_KERNEL);

	if (!imports || !dll_name_buf || !thunk_buf || !iat_buf) {
		ret = -ENOMEM;
		goto out;
	}

	ret = dll_pe_read_at(mod->file, imports,
			     num_imports * sizeof(*imports), import_rva);
	if (ret)
		goto out;

	mutex_lock(&dll_lock);

	for (i = 0; i < num_imports; i++) {
		struct pe_import_dir *imp = &imports[i];
		u32 thunk_rva, iat_rva, num_thunks = 0;
		unsigned long iat_user;
		u32 j;

		if (!imp->Name)
			break;

		memset(dll_name_buf, 0, 256);
		if (dll_pe_read_at(mod->file, dll_name_buf, 255, imp->Name))
			continue;

		thunk_rva = imp->ImportLookupTable ? imp->ImportLookupTable :
						     imp->ImportAddressTable;
		iat_rva = imp->ImportAddressTable;
		if (!thunk_rva || !iat_rva)
			continue;

		memset(thunk_buf, 0, 256 * thunk_size);
		if (dll_pe_read_at(mod->file, thunk_buf,
				   256 * thunk_size, thunk_rva))
			continue;

		for (j = 0; j < 256; j++) {
			if (is_64bit) {
				if (!thunk_buf[j])
					break;
			} else {
				if (!((u32 *)thunk_buf)[j])
					break;
			}
			num_thunks++;
		}
		if (!num_thunks)
			continue;

		// Ensure the DLL is loaded
		if (!find_dll(dll_name_buf)) {
			struct axon_loaded_dll *dummy;

			if (__axon_load_dll(dll_name_buf, &dummy) != 0) {
				pr_err("%s: failed to load DLL %s\n",
				       mod->name, dll_name_buf);
				continue;
			}
		}

		// Read current IAT contents from user space
		iat_user = (unsigned long)mod->base + iat_rva;
		memset(iat_buf, 0, 256 * sizeof(u64));
		if (copy_from_user(iat_buf, (void __user *)iat_user,
				   num_thunks * thunk_size)) {
			pr_err("%s: failed to read IAT for %s\n",
			       mod->name, dll_name_buf);
			continue;
		}

		ret = patch_iat(mod, dll_name_buf, thunk_buf, iat_buf,
				num_thunks, is_64bit);
		if (ret)
			pr_warn("%s: IAT patch errors for %s\n",
				mod->name, dll_name_buf);
		else
			pr_info("%s: resolved imports from %s (%u thunks)\n",
				mod->name, dll_name_buf, num_thunks);
	}

	mutex_unlock(&dll_lock);
	ret = 0;
out:
	kvfree(iat_buf);
	kvfree(thunk_buf);
	kvfree(dll_name_buf);
	kvfree(imports);
	return ret;
}
