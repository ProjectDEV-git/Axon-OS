// nt-thread.c — NT thread syscalls for the Axon Windows ABI.
//
// Implements NtCreateThreadEx, NtCreateThread, NtTerminateThread,
// and stubs for the remaining thread-related NT syscalls.
//
// Thread creation works by spawning a kernel thread that borrows the
// parent's address space (kthread_use_mm), allocates a user-space
// stack via vm_mmap, wires up pt_regs with ip=start_routine, and
// "returns" into user-mode — the same pattern binfmt_win.c uses for
// the initial PE entry point.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/sched.h>
#include <linux/sched/task.h>
#include <linux/mm.h>
#include <linux/mman.h>
#include <linux/kthread.h>
#include <linux/kthread.h>
#include <asm/processor.h>
#include <asm/ptrace.h>

#include "axon-winabi.h"

#define DEFAULT_USER_STACK_SIZE	(1024UL * 1024)	/* 1 MiB */

/* Synthetic handle counter for NT thread handles */
static atomic_t nt_handle_counter = ATOMIC_INIT(0x1000);

/* ── Thread entry trampoline ──────────────────────────────────────────────── */

struct nt_thread_ctx {
	unsigned long start_routine;
	unsigned long argument;
	size_t stack_size;
};

/*
 * Kernel thread trampoline — allocates a user stack, wires pt_regs,
 * and returns 0 so the kernel's ret_from_kernel_thread path enters
 * user-space at start_routine.
 */
static int nt_thread_trampoline(void *data)
{
	struct nt_thread_ctx ctx;
	struct pt_regs *regs;
	unsigned long user_sp;

	ctx = *(struct nt_thread_ctx *)data;
	kfree(data);

	/* Allocate a user-space stack */
	user_sp = vm_mmap(NULL, 0, ctx.stack_size,
			  PROT_READ | PROT_WRITE,
			  MAP_PRIVATE | MAP_ANONYMOUS | MAP_GROWSDOWN, 0);
	if (IS_ERR_VALUE(user_sp)) {
		pr_err("nt_thread: user stack alloc failed\n");
		return -ENOMEM;
	}

	/* Stack grows down — compute top, 16-byte aligned */
	user_sp += ctx.stack_size;
	user_sp &= ~0xfUL;
	user_sp -= 8;	/* fake return address slot */

	/* Wire pt_regs for return to user-space at start_routine */
	regs = task_pt_regs(current);
#ifdef CONFIG_X86_64
	regs->ip = ctx.start_routine;
	regs->di = ctx.argument;
	regs->sp = user_sp;
	regs->cs = __USER_CS;
	regs->ss = __USER_DS;
	regs->flags = X86_EFLAGS_IF;
#elif defined(CONFIG_ARM64)
	regs->pc = ctx.start_routine;
	regs->regs[0] = ctx.argument;
	regs->sp = user_sp;
#else
#error "axon-winabi: unsupported architecture for thread creation"
#endif

	pr_debug("nt_thread: launching tid=%d at 0x%lx sp=0x%lx\n",
		 current->pid, ctx.start_routine, user_sp);

	return 0;
}

/* ── Shared thread creation ───────────────────────────────────────────────── */

static __u32 do_create_thread(__u64 __user *handle_out,
			      unsigned long start_routine,
			      unsigned long argument,
			      unsigned long stack_size)
{
	struct nt_thread_ctx *ctx;
	struct task_struct *task;
	__u32 handle;

	if (!start_routine)
		return NT_STATUS_INVALID_PARAMETER;

	if (stack_size == 0)
		stack_size = DEFAULT_USER_STACK_SIZE;
	stack_size = ALIGN(stack_size, PAGE_SIZE);

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return NT_STATUS_NO_MEMORY;

	ctx->start_routine = start_routine;
	ctx->argument = argument;
	ctx->stack_size = stack_size;

	task = kthread_create(nt_thread_trampoline, ctx, "ntthr/%d",
			      atomic_inc_return(&nt_handle_counter));
	if (IS_ERR(task)) {
		pr_err("nt_thread: kthread_create failed: %ld\n",
		       PTR_ERR(task));
		kfree(ctx);
		return NT_STATUS_NO_MEMORY;
	}

	/* Share the parent's address space */
	kthread_use_mm(current->mm);

	handle = (__u32)atomic_read(&nt_handle_counter);
	wake_up_process(task);

	pr_info("NtCreateThread: handle=0x%x start=0x%lx arg=0x%lx\n",
		handle, start_routine, argument);

	if (handle_out && access_ok(handle_out, sizeof(handle)))
		copy_to_user(handle_out, &handle, sizeof(handle));

	return NT_STATUS_SUCCESS;
}

/* ── NtCreateThreadEx (syscall 0x4E) ──────────────────────────────────────── */

__u32 nt_create_thread_ex(const __u64 *args)
{
	__u64 __user *handle_out = (__u64 __user *)args[0];
	unsigned long start_routine = (unsigned long)args[4];
	unsigned long argument = (unsigned long)args[5];
	unsigned long stack_size = (unsigned long)args[8];

	return do_create_thread(handle_out, start_routine, argument,
				stack_size);
}

/* ── NtCreateThread (legacy, syscall 0x32) ────────────────────────────────── */

__u32 nt_create_thread(const __u64 *args)
{
	__u64 __user *handle_out = (__u64 __user *)args[0];
	unsigned long start_routine = (unsigned long)args[5];
	unsigned long argument = (unsigned long)args[6];

	return do_create_thread(handle_out, start_routine, argument, 0);
}

/* ── NtOpenThread (stub) ──────────────────────────────────────────────────── */

__u32 nt_open_thread(const __u64 *args)
{
	return NT_STATUS_NOT_IMPLEMENTED;
}

/* ── NtTerminateThread ────────────────────────────────────────────────────── */

__u32 nt_terminate_thread(const __u64 *args)
{
	int exit_code = (int)args[1];

	pr_info("NtTerminateThread: exit_code=%d\n", exit_code);
	kthread_complete_and_exit(NULL, exit_code);
	return NT_STATUS_SUCCESS; /* unreachable */
}

/* ── NtSuspendThread / NtResumeThread (stubs) ─────────────────────────────── */

__u32 nt_suspend_thread(const __u64 *args)
{
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_resume_thread(const __u64 *args)
{
	return NT_STATUS_NOT_IMPLEMENTED;
}

/* ── NtGetContextThread / NtSetContextThread (stubs) ───────────────────────── */

__u32 nt_get_context_thread(const __u64 *args)
{
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_set_context_thread(const __u64 *args)
{
	return NT_STATUS_NOT_IMPLEMENTED;
}
