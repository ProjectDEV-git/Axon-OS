// axon-winabi.c — Main kernel module entry point for the Axon Windows ABI.
//
// This module registers a Linux binfmt handler that intercepts PE/COFF
// executables (Windows .exe files) and provides NT syscall translation
// so they run natively on the Linux kernel.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/slab.h>
#include <linux/sched.h>
#include <linux/binfmts.h>
#include <linux/mm.h>
#include <linux/fs.h>
#include <linux/hashtable.h>

#include "axon-winabi.h"

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Axon OS Contributors");
MODULE_DESCRIPTION("Windows ABI support — PE loader and NT syscall translation");
MODULE_VERSION("0.1.0");

/* ── Per-task state management ───────────────────────────────────────────── */

/*
 * We store per-task Windows ABI state in a hash table keyed by pid.
 * Using task_struct->android_vendor_data would be cleaner but requires
 * kernel patches. A simple hash table works across kernel versions.
 */

#define AXON_TASK_HASH_BITS 8
#define AXON_TASK_HASH_SIZE (1 << AXON_TASK_HASH_BITS)

struct axon_task_entry {
    struct hlist_node node;
    pid_t pid;
    struct axon_task_state state;
};

static DEFINE_HASHTABLE(axon_task_table, AXON_TASK_HASH_BITS);
static DEFINE_SPINLOCK(axon_task_lock);

struct axon_task_state *axon_get_task_state(struct task_struct *tsk)
{
    struct axon_task_entry *entry;
    pid_t pid = task_pid_vnr(tsk);

    hash_for_each_possible(axon_task_table, entry, node, pid) {
        if (entry->pid == pid)
            return &entry->state;
    }
    return NULL;
}

int axon_task_state_alloc(pid_t pid)
{
    struct axon_task_entry *entry;

    entry = kzalloc(sizeof(*entry), GFP_KERNEL);
    if (!entry)
        return -ENOMEM;

    entry->pid = pid;
    entry->state.is_winabi = true;
    entry->state.module = NULL;
    entry->state.syscall_args = NULL;

    spin_lock(&axon_task_lock);
    hash_add(axon_task_table, &entry->node, pid);
    spin_unlock(&axon_task_lock);

    pr_info("registered task %d for Windows ABI\n", pid);
    return 0;
}

void axon_task_state_free(pid_t pid)
{
    struct axon_task_entry *entry;

    spin_lock(&axon_task_lock);
    hash_for_each_possible(axon_task_table, entry, node, pid) {
        if (entry->pid == pid) {
            hash_del(&entry->node);
            kfree(entry->state.syscall_args);
            kfree(entry);
            break;
        }
    }
    spin_unlock(&axon_task_lock);
}

/* ── Module init / exit ──────────────────────────────────────────────────── */

extern int axon_binfmt_init(void);
extern void axon_binfmt_exit(void);

static int __init axon_winabi_init(void)
{
    int ret;

    pr_info("Axon Windows ABI v0.2.0 loading\n");

    ret = axon_handle_table_init();
    if (ret) {
        pr_err("failed to initialize handle table: %d\n", ret);
        return ret;
    }

    ret = axon_syscall_table_init();
    if (ret) {
        pr_err("failed to initialize syscall table: %d\n", ret);
        axon_handle_table_exit();
        return ret;
    }

    ret = axon_registry_init();
    if (ret) {
        pr_err("failed to initialize registry: %d\n", ret);
        axon_syscall_table_exit();
        axon_handle_table_exit();
        return ret;
    }

    ret = axon_dll_loader_init();
    if (ret) {
        pr_err("failed to initialize DLL loader: %d\n", ret);
        axon_registry_exit();
        axon_syscall_table_exit();
        axon_handle_table_exit();
        return ret;
    }

    ret = axon_binfmt_init();
    if (ret) {
        pr_err("failed to register binfmt handler: %d\n", ret);
        axon_dll_loader_exit();
        axon_registry_exit();
        axon_syscall_table_exit();
        axon_handle_table_exit();
        return ret;
    }

    pr_info("loaded — ready to execute Windows PE binaries\n");
    return 0;
}

static void __exit axon_winabi_exit(void)
{
    struct axon_task_entry *entry;
    struct hlist_node *tmp;
    int bkt;

    axon_binfmt_exit();
    axon_dll_loader_exit();
    axon_registry_exit();
    axon_syscall_table_exit();
    axon_handle_table_exit();

    /* Clean up any remaining task states */
    spin_lock(&axon_task_lock);
    hash_for_each_safe(axon_task_table, bkt, tmp, entry, node) {
        hash_del(&entry->node);
        kfree(entry->state.syscall_args);
        kfree(entry);
    }
    spin_unlock(&axon_task_lock);

    pr_info("unloaded\n");
}

module_init(axon_winabi_init);
module_exit(axon_winabi_exit);
