// syscall_table.c — NT syscall dispatch table for the Axon Windows ABI.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>

#include "axon-winabi.h"

static nt_syscall_handler_t nt_syscall_table[NT_MAX_SYSCALLS];

int axon_syscall_table_init(void)
{
	memset(nt_syscall_table, 0, sizeof(nt_syscall_table));

	nt_syscall_table[0x01] = nt_terminate_process;
	nt_syscall_table[0x03] = nt_allocate_virtual_memory;
	nt_syscall_table[0x05] = nt_free_virtual_memory;
	nt_syscall_table[0x06] = nt_write_file;
	nt_syscall_table[0x07] = nt_read_file;
	nt_syscall_table[0x08] = nt_create_file;
	nt_syscall_table[0x09] = nt_close;
	nt_syscall_table[0x0A] = nt_query_information_process;
	nt_syscall_table[0x0B] = nt_set_information_process;
	nt_syscall_table[0x10] = nt_query_information_file;
	nt_syscall_table[0x11] = nt_set_information_file;
	nt_syscall_table[0x14] = nt_flush_buffers_file;
	nt_syscall_table[0x19] = nt_query_system_information;
	nt_syscall_table[0x23] = nt_create_section;
	nt_syscall_table[0x24] = nt_open_thread;
	nt_syscall_table[0x25] = nt_map_view_of_section;
	nt_syscall_table[0x26] = nt_query_section;
	nt_syscall_table[0x27] = nt_unmap_view_of_section;
	nt_syscall_table[0x28] = nt_query_virtual_memory;
	nt_syscall_table[0x29] = nt_protect_virtual_memory;
	nt_syscall_table[0x2C] = nt_open_key;
	nt_syscall_table[0x2D] = nt_query_value_key;
	nt_syscall_table[0x2E] = nt_query_key;
	nt_syscall_table[0x32] = nt_create_thread;
	nt_syscall_table[0x33] = nt_terminate_thread;
	nt_syscall_table[0x34] = nt_suspend_thread;
	nt_syscall_table[0x35] = nt_resume_thread;
	nt_syscall_table[0x36] = nt_get_context_thread;
	nt_syscall_table[0x37] = nt_set_context_thread;
	nt_syscall_table[0x38] = nt_wait_for_single_object;
	nt_syscall_table[0x39] = nt_wait_for_multiple_objects;
	nt_syscall_table[0x3A] = nt_create_event;
	nt_syscall_table[0x3B] = nt_set_event;
	nt_syscall_table[0x3C] = nt_reset_event;
	nt_syscall_table[0x3D] = nt_open_event;
	nt_syscall_table[0x3E] = nt_pulse_event;
	nt_syscall_table[0x40] = nt_create_mutant;
	nt_syscall_table[0x41] = nt_release_mutant;
	nt_syscall_table[0x42] = nt_open_mutant;
	nt_syscall_table[0x43] = nt_create_semaphore;
	nt_syscall_table[0x44] = nt_release_semaphore;
	nt_syscall_table[0x4E] = nt_create_thread_ex;
	nt_syscall_table[0x100] = nt_get_current_process_id;
	nt_syscall_table[0x101] = nt_get_current_thread_id;
	nt_syscall_table[0x102] = nt_get_tick_count;
	nt_syscall_table[0x103] = nt_query_system_time;
	nt_syscall_table[0x104] = nt_delay_execution;
	nt_syscall_table[0x105] = nt_open_section;

	pr_info("syscall table initialized with 38 entries\n");
	return 0;
}

__u32 axon_dispatch_nt_syscall(__u32 syscall_nr, const __u64 *args)
{
	nt_syscall_handler_t handler;

	if (syscall_nr >= NT_MAX_SYSCALLS) {
		pr_warn("syscall 0x%x out of range\n", syscall_nr);
		return NT_STATUS_NOT_IMPLEMENTED;
	}

	handler = nt_syscall_table[syscall_nr];
	if (!handler) {
		pr_warn("syscall 0x%x not implemented\n", syscall_nr);
		return NT_STATUS_NOT_IMPLEMENTED;
	}

	return handler(args);
}

void axon_syscall_table_exit(void)
{
	pr_info("syscall table cleaned up\n");
}
