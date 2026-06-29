// nt-sync.c — NT synchronization primitives for the Axon Windows ABI.
// Implements events, mutants (mutexes), semaphores, and wait functions
// using Linux kernel wait queues, mutexes, and atomics.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/sched.h>
#include <linux/wait.h>
#include <linux/spinlock.h>
#include <linux/mutex.h>
#include <linux/atomic.h>
#include <linux/idr.h>
#include <linux/jiffies.h>

#include "axon-winabi.h"

#define NT_STATUS_INVALID_HANDLE 0xC0000008
#define NT_STATUS_ABANDONED      0x00000080
#define NT_STATUS_TIMEOUT        0x00000102

// ── Handle Table ────────────────────────────────────────────────────────────

struct axon_handle_entry {
	int type;
	void *object;
};

static DEFINE_IDR(axon_handle_idr);
static DEFINE_SPINLOCK(axon_handle_lock);

int axon_handle_alloc(int type, void *object)
{
	struct axon_handle_entry *h;
	int id;

	h = kmalloc(sizeof(*h), GFP_KERNEL);
	if (!h)
		return -ENOMEM;

	h->type = type;
	h->object = object;

	idr_preload(GFP_KERNEL);
	spin_lock(&axon_handle_lock);
	id = idr_alloc(&axon_handle_idr, h, 1, 0, GFP_NOWAIT);
	spin_unlock(&axon_handle_lock);
	idr_preload_end();

	if (id < 0)
		kfree(h);
	return id;
}

void *axon_handle_lookup(int handle, int expected_type)
{
	struct axon_handle_entry *h;

	spin_lock(&axon_handle_lock);
	h = idr_find(&axon_handle_idr, handle);
	spin_unlock(&axon_handle_lock);

	if (!h || h->type != expected_type)
		return NULL;
	return h->object;
}

void axon_handle_free(int handle)
{
	struct axon_handle_entry *h;

	spin_lock(&axon_handle_lock);
	h = idr_remove(&axon_handle_idr, handle);
	spin_unlock(&axon_handle_lock);
	kfree(h);
}

int axon_handle_table_init(void)
{
	idr_init(&axon_handle_idr);
	pr_info("handle table initialized\n");
	return 0;
}

void axon_handle_table_exit(void)
{
	struct axon_handle_entry *h;
	int id;

	idr_for_each_entry(&axon_handle_idr, h, id) {
		idr_remove(&axon_handle_idr, id);
		kfree(h);
	}
	pr_info("handle table cleaned up\n");
}

// ── Event Objects ───────────────────────────────────────────────────────────

struct axon_event {
	wait_queue_head_t wq;
	spinlock_t lock;
	bool manual_reset;
	bool signaled;
};

__u32 nt_create_event(const __u64 *args)
{
	__u32 __user *handle_out = (__u32 __user *)args[0];
	__u32 event_type = (__u32)args[3];
	bool initial_state = !!args[4];
	struct axon_event *evt;
	int handle;

	if (!handle_out)
		return NT_STATUS_INVALID_PARAMETER;

	evt = kzalloc(sizeof(*evt), GFP_KERNEL);
	if (!evt)
		return NT_STATUS_NO_MEMORY;

	init_waitqueue_head(&evt->wq);
	spin_lock_init(&evt->lock);
	evt->manual_reset = (event_type == 0);
	evt->signaled = initial_state;

	handle = axon_handle_alloc(AXON_HANDLE_EVENT, evt);
	if (handle < 0) {
		kfree(evt);
		return NT_STATUS_NO_MEMORY;
	}

	if (copy_to_user(handle_out, &handle, sizeof(handle))) {
		axon_handle_free(handle);
		kfree(evt);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	pr_debug("NtCreateEvent: handle=%d type=%u init=%d\n",
		 handle, event_type, initial_state);
	return NT_STATUS_SUCCESS;
}

__u32 nt_open_event(const __u64 *args)
{
	pr_debug("NtOpenEvent: stub\n");
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_set_event(const __u64 *args)
{
	int handle = (int)args[0];
	struct axon_event *evt;

	evt = axon_handle_lookup(handle, AXON_HANDLE_EVENT);
	if (!evt)
		return NT_STATUS_INVALID_HANDLE;

	spin_lock(&evt->lock);
	evt->signaled = true;
	if (evt->manual_reset)
		wake_up_all(&evt->wq);
	else
		wake_up(&evt->wq);
	spin_unlock(&evt->lock);

	return NT_STATUS_SUCCESS;
}

__u32 nt_reset_event(const __u64 *args)
{
	int handle = (int)args[0];
	struct axon_event *evt;

	evt = axon_handle_lookup(handle, AXON_HANDLE_EVENT);
	if (!evt)
		return NT_STATUS_INVALID_HANDLE;

	spin_lock(&evt->lock);
	evt->signaled = false;
	spin_unlock(&evt->lock);

	return NT_STATUS_SUCCESS;
}

__u32 nt_pulse_event(const __u64 *args)
{
	int handle = (int)args[0];
	struct axon_event *evt;

	evt = axon_handle_lookup(handle, AXON_HANDLE_EVENT);
	if (!evt)
		return NT_STATUS_INVALID_HANDLE;

	spin_lock(&evt->lock);
	evt->signaled = true;
	wake_up_all(&evt->wq);
	evt->signaled = false;
	spin_unlock(&evt->lock);

	return NT_STATUS_SUCCESS;
}

// ── Mutant (Mutex) Objects ──────────────────────────────────────────────────

struct axon_mutant {
	struct mutex mtx;
	bool owned;
	pid_t owner_pid;
};

__u32 nt_create_mutant(const __u64 *args)
{
	__u32 __user *handle_out = (__u32 __user *)args[0];
	struct axon_mutant *mut;
	int handle;

	if (!handle_out)
		return NT_STATUS_INVALID_PARAMETER;

	mut = kzalloc(sizeof(*mut), GFP_KERNEL);
	if (!mut)
		return NT_STATUS_NO_MEMORY;

	mutex_init(&mut->mtx);
	mut->owned = false;
	mut->owner_pid = 0;

	handle = axon_handle_alloc(AXON_HANDLE_MUTANT, mut);
	if (handle < 0) {
		kfree(mut);
		return NT_STATUS_NO_MEMORY;
	}

	if (copy_to_user(handle_out, &handle, sizeof(handle))) {
		axon_handle_free(handle);
		kfree(mut);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	pr_debug("NtCreateMutant: handle=%d\n", handle);
	return NT_STATUS_SUCCESS;
}

__u32 nt_open_mutant(const __u64 *args)
{
	pr_debug("NtOpenMutant: stub\n");
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_release_mutant(const __u64 *args)
{
	int handle = (int)args[0];
	struct axon_mutant *mut;

	mut = axon_handle_lookup(handle, AXON_HANDLE_MUTANT);
	if (!mut)
		return NT_STATUS_INVALID_HANDLE;

	if (!mut->owned || mut->owner_pid != task_tgid_vnr(current))
		return NT_STATUS_INVALID_PARAMETER;

	mut->owned = false;
	mut->owner_pid = 0;
	mutex_unlock(&mut->mtx);

	return NT_STATUS_SUCCESS;
}

// ── Semaphore Objects ───────────────────────────────────────────────────────

struct axon_semaphore {
	wait_queue_head_t wq;
	spinlock_t lock;
	atomic_t count;
	unsigned int max_count;
};

__u32 nt_create_semaphore(const __u64 *args)
{
	__u32 __user *handle_out = (__u32 __user *)args[0];
	__u32 max_count = (__u32)args[3];
	__s32 initial_count = (__s32)args[2];
	struct axon_semaphore *sem;
	int handle;

	if (!handle_out || max_count == 0 || initial_count < 0 ||
	    initial_count > max_count)
		return NT_STATUS_INVALID_PARAMETER;

	sem = kzalloc(sizeof(*sem), GFP_KERNEL);
	if (!sem)
		return NT_STATUS_NO_MEMORY;

	init_waitqueue_head(&sem->wq);
	spin_lock_init(&sem->lock);
	atomic_set(&sem->count, initial_count);
	sem->max_count = max_count;

	handle = axon_handle_alloc(AXON_HANDLE_SEMAPHORE, sem);
	if (handle < 0) {
		kfree(sem);
		return NT_STATUS_NO_MEMORY;
	}

	if (copy_to_user(handle_out, &handle, sizeof(handle))) {
		axon_handle_free(handle);
		kfree(sem);
		return NT_STATUS_ACCESS_VIOLATION;
	}

	pr_debug("NtCreateSemaphore: handle=%d max=%u init=%d\n",
		 handle, max_count, initial_count);
	return NT_STATUS_SUCCESS;
}

__u32 nt_release_semaphore(const __u64 *args)
{
	int handle = (int)args[0];
	__s32 release_count = (__s32)args[1];
	struct axon_semaphore *sem;
	int old, new;

	sem = axon_handle_lookup(handle, AXON_HANDLE_SEMAPHORE);
	if (!sem)
		return NT_STATUS_INVALID_HANDLE;

	if (release_count <= 0)
		return NT_STATUS_INVALID_PARAMETER;

	do {
		old = atomic_read(&sem->count);
		new = old + release_count;
		if (new > sem->max_count)
			return NT_STATUS_INVALID_PARAMETER;
	} while (atomic_cmpxchg(&sem->count, old, new) != old);

	wake_up_all(&sem->wq);
	return NT_STATUS_SUCCESS;
}

// ── Wait Functions ──────────────────────────────────────────────────────────

static __u32 wait_event_obj(struct axon_event *evt, bool alertable,
			    long timeout_jiffies)
{
	int ret;

	if (timeout_jiffies == 0) {
		/* Poll — no wait */
		bool s;

		spin_lock(&evt->lock);
		s = evt->signaled;
		if (s && !evt->manual_reset)
			evt->signaled = false;
		spin_unlock(&evt->lock);
		return s ? NT_STATUS_SUCCESS : NT_STATUS_TIMEOUT;
	}

	if (alertable) {
		ret = wait_event_interruptible_timeout(
			evt->wq,
			({
				bool s;
				spin_lock(&evt->lock);
				s = evt->signaled;
				if (s && !evt->manual_reset)
					evt->signaled = false;
				spin_unlock(&evt->lock);
				s;
			}),
			timeout_jiffies);
	} else {
		ret = wait_event_timeout(
			evt->wq,
			({
				bool s;
				spin_lock(&evt->lock);
				s = evt->signaled;
				if (s && !evt->manual_reset)
					evt->signaled = false;
				spin_unlock(&evt->lock);
				s;
			}),
			timeout_jiffies);
	}

	if (ret == 0)
		return NT_STATUS_TIMEOUT;
	if (ret < 0)
		return NT_STATUS_ACCESS_VIOLATION;
	return NT_STATUS_SUCCESS;
}

static __u32 wait_mutant_obj(struct axon_mutant *mut, bool alertable,
			     long timeout_jiffies)
{
	int ret;

	if (timeout_jiffies == 0) {
		if (mutex_trylock(&mut->mtx)) {
			mut->owned = true;
			mut->owner_pid = task_tgid_vnr(current);
			return NT_STATUS_SUCCESS;
		}
		return NT_STATUS_TIMEOUT;
	}

	if (alertable)
		ret = mutex_lock_interruptible(&mut->mtx);
	else
		ret = mutex_lock_killable(&mut->mtx);

	if (ret)
		return NT_STATUS_ACCESS_VIOLATION;

	mut->owned = true;
	mut->owner_pid = task_tgid_vnr(current);
	return NT_STATUS_SUCCESS;
}

static __u32 wait_semaphore_obj(struct axon_semaphore *sem, bool alertable,
				long timeout_jiffies)
{
	int ret;

	if (timeout_jiffies == 0) {
		/* Poll */
		int old;

		do {
			old = atomic_read(&sem->count);
			if (old <= 0)
				return NT_STATUS_TIMEOUT;
		} while (atomic_cmpxchg(&sem->count, old, old - 1) != old);
		return NT_STATUS_SUCCESS;
	}

	if (alertable) {
		ret = wait_event_interruptible_timeout(
			sem->wq,
			({
				int old;
				bool got = false;
				do {
					old = atomic_read(&sem->count);
					if (old > 0) {
						got = atomic_cmpxchg(&sem->count,
								     old,
								     old - 1) == old;
					}
				} while (!got && old > 0);
				got;
			}),
			timeout_jiffies);
	} else {
		ret = wait_event_timeout(
			sem->wq,
			({
				int old;
				bool got = false;
				do {
					old = atomic_read(&sem->count);
					if (old > 0) {
						got = atomic_cmpxchg(&sem->count,
								     old,
								     old - 1) == old;
					}
				} while (!got && old > 0);
				got;
			}),
			timeout_jiffies);
	}

	if (ret == 0)
		return NT_STATUS_TIMEOUT;
	if (ret < 0)
		return NT_STATUS_ACCESS_VIOLATION;
	return NT_STATUS_SUCCESS;
}

__u32 nt_wait_for_single_object(const __u64 *args)
{
	int handle = (int)args[0];
	bool alertable = !!args[1];
	__s64 __user *timeout_ptr = (__s64 __user *)args[2];
	__s64 timeout_val = -1;
	long timeout_jiffies;
	void *obj;

	if (timeout_ptr && access_ok(timeout_ptr, sizeof(*timeout_ptr)))
		copy_from_user(&timeout_val, timeout_ptr, sizeof(timeout_val));

	if (timeout_val < 0) {
		if (timeout_val == -1)
			timeout_jiffies = MAX_SCHEDULE_TIMEOUT;
		else
			timeout_jiffies = usecs_to_jiffies(
				(unsigned long)(-timeout_val / 10));
	} else if (timeout_val == 0) {
		timeout_jiffies = 0;
	} else {
		timeout_jiffies = usecs_to_jiffies(
			(unsigned long)(timeout_val / 10));
	}

	obj = axon_handle_lookup(handle, AXON_HANDLE_EVENT);
	if (obj)
		return wait_event_obj(obj, alertable, timeout_jiffies);

	obj = axon_handle_lookup(handle, AXON_HANDLE_MUTANT);
	if (obj)
		return wait_mutant_obj(obj, alertable, timeout_jiffies);

	obj = axon_handle_lookup(handle, AXON_HANDLE_SEMAPHORE);
	if (obj)
		return wait_semaphore_obj(obj, alertable, timeout_jiffies);

	return NT_STATUS_INVALID_HANDLE;
}

__u32 nt_wait_for_multiple_objects(const __u64 *args)
{
	pr_debug("NtWaitForMultipleObjects: stub\n");
	return NT_STATUS_NOT_IMPLEMENTED;
}
