// binfmt_win.c — Linux binfmt handler for PE/COFF executables.
//
// Registers a binary format handler that recognises Windows PE files
// (MZ magic) and loads them as native Linux processes with the Axon
// Windows ABI providing NT syscall translation.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/binfmts.h>
#include <linux/slab.h>
#include <linux/fs.h>
#include <linux/sched.h>
#include <linux/mm.h>
#include <linux/elf.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/cred.h>
#include <asm/processor.h>

#include "axon-winabi.h"

static int read_mz_magic(struct file *file, __u16 *magic)
{
	loff_t pos = 0;
	ssize_t nr;

	nr = kernel_read(file, magic, sizeof(*magic), &pos);
	if (nr != sizeof(*magic))
		return -ENOEXEC;

	return 0;
}

static int setup_user_stack(struct linux_binprm *bprm,
			    unsigned long *stack_top)
{
	unsigned long sp;
	unsigned long zero = 0;
	int ret;

	sp = bprm->p;

	sp -= sizeof(unsigned long);
	ret = copy_to_user((unsigned long __user *)sp, &zero, sizeof(zero));
	if (ret)
		return -EFAULT;

	sp -= sizeof(unsigned long);
	ret = copy_to_user((unsigned long __user *)sp, &zero, sizeof(zero));
	if (ret)
		return -EFAULT;

	sp -= sizeof(unsigned long);
	ret = copy_to_user((unsigned long __user *)sp, &zero, sizeof(zero));
	if (ret)
		return -EFAULT;

	sp &= ~0xfUL;

	*stack_top = sp;
	return 0;
}

static int axon_binfmt_load_binary(struct linux_binprm *bprm);

static struct linux_binfmt axon_binfmt = {
	.module      = THIS_MODULE,
	.load_binary = axon_binfmt_load_binary,
};

static int axon_binfmt_load_binary(struct linux_binprm *bprm)
{
	struct axon_pe_module *mod = NULL;
	struct axon_task_state *state;
	struct pt_regs *regs;
	struct cred *new_creds;
	unsigned long entry_addr;
	unsigned long stack_addr;
	__u16 mz_magic;
	int ret;

	ret = read_mz_magic(bprm->file, &mz_magic);
	if (ret || mz_magic != MZ_MAGIC)
		return -ENOEXEC;

	ret = axon_pe_validate(bprm->file);
	if (ret) {
		pr_debug("PE validation failed: %d\n", ret);
		return ret;
	}

	ret = axon_pe_load(bprm, &mod);
	if (ret) {
		pr_err("PE load failed: %d\n", ret);
		return ret;
	}

	ret = begin_new_exec(bprm);
	if (ret) {
		pr_err("begin_new_exec failed: %d\n", ret);
		goto err_unload;
	}

	/* Personality stays as default Linux */

	new_creds = prepare_creds();
	if (new_creds)
		commit_creds(new_creds);

	set_binfmt(&axon_binfmt);

	entry_addr = axon_pe_map_user(mod);
	if (!entry_addr) {
		pr_err("PE user-space mapping failed\n");
		ret = -ENOMEM;
		goto err_unload;
	}

	entry_addr += mod->entry_point_rva;

	ret = axon_task_state_alloc(current->pid);
	if (ret) {
		pr_err("task state alloc failed: %d\n", ret);
		goto err_unload;
	}

	state = axon_get_task_state(current);
	if (state)
		state->module = mod;

	ret = setup_user_stack(bprm, &stack_addr);
	if (ret) {
		pr_err("user stack setup failed: %d\n", ret);
		goto err_free_state;
	}

	setup_new_exec(bprm);

	regs = task_pt_regs(current);
#ifdef CONFIG_X86_64
	regs->ip = entry_addr;
	regs->sp = stack_addr;
	regs->cs = __USER_CS;
	regs->ss = __USER_DS;
	regs->flags = X86_EFLAGS_IF;
#elif defined(CONFIG_ARM64)
	regs->pc = entry_addr;
	regs->sp = stack_addr;
#else
#error "axon-winabi: unsupported architecture"
#endif

	pr_info("loaded PE image '%s': entry=0x%lx sp=0x%lx\n",
		mod->name, entry_addr, stack_addr);

	return 0;

err_free_state:
	axon_task_state_free(current->pid);
err_unload:
	if (mod)
		axon_pe_unload(mod);
	return ret;
}

int axon_binfmt_init(void)
{
	register_binfmt(&axon_binfmt);
	pr_info("PE/COFF binfmt handler registered\n");
	return 0;
}

void axon_binfmt_exit(void)
{
	unregister_binfmt(&axon_binfmt);
	pr_info("PE/COFF binfmt handler unregistered\n");
}
