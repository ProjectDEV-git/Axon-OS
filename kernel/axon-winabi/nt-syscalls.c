// nt-syscalls.c — NT syscall implementations for the Axon Windows ABI.
// Phase 1: 30 core syscalls. Many are stubs; real implementations for I/O and memory.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>
#include <linux/fs.h>
#include <linux/mm.h>
#include <linux/mman.h>
#include <linux/jiffies.h>
#include <linux/ktime.h>
#include <linux/delay.h>
#include <linux/idr.h>
#include <linux/file.h>
#include <linux/namei.h>

#include "axon-winabi.h"

#define NT_STATUS_INFO_LENGTH_MISMATCH 0xC0000004
#define NT_STATUS_OBJECT_NAME_NOT_FOUND 0xC0000034
#define NT_STATUS_INVALID_HANDLE        0xC0000008

/* Process information classes */
#define ProcessBasicInformation       0
#define ProcessDebugPort              7
#define ProcessWow64Information       26
#define ProcessImageFileName          27
#define ProcessCommandLineInformation 60

/* File information classes */
#define FileBasicInformation 4

/* File handle IDR — maps integer handles to struct file * */
static DEFINE_IDR(axon_file_idr);
static DEFINE_SPINLOCK(axon_file_idr_lock);

static struct file *axon_file_lookup(__u64 handle)
{
	struct file *f;

	spin_lock(&axon_file_idr_lock);
	f = idr_find(&axon_file_idr, (int)handle);
	if (f)
		get_file(f);
	spin_unlock(&axon_file_idr_lock);
	return f;
}

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

// ── Process Lifecycle ────────────────────────────────────────────────────────

__u32 nt_terminate_process(const __u64 *args)
{
	int exit_code = (int)args[1];

	pr_info("NtTerminateProcess: exit_code=%d\n", exit_code);
	send_sig(SIGKILL, current, 1);
	return NT_STATUS_SUCCESS; // unreachable
}

__u32 nt_get_current_process_id(const __u64 *args)
{
	return (__u32)task_tgid_vnr(current);
}

__u32 nt_get_current_thread_id(const __u64 *args)
{
	return (__u32)task_pid_vnr(current);
}

// ── Time ─────────────────────────────────────────────────────────────────────

__u32 nt_get_tick_count(const __u64 *args)
{
	return (__u32)jiffies_to_msecs(jiffies);
}

__u32 nt_query_system_time(const __u64 *args)
{
	/* args[0] = pointer to LARGE_INTEGER receiving NT epoch time (100ns since 1601) */
	__u64 __user *out = (__u64 __user *)args[0];
	__u64 ns = ktime_get_real_ns();
	/* NT epoch: 100ns intervals since 1601-01-01.
	 * Offset from Unix epoch = 11644473600 seconds = 116444736000000000 * 100ns */
	__u64 nt_time = ns / 100ULL + 116444736000000000ULL;

	if (out && access_ok(out, sizeof(*out)))
		copy_to_user(out, &nt_time, sizeof(nt_time));
	return NT_STATUS_SUCCESS;
}

__u32 nt_delay_execution(const __u64 *args)
{
	// args[0] = alertable (bool), args[1] = pointer to LARGE_INTEGER (100ns intervals)
	__u64 __user *interval_ptr = (__u64 __user *)args[1];
	__s64 interval = 0;
	long timeout;

	if (interval_ptr && access_ok(interval_ptr, sizeof(interval)))
		copy_from_user(&interval, interval_ptr, sizeof(interval));

	if (interval < 0) {
		// Negative = relative delay in 100ns units
		timeout = usecs_to_jiffies((unsigned long)(-interval / 10));
	} else if (interval > 0) {
		timeout = usecs_to_jiffies((unsigned long)(interval / 10));
	} else {
		timeout = 0;
	}

	if (timeout > 0)
		schedule_timeout_interruptible(timeout);

	return NT_STATUS_SUCCESS;
}

// ── Memory Management ────────────────────────────────────────────────────────

__u32 nt_allocate_virtual_memory(const __u64 *args)
{
	/* args: [process_handle, base_addr_ptr, zero_bits, region_size_ptr, alloc_type, protect] */
	void __user **base_ptr = (void __user **)args[1];
	size_t __user *size_ptr = (size_t __user *)args[3];
	__u64 protect = args[5];
	size_t region_size = 0;
	unsigned long addr_hint = 0;
	unsigned long addr;
	int prot;

	if (!base_ptr || !size_ptr)
		return NT_STATUS_INVALID_PARAMETER;

	if (copy_from_user(&addr_hint, base_ptr, sizeof(addr_hint)))
		return NT_STATUS_ACCESS_VIOLATION;

	if (copy_from_user(&region_size, size_ptr, sizeof(region_size)))
		return NT_STATUS_ACCESS_VIOLATION;

	if (region_size == 0)
		region_size = PAGE_SIZE;

	region_size = ALIGN(region_size, PAGE_SIZE);

	prot = PROT_READ | PROT_WRITE;
	if (protect & 0x10 /* PAGE_EXECUTE */ ||
	    protect & 0x20 /* PAGE_EXECUTE_READ */)
		prot |= PROT_EXEC;

	addr = vm_mmap(NULL, addr_hint, region_size, prot,
		       MAP_PRIVATE | MAP_ANONYMOUS, 0);
	if (IS_ERR_VALUE(addr))
		return NT_STATUS_NO_MEMORY;

	if (copy_to_user(base_ptr, &addr, sizeof(addr))) {
		vm_munmap(addr, region_size);
		return NT_STATUS_ACCESS_VIOLATION;
	}
	if (copy_to_user(size_ptr, &region_size, sizeof(region_size))) {
		vm_munmap(addr, region_size);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	pr_debug("NtAllocateVirtualMemory: addr=0x%lx size=0x%zx\n",
		 addr, region_size);
	return NT_STATUS_SUCCESS;
}

__u32 nt_free_virtual_memory(const __u64 *args)
{
	/* args: [process_handle, base_addr_ptr, region_size_ptr, free_type] */
	unsigned long __user *base_ptr = (unsigned long __user *)args[1];
	size_t __user *size_ptr = (size_t __user *)args[2];
	unsigned long addr;
	size_t region_size = 0;

	if (!base_ptr)
		return NT_STATUS_INVALID_PARAMETER;

	if (copy_from_user(&addr, base_ptr, sizeof(addr)))
		return NT_STATUS_ACCESS_VIOLATION;

	if (addr == 0)
		return NT_STATUS_INVALID_PARAMETER;

	if (size_ptr && !copy_from_user(&region_size, size_ptr, sizeof(region_size))
	    && region_size > 0)
		region_size = ALIGN(region_size, PAGE_SIZE);
	else
		region_size = PAGE_SIZE;

	vm_munmap(addr, region_size);
	return NT_STATUS_SUCCESS;
}

__u32 nt_query_virtual_memory(const __u64 *args)
{
	// Stub: return success
	return NT_STATUS_SUCCESS;
}

__u32 nt_protect_virtual_memory(const __u64 *args)
{
	// Stub: return success
	return NT_STATUS_SUCCESS;
}

// ── File I/O ─────────────────────────────────────────────────────────────────

__u32 nt_write_file(const __u64 *args)
{
	// args: [file_handle, event, apc, apc_ctx, io_status_block, buffer, length, byte_offset, key]
	__u64 handle = args[0];
	void __user *buf = (void __user *)args[5];
	__u32 length = (__u32)args[6];
	char *kbuf;
	ssize_t written;

	if (length == 0)
		return NT_STATUS_SUCCESS;

	if (!buf || !access_ok(buf, length))
		return NT_STATUS_INVALID_PARAMETER;

	// Handle stdout (1) and stderr (2) via printk
	if (handle == 1 || handle == 2) {
		kbuf = kvmalloc(length + 1, GFP_KERNEL);
		if (!kbuf)
			return NT_STATUS_NO_MEMORY;

		if (copy_from_user(kbuf, buf, length)) {
			kvfree(kbuf);
			return NT_STATUS_ACCESS_VIOLATION;
		}
		kbuf[length] = '\0';

		if (handle == 1)
			pr_info("[stdout] %s", kbuf);
		else
			pr_err("[stderr] %s", kbuf);

		kvfree(kbuf);
		return NT_STATUS_SUCCESS;
	}

	// For real file handles — stub for Phase 1
	pr_debug("NtWriteFile: handle=0x%llx len=%u (stub)\n", handle, length);
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_read_file(const __u64 *args)
{
	/* args: [file_handle, event, apc, apc_ctx, io_status_block,
	 *        buffer, length, byte_offset, key] */
	__u64 handle = args[0];
	void __user *buf = (void __user *)args[5];
	__u32 length = (__u32)args[6];
	__u64 __user *io_status = (__u64 __user *)args[4];
	struct file *f;
	loff_t pos = 0;
	ssize_t bytes_read;
	char *kbuf;

	if (length == 0)
		return NT_STATUS_SUCCESS;

	/* stdin (0) — stub */
	if (handle == 0) {
		pr_debug("NtReadFile: stdin read of %u bytes (stub)\n", length);
		return NT_STATUS_END_OF_FILE;
	}

	if (!buf || !access_ok(buf, length))
		return NT_STATUS_INVALID_PARAMETER;

	f = axon_file_lookup(handle);
	if (!f)
		return NT_STATUS_INVALID_HANDLE;

	kbuf = kvmalloc(length, GFP_KERNEL);
	if (!kbuf) {
		fput(f);
		return NT_STATUS_NO_MEMORY;
	}

	pos = f->f_pos;
	bytes_read = kernel_read(f, kbuf, length, &pos);
	if (bytes_read < 0) {
		kvfree(kbuf);
		fput(f);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	f->f_pos = pos;

	if (copy_to_user(buf, kbuf, bytes_read)) {
		kvfree(kbuf);
		fput(f);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	kvfree(kbuf);
	fput(f);

	/* Write bytes read to IO_STATUS_BLOCK.Information (offset 8) */
	if (io_status && access_ok(io_status, 16)) {
		__u64 info = (__u64)bytes_read;
		copy_to_user((__u64 __user *)((char __user *)io_status + 8),
			     &info, sizeof(info));
	}

	pr_debug("NtReadFile: handle=0x%llx read %zd bytes\n",
		 handle, bytes_read);

	if (bytes_read == 0)
		return NT_STATUS_END_OF_FILE;

	return NT_STATUS_SUCCESS;
}

__u32 nt_create_file(const __u64 *args)
{
	/* args: [handle_out, desired_access, obj_attr, io_status_block,
	 *        allocation_size, file_attr, share_access, create_disp,
	 *        create_options, ea_buffer, ea_length]
	 *
	 * obj_attr (args[2]) points to an OBJECT_ATTRIBUTES-like struct:
	 *   +0x00 Length          (4 bytes)
	 *   +0x04 pad             (4 bytes)
	 *   +0x08 RootDirectory   (8 bytes)
	 *   +0x10 ObjectName      (8 bytes, ptr to UNICODE_STRING)
	 *   +0x18 Attributes      (4 bytes)
	 *
	 * UNICODE_STRING:
	 *   +0x00 Length          (2 bytes, in bytes)
	 *   +0x02 MaximumLength   (2 bytes)
	 *   +0x04 pad             (4 bytes)
	 *   +0x08 Buffer          (8 bytes, ptr to wchar string)
	 */
	__u64 __user *handle_out = (__u64 __user *)args[0];
	void __user *obj_attr = (void __user *)args[2];
	__u64 create_disp = args[7];
	__u64 desired_access = args[1];
	struct file *f;
	int fd;
	int flags = 0;
	char kname[256];
	__u16 name_len;
	void __user *name_buf;
	__u16 __user *ustr_len_ptr;

	if (!obj_attr)
		return NT_STATUS_INVALID_PARAMETER;

	/* Read ObjectName pointer from OBJECT_ATTRIBUTES at offset 0x10 */
	if (copy_from_user(&name_buf, (char __user *)obj_attr + 0x10,
			   sizeof(name_buf)))
		return NT_STATUS_ACCESS_VIOLATION;

	if (!name_buf)
		return NT_STATUS_INVALID_PARAMETER;

	/* Read UNICODE_STRING.Length at offset 0x00 of the UNICODE_STRING */
	ustr_len_ptr = (__u16 __user *)name_buf;
	if (copy_from_user(&name_len, ustr_len_ptr, sizeof(name_len)))
		return NT_STATUS_ACCESS_VIOLATION;

	/* Read Buffer pointer at offset 0x08 of the UNICODE_STRING */
	if (copy_from_user(&name_buf, (char __user *)name_buf + 0x08,
			   sizeof(name_buf)))
		return NT_STATUS_ACCESS_VIOLATION;

	if (!name_buf || name_len == 0 || name_len >= sizeof(kname))
		return NT_STATUS_INVALID_PARAMETER;

	/* Copy the wide-char name and convert to ASCII (naive) */
	{
		__u16 wbuf[128];
		unsigned int i, count;

		count = min_t(unsigned int, name_len / 2, 127);
		if (copy_from_user(wbuf, name_buf, count * 2))
			return NT_STATUS_ACCESS_VIOLATION;

		for (i = 0; i < count; i++)
			kname[i] = (char)(wbuf[i] & 0x7F);
		kname[count] = '\0';
	}

	/* Map NT create disposition to Linux flags */
	switch (create_disp) {
	case 1: /* FILE_SUPERSEDE */
	case 2: /* FILE_OPEN */
		flags = O_RDONLY;
		break;
	case 3: /* FILE_CREATE */
		flags = O_CREAT | O_EXCL | O_WRONLY;
		break;
	case 4: /* FILE_OPEN_IF */
		flags = O_CREAT | O_WRONLY;
		break;
	case 5: /* FILE_OVERWRITE */
		flags = O_WRONLY | O_TRUNC;
		break;
	case 6: /* FILE_OVERWRITE_IF */
		flags = O_CREAT | O_WRONLY | O_TRUNC;
		break;
	default:
		flags = O_RDONLY;
		break;
	}

	if (desired_access & 0x40000000) /* GENERIC_WRITE */
		flags = (flags & ~O_ACCMODE) | O_RDWR;

	pr_debug("NtCreateFile: path='%s' flags=0x%x disp=%llu\n",
		 kname, flags, create_disp);

	f = filp_open(kname, flags, 0);
	if (IS_ERR(f)) {
		long err = PTR_ERR(f);

		if (err == -ENOENT)
			return NT_STATUS_OBJECT_NAME_NOT_FOUND;
		return NT_STATUS_ACCESS_VIOLATION;
	}

	fd = axon_file_install(f);
	if (fd < 0) {
		fput(f);
		return NT_STATUS_NO_MEMORY;
	}

	if (handle_out && access_ok(handle_out, sizeof(__u64))) {
		__u64 h = (__u64)fd;
		copy_to_user(handle_out, &h, sizeof(h));
	}

	pr_debug("NtCreateFile: handle=%d\n", fd);
	return NT_STATUS_SUCCESS;
}

__u32 nt_close(const __u64 *args)
{
	__u64 handle = args[0];
	struct file *f;

	if (nt_is_registry_handle((__u32)handle))
		return nt_registry_close((__u32)handle);

	/* Check if this is a file handle in our IDR */
	spin_lock(&axon_file_idr_lock);
	f = idr_find(&axon_file_idr, (int)handle);
	if (f)
		idr_remove(&axon_file_idr, (int)handle);
	spin_unlock(&axon_file_idr_lock);

	if (f) {
		fput(f);
		pr_debug("NtClose: file handle=%llu closed\n", handle);
		return NT_STATUS_SUCCESS;
	}

	pr_debug("NtClose: handle=0x%llx (unknown)\n", handle);
	return NT_STATUS_SUCCESS;
}

__u32 nt_query_information_file(const __u64 *args)
{
	/* args: [file_handle, io_status_block, buffer, buffer_length, info_class] */
	__u64 handle = args[0];
	__u64 info_class = args[4];
	void __user *buf = (void __user *)args[2];
	__u64 buf_len = args[3];
	struct file *f;

	switch (info_class) {
	case FileBasicInformation: {
		/* FILE_BASIC_INFORMATION (48 bytes):
		 *   +0x00 CreationTime     (8 bytes, NT epoch)
		 *   +0x08 LastAccessTime   (8 bytes)
		 *   +0x10 LastWriteTime    (8 bytes)
		 *   +0x18 ChangeTime       (8 bytes)
		 *   +0x20 FileAttributes   (4 bytes + 4 pad) */
		struct kstat st;
		__u8 fbi[48];
		__u64 nt_time;
		__u32 attrs;

		if (buf_len < sizeof(fbi))
			return NT_STATUS_INFO_LENGTH_MISMATCH;

		f = axon_file_lookup(handle);
		if (!f)
			return NT_STATUS_INVALID_HANDLE;

		memset(&st, 0, sizeof(st));
		vfs_getattr(&f->f_path, &st, STATX_BASIC_STATS,
			    AT_STATX_SYNC_AS_STAT);
		fput(f);

		memset(fbi, 0, sizeof(fbi));

		/* Convert Unix timespec to NT epoch (100ns since 1601-01-01) */
		#define UNIX_TO_NT_EPOCH 116444736000000000ULL
		nt_time = (u64)st.btime.tv_sec * 10000000ULL +
			  st.btime.tv_nsec / 100ULL + UNIX_TO_NT_EPOCH;
		memcpy(&fbi[0x00], &nt_time, sizeof(nt_time));

		nt_time = (u64)st.atime.tv_sec * 10000000ULL +
			  st.atime.tv_nsec / 100ULL + UNIX_TO_NT_EPOCH;
		memcpy(&fbi[0x08], &nt_time, sizeof(nt_time));

		nt_time = (u64)st.mtime.tv_sec * 10000000ULL +
			  st.mtime.tv_nsec / 100ULL + UNIX_TO_NT_EPOCH;
		memcpy(&fbi[0x10], &nt_time, sizeof(nt_time));

		nt_time = (u64)st.ctime.tv_sec * 10000000ULL +
			  st.ctime.tv_nsec / 100ULL + UNIX_TO_NT_EPOCH;
		memcpy(&fbi[0x18], &nt_time, sizeof(nt_time));

		/* Map Linux mode to NT attributes */
		attrs = 0;
		if (S_ISDIR(st.mode))
			attrs |= 0x10; /* FILE_ATTRIBUTE_DIRECTORY */
		else
			attrs |= 0x80; /* FILE_ATTRIBUTE_NORMAL */
		if (S_ISLNK(st.mode))
			attrs |= 0x400; /* FILE_ATTRIBUTE_REPARSE_POINT */
		memcpy(&fbi[0x20], &attrs, sizeof(attrs));

		if (buf && access_ok(buf, sizeof(fbi)))
			copy_to_user(buf, fbi, sizeof(fbi));
		return NT_STATUS_SUCCESS;
	}

	default:
		pr_debug("NtQueryInformationFile: handle=0x%llx class=%llu (stub)\n",
			 handle, info_class);
		return NT_STATUS_NOT_IMPLEMENTED;
	}
}

__u32 nt_set_information_file(const __u64 *args)
{
	/* args: [file_handle, io_status_block, buffer, buffer_length, info_class] */
	pr_debug("NtSetInformationFile: handle=0x%llx class=%llu (stub)\n",
		 args[0], args[4]);
	return NT_STATUS_SUCCESS;
}

__u32 nt_flush_buffers_file(const __u64 *args)
{
	return NT_STATUS_SUCCESS;
}

// ── Process Information ──────────────────────────────────────────────────────

__u32 nt_query_information_process(const __u64 *args)
{
	// args: [handle, process_info_class, buffer, buffer_length, return_length]
	u64 info_class = args[1];
	void __user *buf = (void __user *)args[2];
	u32 buf_len = (u32)args[3];
	void __user *ret_len = (void __user *)args[4];

	switch (info_class) {
	case 0: { // ProcessBasicInformation (48 bytes on x86-64)
		struct {
			u32 exit_status;
			u64 peb_address;
			u64 affinity_mask;
			u32 base_priority;
			u64 unique_process_id;
			u64 inherited_from_unique_process_id;
		} __packed pbi = {0};

		pbi.exit_status = 0;
		pbi.peb_address = 0x7FFE0000;
		pbi.affinity_mask = 0xFFFFFFFF;
		pbi.base_priority = 8;
		pbi.unique_process_id = task_tgid_vnr(current);
		pbi.inherited_from_unique_process_id = 0;

		if (buf_len < sizeof(pbi))
			return NT_STATUS_INFO_LENGTH_MISMATCH;
		if (copy_to_user(buf, &pbi, sizeof(pbi)))
			return NT_STATUS_ACCESS_VIOLATION;
		if (ret_len && access_ok(ret_len, 4)) {
			u32 sz = sizeof(pbi);
			copy_to_user(ret_len, &sz, 4);
		}
		return NT_STATUS_SUCCESS;
	}
	case 7: { // ProcessDebugPort — no debugger
		u64 port = 0;
		if (buf_len < 8)
			return NT_STATUS_INFO_LENGTH_MISMATCH;
		if (copy_to_user(buf, &port, 8))
			return NT_STATUS_ACCESS_VIOLATION;
		return NT_STATUS_SUCCESS;
	}
	case 26: { // ProcessWow64Information — not WoW64
		u64 wow64 = 0;
		if (buf_len < 8)
			return NT_STATUS_INFO_LENGTH_MISMATCH;
		if (copy_to_user(buf, &wow64, 8))
			return NT_STATUS_ACCESS_VIOLATION;
		return NT_STATUS_SUCCESS;
	}
	default:
		pr_debug("NtQueryInformationProcess: class=%llu (stub)\n", info_class);
		return NT_STATUS_NOT_IMPLEMENTED;
	}
}

__u32 nt_query_system_information(const __u64 *args)
{
	pr_debug("NtQuerySystemInformation: class=%llu (stub)\n", args[0]);
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_set_information_process(const __u64 *args)
{
	pr_debug("NtSetInformationProcess: class=%llu (stub)\n", args[1]);
	return NT_STATUS_SUCCESS;
}
