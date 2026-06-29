// nt-section.c — NT section (memory-mapped file) syscalls for Axon Windows ABI.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/fs.h>
#include <linux/mm.h>
#include <linux/idr.h>
#include <linux/spinlock.h>
#include <linux/file.h>
#include <linux/mman.h>

#include "axon-winabi.h"

#define NT_SECTION_PROT_READ    0x02
#define NT_SECTION_PROT_WRITE   0x04
#define NT_SECTION_PROT_EXEC    0x10

#define SEC_COMMIT     0x08000000

#define NT_STATUS_INFO_LENGTH_MISMATCH 0xC0000004

enum axon_section_type {
	AXON_SEC_FILE_BACKED,
	AXON_SEC_ANONYMOUS,
};

struct axon_section {
	enum axon_section_type type;
	struct file *file;
	size_t size;
	u32 protect;
};

struct axon_mapping {
	unsigned long addr;
	size_t size;
	struct list_head node;
};

static DEFINE_IDR(axon_section_idr);
static DEFINE_SPINLOCK(axon_section_lock);
static u32 axon_section_next_handle = 1;

static LIST_HEAD(axon_mapping_list);
static DEFINE_SPINLOCK(axon_mapping_lock);

static int axon_prot_to_linux(u32 win_prot)
{
	int prot = 0;

	if (win_prot & NT_SECTION_PROT_READ)
		prot |= PROT_READ;
	if (win_prot & NT_SECTION_PROT_WRITE)
		prot |= PROT_WRITE;
	if (win_prot & NT_SECTION_PROT_EXEC)
		prot |= PROT_EXEC;

	return prot ? prot : PROT_READ;
}

static void axon_section_destroy(struct axon_section *sec)
{
	if (!sec)
		return;
	if (sec->file)
		fput(sec->file);
	kfree(sec);
}

static struct axon_section *axon_section_get(u32 handle)
{
	struct axon_section *sec;

	spin_lock(&axon_section_lock);
	sec = idr_find(&axon_section_idr, handle);
	spin_unlock(&axon_section_lock);

	return sec;
}

static void axon_mapping_track(unsigned long addr, size_t size)
{
	struct axon_mapping *map;

	map = kmalloc(sizeof(*map), GFP_KERNEL);
	if (!map)
		return;

	map->addr = addr;
	map->size = size;

	spin_lock(&axon_mapping_lock);
	list_add(&map->node, &axon_mapping_list);
	spin_unlock(&axon_mapping_lock);
}

static size_t axon_mapping_find_size(unsigned long addr)
{
	struct axon_mapping *map;
	size_t size = 0;

	spin_lock(&axon_mapping_lock);
	list_for_each_entry(map, &axon_mapping_list, node) {
		if (map->addr == addr) {
			size = map->size;
			break;
		}
	}
	spin_unlock(&axon_mapping_lock);

	return size;
}

static void axon_mapping_remove(unsigned long addr)
{
	struct axon_mapping *map, *tmp;

	spin_lock(&axon_mapping_lock);
	list_for_each_entry_safe(map, tmp, &axon_mapping_list, node) {
		if (map->addr == addr) {
			list_del(&map->node);
			kfree(map);
			break;
		}
	}
	spin_unlock(&axon_mapping_lock);
}

// ── NtCreateSection ──────────────────────────────────────────────────────────

__u32 nt_create_section(const __u64 *args)
{
	u32 __user *handle_out = (u32 __user *)args[0];
	u64 maximum_size_raw = args[3];
	u32 section_page_prot = (u32)args[4];
	u64 file_handle = args[6];
	struct axon_section *sec;
	struct file *file = NULL;
	size_t max_size;
	u32 handle;
	int id;

	if (!handle_out)
		return NT_STATUS_INVALID_PARAMETER;

	if (file_handle != 0) {
		file = fget((unsigned int)file_handle);
		if (!file)
			return NT_STATUS_INVALID_PARAMETER;
	}

	sec = kzalloc(sizeof(*sec), GFP_KERNEL);
	if (!sec) {
		if (file)
			fput(file);
		return NT_STATUS_NO_MEMORY;
	}

	if (file) {
		loff_t fsize = i_size_read(file_inode(file));

		sec->type = AXON_SEC_FILE_BACKED;
		sec->file = file;
		max_size = maximum_size_raw ? (size_t)maximum_size_raw
					    : (size_t)fsize;
		if (max_size == 0)
			max_size = PAGE_SIZE;
	} else {
		sec->type = AXON_SEC_ANONYMOUS;
		sec->file = NULL;
		max_size = maximum_size_raw ? (size_t)maximum_size_raw
					    : PAGE_SIZE;
	}

	max_size = ALIGN(max_size, PAGE_SIZE);
	sec->size = max_size;
	sec->protect = section_page_prot;

	spin_lock(&axon_section_lock);
	id = idr_alloc(&axon_section_idr, sec, axon_section_next_handle,
		       0, GFP_ATOMIC);
	if (id < 0) {
		spin_unlock(&axon_section_lock);
		axon_section_destroy(sec);
		return NT_STATUS_NO_MEMORY;
	}
	axon_section_next_handle = (u32)(id + 1);
	handle = (u32)id;
	spin_unlock(&axon_section_lock);

	if (copy_to_user(handle_out, &handle, sizeof(handle))) {
		spin_lock(&axon_section_lock);
		idr_remove(&axon_section_idr, handle);
		spin_unlock(&axon_section_lock);
		axon_section_destroy(sec);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	pr_debug("NtCreateSection: handle=%u type=%s size=0x%zx\n",
		 handle,
		 sec->type == AXON_SEC_FILE_BACKED ? "file" : "anon",
		 sec->size);
	return NT_STATUS_SUCCESS;
}

// ── NtOpenSection ────────────────────────────────────────────────────────────

__u32 nt_open_section(const __u64 *args)
{
	pr_debug("NtOpenSection: stub\n");
	return NT_STATUS_NOT_IMPLEMENTED;
}

// ── NtMapViewOfSection ──────────────────────────────────────────────────────

__u32 nt_map_view_of_section(const __u64 *args)
{
	u32 section_handle = (u32)args[0];
	unsigned long __user *base_addr_ptr = (unsigned long __user *)args[2];
	size_t __user *view_size_ptr = (size_t __user *)args[6];
	u32 win32_protect = (u32)args[9];
	struct axon_section *sec;
	unsigned long base_hint = 0;
	size_t view_size = 0;
	unsigned long addr;
	int prot, flags;

	sec = axon_section_get(section_handle);
	if (!sec)
		return NT_STATUS_INVALID_PARAMETER;

	if (base_addr_ptr && access_ok(base_addr_ptr, sizeof(unsigned long)))
		copy_from_user(&base_hint, base_addr_ptr, sizeof(base_hint));

	if (view_size_ptr && access_ok(view_size_ptr, sizeof(size_t)))
		copy_from_user(&view_size, view_size_ptr, sizeof(view_size));

	if (view_size == 0)
		view_size = sec->size;

	view_size = ALIGN(view_size, PAGE_SIZE);

	prot = axon_prot_to_linux(win32_protect);

	if (sec->type == AXON_SEC_FILE_BACKED && sec->file) {
		flags = MAP_SHARED;
		if (base_hint != 0)
			flags |= MAP_FIXED;
		addr = vm_mmap(sec->file, base_hint, view_size, prot,
			       flags, 0);
	} else {
		flags = MAP_PRIVATE | MAP_ANONYMOUS;
		if (base_hint != 0)
			flags |= MAP_FIXED;
		addr = vm_mmap(NULL, base_hint, view_size, prot, flags, 0);
	}

	if (IS_ERR_VALUE(addr))
		return NT_STATUS_NO_MEMORY;

	axon_mapping_track(addr, view_size);

	if (base_addr_ptr && access_ok(base_addr_ptr, sizeof(unsigned long)))
		copy_to_user(base_addr_ptr, &addr, sizeof(addr));
	if (view_size_ptr && access_ok(view_size_ptr, sizeof(size_t)))
		copy_to_user(view_size_ptr, &view_size, sizeof(view_size));

	pr_debug("NtMapViewOfSection: handle=%u addr=0x%lx size=0x%zx\n",
		 section_handle, addr, view_size);
	return NT_STATUS_SUCCESS;
}

// ── NtUnmapViewOfSection ────────────────────────────────────────────────────

__u32 nt_unmap_view_of_section(const __u64 *args)
{
	unsigned long base_address = (unsigned long)args[1];
	size_t size;

	if (base_address == 0)
		return NT_STATUS_INVALID_PARAMETER;

	size = axon_mapping_find_size(base_address);
	if (size == 0)
		size = PAGE_SIZE;

	vm_munmap(base_address, size);
	axon_mapping_remove(base_address);

	pr_debug("NtUnmapViewOfSection: addr=0x%lx size=0x%zx\n",
		 base_address, size);
	return NT_STATUS_SUCCESS;
}

// ── NtQuerySection ──────────────────────────────────────────────────────────

struct section_basic_info {
	u32 section_attributes;
	u64 section_size;
};

__u32 nt_query_section(const __u64 *args)
{
	u32 section_handle = (u32)args[0];
	u32 info_class = (u32)args[1];
	void __user *buf = (void __user *)args[2];
	u32 buf_len = (u32)args[3];
	u32 __user *ret_len = (u32 __user *)args[4];
	struct axon_section *sec;

	sec = axon_section_get(section_handle);
	if (!sec)
		return NT_STATUS_INVALID_PARAMETER;

	if (info_class == 0 /* SectionBasicInformation */) {
		struct section_basic_info info;

		if (buf_len < sizeof(info))
			return NT_STATUS_INFO_LENGTH_MISMATCH;

		info.section_attributes = SEC_COMMIT;
		info.section_size = sec->size;

		if (buf && access_ok(buf, sizeof(info)))
			copy_to_user(buf, &info, sizeof(info));
		if (ret_len && access_ok(ret_len, sizeof(u32))) {
			u32 written = sizeof(info);
			copy_to_user(ret_len, &written, sizeof(written));
		}
		return NT_STATUS_SUCCESS;
	}

	pr_debug("NtQuerySection: handle=%u class=%u (stub)\n",
		 section_handle, info_class);
	return NT_STATUS_NOT_IMPLEMENTED;
}

// ── Cleanup ─────────────────────────────────────────────────────────────────

void axon_section_cleanup(void)
{
	struct axon_section *sec;
	struct axon_mapping *map, *tmp_map;
	int id;

	spin_lock(&axon_section_lock);
	idr_for_each_entry(&axon_section_idr, sec, id) {
		idr_remove(&axon_section_idr, id);
		axon_section_destroy(sec);
	}
	idr_destroy(&axon_section_idr);
	spin_unlock(&axon_section_lock);

	spin_lock(&axon_mapping_lock);
	list_for_each_entry_safe(map, tmp_map, &axon_mapping_list, node) {
		list_del(&map->node);
		kfree(map);
	}
	spin_unlock(&axon_mapping_lock);

	pr_debug("section subsystem cleaned up\n");
}
