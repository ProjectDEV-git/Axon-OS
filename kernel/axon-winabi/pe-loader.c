// pe-loader.c — PE/COFF binary loader for the Axon Windows ABI.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/fs.h>
#include <linux/file.h>
#include <linux/mm.h>
#include <linux/binfmts.h>
#include <linux/uaccess.h>
#include <linux/elf.h>
#include <linux/mman.h>
#include <linux/string.h>

#include "axon-winabi.h"

// ── Helpers ───────────────────────────────────────────────────────────────────

static int pe_read_at(struct file *file, void *buf, size_t count, loff_t pos)
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

static int pe_section_prot(__u32 flags)
{
	int prot = 0;

	if (flags & PE_SCN_MEM_READ)
		prot |= PROT_READ;
	if (flags & PE_SCN_MEM_WRITE)
		prot |= PROT_WRITE;
	if (flags & PE_SCN_MEM_EXECUTE)
		prot |= PROT_EXEC;
	if (!prot)
		prot = PROT_READ;
	return prot;
}

// ── Validate ─────────────────────────────────────────────────────────────────

int axon_pe_validate(struct file *file)
{
	struct pe_dos_header dos;
	struct pe_coff_header coff;
	__u32 pe_sig;
	loff_t pe_off;
	int ret;

	if (!file)
		return -ENOEXEC;

	ret = pe_read_at(file, &dos, sizeof(dos), 0);
	if (ret) {
		pr_err("failed to read DOS header\n");
		return -ENOEXEC;
	}

	if (dos.e_magic != MZ_MAGIC) {
		pr_err("bad MZ magic: 0x%04x\n", dos.e_magic);
		return -ENOEXEC;
	}

	pe_off = dos.e_lfanew;
	if (pe_off < (loff_t)sizeof(dos) || pe_off > 0x10000) {
		pr_err("invalid PE offset: %lld\n", pe_off);
		return -ENOEXEC;
	}

	ret = pe_read_at(file, &pe_sig, sizeof(pe_sig), pe_off);
	if (ret || pe_sig != PE_MAGIC) {
		pr_err("bad PE magic: 0x%08x\n", pe_sig);
		return -ENOEXEC;
	}

	ret = pe_read_at(file, &coff, sizeof(coff),
			  pe_off + (__u32)sizeof(pe_sig));
	if (ret) {
		pr_err("failed to read COFF header\n");
		return -ENOEXEC;
	}

	if (coff.Machine != PE_MACHINE_AMD64 &&
	    coff.Machine != PE_MACHINE_I386) {
		pr_err("unsupported machine type: 0x%04x\n", coff.Machine);
		return -ENOEXEC;
	}

	if (coff.NumberOfSections == 0 || coff.NumberOfSections > 96) {
		pr_err("invalid section count: %u\n", coff.NumberOfSections);
		return -ENOEXEC;
	}

	pr_info("validated PE: machine=0x%04x sections=%u\n",
		coff.Machine, coff.NumberOfSections);
	return 0;
}

// ── Load ─────────────────────────────────────────────────────────────────────

static int pe_read_headers(struct file *file,
			   struct pe_dos_header *dos,
			   struct pe_coff_header *coff,
			   struct pe_optional_header64 *opt64,
			   struct pe_optional_header32 *opt32,
			   bool *is_64bit)
{
	__u32 pe_off;
	__u16 opt_magic;
	int ret;

	ret = pe_read_at(file, dos, sizeof(*dos), 0);
	if (ret)
		return ret;

	pe_off = dos->e_lfanew;
	ret = pe_read_at(file, coff, sizeof(*coff),
			  pe_off + sizeof(__u32));
	if (ret)
		return ret;

	ret = pe_read_at(file, &opt_magic, sizeof(opt_magic),
			  pe_off + sizeof(__u32) + sizeof(*coff));
	if (ret)
		return ret;

	if (opt_magic == PE_OPT_MAGIC64) {
		if (coff->SizeOfOptionalHeader < sizeof(*opt64))
			return -ENOEXEC;
		ret = pe_read_at(file, opt64, sizeof(*opt64),
				  pe_off + sizeof(__u32) + sizeof(*coff));
		if (ret)
			return ret;
		*is_64bit = true;
	} else if (opt_magic == PE_OPT_MAGIC32) {
		if (coff->SizeOfOptionalHeader < sizeof(*opt32))
			return -ENOEXEC;
		ret = pe_read_at(file, opt32, sizeof(*opt32),
				  pe_off + sizeof(__u32) + sizeof(*coff));
		if (ret)
			return ret;
		*is_64bit = false;
	} else {
		pr_err("unknown optional header magic: 0x%04x\n", opt_magic);
		return -ENOEXEC;
	}

	return 0;
}

static int pe_read_section_headers(struct file *file,
				   struct pe_dos_header *dos,
				   __u16 num_sections,
				   struct pe_section_header **out)
{
	__u32 pe_off = dos->e_lfanew;
	__u32 coff_off = pe_off + sizeof(__u32);
	struct pe_coff_header coff;
	struct pe_section_header *sects;
	size_t table_off;
	int ret;

	ret = pe_read_at(file, &coff, sizeof(coff), coff_off);
	if (ret)
		return ret;

	table_off = coff_off + sizeof(coff) + coff.SizeOfOptionalHeader;
	sects = kcalloc(num_sections, sizeof(*sects), GFP_KERNEL);
	if (!sects)
		return -ENOMEM;

	ret = pe_read_at(file, sects,
			  num_sections * sizeof(*sects), table_off);
	if (ret) {
		kfree(sects);
		return ret;
	}

	*out = sects;
	return 0;
}

static int pe_map_sections(struct file *file,
			   struct pe_section_header *sects,
			   __u16 num_sections,
			   struct axon_pe_module *mod)
{
	unsigned long base;
	__u64 image_base;
	__u32 section_align;
	__u32 file_align;
	__u16 i;

	if (mod->is_64bit) {
		struct pe_optional_header64 opt64;
		struct pe_dos_header dos;
		__u32 pe_off;

		pe_read_at(file, &dos, sizeof(dos), 0);
		pe_off = dos.e_lfanew;
		pe_read_at(file, &opt64, sizeof(opt64),
			   pe_off + sizeof(__u32) + sizeof(struct pe_coff_header));
		image_base = opt64.ImageBase;
		section_align = opt64.SectionAlignment;
		file_align = opt64.FileAlignment;
	} else {
		struct pe_optional_header32 opt32;
		struct pe_dos_header dos;
		__u32 pe_off;

		pe_read_at(file, &dos, sizeof(dos), 0);
		pe_off = dos.e_lfanew;
		pe_read_at(file, &opt32, sizeof(opt32),
			   pe_off + sizeof(__u32) + sizeof(struct pe_coff_header));
		image_base = opt32.ImageBase;
		section_align = opt32.SectionAlignment;
		file_align = opt32.FileAlignment;
	}

	mod->image_base = image_base;

	base = (unsigned long)vm_mmap(NULL, 0,
			mod->size_of_image,
			PROT_NONE,
			MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE,
			0);
	if (IS_ERR_VALUE(base)) {
		pr_err("failed to allocate user VA for image: %ld\n", base);
		return (int)base;
	}

	mod->base = (void *)base;

	for (i = 0; i < num_sections; i++) {
		struct pe_section_header *s = &sects[i];
		unsigned long sec_addr;
		size_t map_size;
		int prot;
		void *p;

		if (s->SizeOfRawData == 0 && s->Misc.VirtualSize == 0)
			continue;

		sec_addr = base + ALIGN(s->VirtualAddress, section_align);
		map_size = ALIGN(max_t(__u32, s->Misc.VirtualSize,
					s->SizeOfRawData), section_align);
		prot = pe_section_prot(s->Characteristics);

		p = (void *)vm_mmap(NULL, sec_addr, map_size, prot,
				     MAP_PRIVATE | MAP_ANONYMOUS |
				     MAP_FIXED_NOREPLACE, 0);
		if (IS_ERR_VALUE(p)) {
			pr_err("section %.8s: vm_mmap failed at 0x%lx (%ld)\n",
			       s->Name, sec_addr, (long)p);
			return (int)(long)p;
		}

		if (s->SizeOfRawData > 0 && s->PointerToRawData > 0) {
			loff_t off = ALIGN(s->PointerToRawData, file_align);
			void *raw_buf;
			size_t raw_sz = min_t(size_t, s->SizeOfRawData, map_size);

			raw_buf = kvmalloc(raw_sz, GFP_KERNEL);
			if (!raw_buf)
				return -ENOMEM;

			if (pe_read_at(file, raw_buf, raw_sz, off)) {
				kvfree(raw_buf);
				pr_err("section %.8s: failed to read raw data\n",
				       s->Name);
				return -EIO;
			}

			if (copy_to_user((void __user *)sec_addr, raw_buf, raw_sz)) {
				kvfree(raw_buf);
				pr_err("section %.8s: copy_to_user failed\n",
				       s->Name);
				return -EFAULT;
			}
			kvfree(raw_buf);
		}

		pr_info("section %.8s: VA=0x%lx size=0x%zx prot=%d\n",
			s->Name, sec_addr, map_size, prot);
	}

	return 0;
}

static int pe_apply_relocs(struct file *file,
			   struct pe_dos_header *dos,
			   struct pe_coff_header *coff,
			   struct pe_optional_header64 *opt64,
			   struct pe_optional_header32 *opt32,
			   bool is_64bit,
			   struct axon_pe_module *mod)
{
	__u32 reloc_rva = 0;
	__u32 reloc_size = 0;
	__u32 section_align;
	__u64 image_base;
	__u64 actual_base;
	__s64 delta;
	unsigned long sec_start;
	__u32 off;
	void *reloc_buf;
	int ret;

	if (is_64bit) {
		struct pe_data_dir *dd = opt64->DataDirectory;

		if (opt64->NumberOfRvaAndSizes > PE_DIR_BASERELOC) {
			reloc_rva = dd[PE_DIR_BASERELOC].VirtualAddress;
			reloc_size = dd[PE_DIR_BASERELOC].Size;
		}
		image_base = opt64->ImageBase;
		section_align = opt64->SectionAlignment;
	} else {
		struct pe_data_dir *dd = opt32->DataDirectory;

		if (opt32->NumberOfRvaAndSizes > PE_DIR_BASERELOC) {
			reloc_rva = dd[PE_DIR_BASERELOC].VirtualAddress;
			reloc_size = dd[PE_DIR_BASERELOC].Size;
		}
		image_base = opt32->ImageBase;
		section_align = opt32->SectionAlignment;
	}

	if (reloc_rva == 0 || reloc_size == 0)
		return 0;

	actual_base = (__u64)(unsigned long)mod->base;
	delta = (__s64)(actual_base - image_base);
	if (delta == 0)
		return 0;

	pr_info("applying relocations: delta=0x%llx (preferred=0x%llx actual=0x%llx)\n",
		(unsigned long long)delta,
		(unsigned long long)image_base,
		(unsigned long long)actual_base);

	reloc_buf = kvmalloc(reloc_size, GFP_KERNEL);
	if (!reloc_buf)
		return -ENOMEM;

	sec_start = (unsigned long)mod->base + reloc_rva;

	if (copy_from_user(reloc_buf, (void __user *)sec_start, reloc_size)) {
		kvfree(reloc_buf);
		return -EFAULT;
	}

	off = 0;
	while (off < reloc_size) {
		struct pe_base_reloc_block *blk;
		__u32 blk_size;
		__u32 num_entries;
		__u32 page_rva;
		__u32 j;

		blk = (struct pe_base_reloc_block *)(reloc_buf + off);
		blk_size = blk->BlockSize;
		page_rva = blk->PageRVA;

		if (blk_size < sizeof(*blk) || off + blk_size > reloc_size)
			break;

		num_entries = (blk_size - sizeof(*blk)) / sizeof(__u16);

		for (j = 0; j < num_entries; j++) {
			__u16 entry = ((__u16 *)(blk + 1))[j];
			__u16 type = entry >> 12;
			__u16 rva_off = entry & 0x0FFF;
			unsigned long patch_addr =
				(unsigned long)mod->base + page_rva + rva_off;

			if (type == PE_REL_ABSOLUTE)
				continue;

			if (type == PE_REL_DIR64 && is_64bit) {
				__u64 val;

				if (get_user(val, (__u64 __user *)patch_addr))
					continue;
				val += (__u64)delta;
				put_user(val, (__u64 __user *)patch_addr);
			} else if (type == PE_REL_HIGHLOW && !is_64bit) {
				__u32 val;

				if (get_user(val, (__u32 __user *)patch_addr))
					continue;
				val += (__u32)delta;
				put_user(val, (__u32 __user *)patch_addr);
			} else if (type == PE_REL_HIGH) {
				__u16 val;

				if (get_user(val, (__u16 __user *)patch_addr))
					continue;
				val += (__u16)((__u32)delta >> 16);
				put_user(val, (__u16 __user *)patch_addr);
			} else if (type == PE_REL_LOW) {
				__u16 val;

				if (get_user(val, (__u16 __user *)patch_addr))
					continue;
				val += (__u16)delta;
				put_user(val, (__u16 __user *)patch_addr);
			}
		}

		off += blk_size;
	}

	ret = 0;
	kvfree(reloc_buf);
	return ret;
}

int axon_pe_load(struct linux_binprm *bprm, struct axon_pe_module **out_mod)
{
	struct pe_dos_header dos;
	struct pe_coff_header coff;
	struct pe_optional_header64 opt64 = { 0 };
	struct pe_optional_header32 opt32 = { 0 };
	struct pe_section_header *sects = NULL;
	struct axon_pe_module *mod = NULL;
	bool is_64bit = false;
	__u32 entry_rva;
	__u32 size_of_image;
	__u16 subsystem;
	__u16 num_sects;
	int ret;

	if (!bprm || !bprm->file || !out_mod)
		return -EINVAL;

	ret = axon_pe_validate(bprm->file);
	if (ret)
		return ret;

	ret = pe_read_headers(bprm->file, &dos, &coff, &opt64, &opt32,
			      &is_64bit);
	if (ret) {
		pr_err("failed to read PE headers\n");
		return ret;
	}

	num_sects = coff.NumberOfSections;

	if (is_64bit) {
		entry_rva = opt64.AddressOfEntryPoint;
		size_of_image = opt64.SizeOfImage;
		subsystem = opt64.Subsystem;
	} else {
		entry_rva = opt32.AddressOfEntryPoint;
		size_of_image = opt32.SizeOfImage;
		subsystem = opt32.Subsystem;
	}

	if (size_of_image == 0 || size_of_image > 0x80000000U) {
		pr_err("invalid SizeOfImage: 0x%x\n", size_of_image);
		return -ENOEXEC;
	}

	ret = pe_read_section_headers(bprm->file, &dos, num_sects, &sects);
	if (ret) {
		pr_err("failed to read section headers\n");
		return ret;
	}

	mod = kzalloc(sizeof(*mod), GFP_KERNEL);
	if (!mod) {
		kfree(sects);
		return -ENOMEM;
	}

	mod->image_base = is_64bit ? opt64.ImageBase : opt32.ImageBase;
	mod->size_of_image = size_of_image;
	mod->entry_point_rva = entry_rva;
	mod->machine = coff.Machine;
	mod->subsystem = subsystem;
	mod->is_64bit = is_64bit;
	mod->file = get_file(bprm->file);

	strscpy(mod->name, bprm->filename, sizeof(mod->name));

	ret = pe_map_sections(bprm->file, sects, num_sects, mod);
	if (ret) {
		pr_err("failed to map PE sections\n");
		goto err_free;
	}

	ret = pe_apply_relocs(bprm->file, &dos, &coff, &opt64, &opt32,
			      is_64bit, mod);
	if (ret) {
		pr_err("failed to apply relocations\n");
		goto err_unmap;
	}

	kfree(sects);

	pr_info("loaded PE: %s base=0x%lx entry=0x%x image_size=0x%x%s\n",
		mod->name,
		(unsigned long)mod->base,
		mod->entry_point_rva,
		mod->size_of_image,
		is_64bit ? " (PE32+)" : " (PE32)");

	*out_mod = mod;
	return 0;

err_unmap:
	vm_munmap((unsigned long)mod->base, mod->size_of_image);
err_free:
	fput(mod->file);
	kfree(mod);
	kfree(sects);
	return ret;
}

// ── Unload ───────────────────────────────────────────────────────────────────

void axon_pe_unload(struct axon_pe_module *mod)
{
	if (!mod)
		return;

	pr_info("unloading PE: %s\n", mod->name);

	if (mod->base && !IS_ERR_VALUE((unsigned long)mod->base))
		vm_munmap((unsigned long)mod->base, mod->size_of_image);

	if (mod->file)
		fput(mod->file);

	kfree(mod);
}

// ── Map User ─────────────────────────────────────────────────────────────────

unsigned long axon_pe_map_user(struct axon_pe_module *mod)
{
	unsigned long addr;

	if (!mod || !mod->base)
		return 0;

	addr = (unsigned long)mod->base;

	pr_info("PE %s mapped at user VA 0x%lx (size=0x%x)\n",
		mod->name, addr, mod->size_of_image);

	return addr;
}
