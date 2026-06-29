// nt-registry.c — Windows NT registry for the Axon Windows ABI (Phase 2).
// Read-only in-memory registry with pre-populated default keys.

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/idr.h>
#include <linux/mutex.h>
#include <linux/list.h>

#include "axon-winabi.h"

#define REG_SZ      1
#define REG_DWORD   4
#define REG_BINARY  3

#define KEY_VALUE_BASIC_INFORMATION   0
#define KEY_VALUE_FULL_INFORMATION    1

#define NT_STATUS_OBJECT_NAME_NOT_FOUND 0xC0000034
#define NT_STATUS_BUFFER_TOO_SMALL      0xC0000023
#define NT_STATUS_INVALID_HANDLE        0xC0000008

#define REG_HANDLE_BASE 0x8000

struct reg_value {
	char name[256];
	u32 type;
	u32 data_len;
	void *data;
	struct list_head node;
};

struct reg_key {
	char name[256];
	struct list_head values;
	struct list_head children;
	struct list_head node;
	struct reg_key *parent;
};

struct reg_open_handle {
	struct reg_key *key;
};

static struct reg_key *registry_root;
static DEFINE_IDR(reg_handle_idr);
static DEFINE_MUTEX(reg_mutex);

static struct reg_key *reg_key_create(const char *name, struct reg_key *parent)
{
	struct reg_key *key = kzalloc(sizeof(*key), GFP_KERNEL);
	if (!key)
		return NULL;
	strscpy(key->name, name, sizeof(key->name));
	INIT_LIST_HEAD(&key->values);
	INIT_LIST_HEAD(&key->children);
	key->parent = parent;
	if (parent)
		list_add_tail(&key->node, &parent->children);
	return key;
}

static struct reg_value *reg_value_create(const char *name, u32 type,
					  const void *data, u32 data_len)
{
	struct reg_value *val = kzalloc(sizeof(*val), GFP_KERNEL);
	if (!val)
		return NULL;
	strscpy(val->name, name, sizeof(val->name));
	val->type = type;
	val->data_len = data_len;
	if (data_len > 0) {
		val->data = kmemdup(data, data_len, GFP_KERNEL);
		if (!val->data) {
			kfree(val);
			return NULL;
		}
	}
	INIT_LIST_HEAD(&val->node);
	return val;
}

static void reg_key_destroy_recursive(struct reg_key *key)
{
	struct reg_value *val, *vt;
	struct reg_key *child, *ct;

	if (!key)
		return;
	list_for_each_entry_safe(child, ct, &key->children, node)
		reg_key_destroy_recursive(child);
	list_for_each_entry_safe(val, vt, &key->values, node) {
		list_del(&val->node);
		kfree(val->data);
		kfree(val);
	}
	if (key->parent)
		list_del(&key->node);
	kfree(key);
}

static struct reg_key *reg_find_child(struct reg_key *parent, const char *name)
{
	struct reg_key *child;
	list_for_each_entry(child, &parent->children, node)
		if (strcasecmp(child->name, name) == 0)
			return child;
	return NULL;
}

static struct reg_value *reg_find_value(struct reg_key *key, const char *name)
{
	struct reg_value *val;
	list_for_each_entry(val, &key->values, node)
		if (strcmp(val->name, name) == 0)
			return val;
	return NULL;
}

static struct reg_key *reg_lookup_path(const char *path)
{
	struct reg_key *cur;
	char buf[512];
	char *p, *token;

	strscpy(buf, path, sizeof(buf));
	p = buf;

	if (strncasecmp(p, "HKEY_LOCAL_MACHINE\\", 19) == 0)
		p += 19;
	else if (strncasecmp(p, "HKLM\\", 5) == 0)
		p += 5;
	else if (strncasecmp(p, "HKEY_CURRENT_USER\\", 18) == 0)
		p += 18;
	else if (strncasecmp(p, "HKCU\\", 5) == 0)
		p += 5;

	while (*p == '\\')
		p++;

	cur = registry_root;
	if (!cur || !*p)
		return cur;

	while ((token = strsep(&p, "\\")) != NULL) {
		if (!*token)
			continue;
		cur = reg_find_child(cur, token);
		if (!cur)
			return NULL;
	}
	return cur;
}

static int reg_handle_alloc(struct reg_key *key)
{
	struct reg_open_handle *h = kzalloc(sizeof(*h), GFP_KERNEL);
	int id;
	if (!h)
		return -ENOMEM;
	h->key = key;
	mutex_lock(&reg_mutex);
	id = idr_alloc(&reg_handle_idr, h, REG_HANDLE_BASE, 0, GFP_KERNEL);
	mutex_unlock(&reg_mutex);
	if (id < 0) {
		kfree(h);
		return id;
	}
	return id;
}

static struct reg_key *reg_handle_lookup(int handle)
{
	struct reg_open_handle *h;
	mutex_lock(&reg_mutex);
	h = idr_find(&reg_handle_idr, handle);
	mutex_unlock(&reg_mutex);
	return h ? h->key : NULL;
}

static void reg_handle_close(int handle)
{
	struct reg_open_handle *h;
	mutex_lock(&reg_mutex);
	h = idr_remove(&reg_handle_idr, handle);
	mutex_unlock(&reg_mutex);
	kfree(h);
}

static int read_unicode_string(char *out, size_t out_size, void __user *ustr_ptr)
{
	u16 hdr[4]; // length, max_length, pad
	void __user *buffer;
	u16 wlen, copy_len, i, j, wc;

	if (!ustr_ptr)
		return -EINVAL;

	// UNICODE_STRING layout: { u16 length, u16 maxlen, pad, wchar* buffer }
	if (copy_from_user(hdr, ustr_ptr, sizeof(hdr)))
		return -EFAULT;
	if (copy_from_user(&buffer, ustr_ptr + 8, sizeof(buffer)))
		return -EFAULT;

	if (!buffer || hdr[0] == 0)
		return -EINVAL;

	wlen = hdr[0] / 2;
	copy_len = min_t(u16, wlen, out_size - 1);

	for (i = 0, j = 0; i < copy_len; i++) {
		if (copy_from_user(&wc, buffer + i * 2, sizeof(wc)))
			return -EFAULT;
		out[j++] = (wc < 128) ? (char)wc : '?';
	}
	out[j] = '\0';
	return 0;
}

static void reg_add_string(struct reg_key *key, const char *name,
			   const char *str)
{
	struct reg_value *val;

	val = reg_value_create(name, REG_SZ, str, strlen(str) + 1);
	if (val)
		list_add_tail(&val->node, &key->values);
}

static void reg_add_dword(struct reg_key *key, const char *name, u32 data)
{
	struct reg_value *val;

	val = reg_value_create(name, REG_DWORD, &data, sizeof(u32));
	if (val)
		list_add_tail(&val->node, &key->values);
}

static void reg_populate_defaults(void)
{
	struct reg_key *hklm, *cv, *ctrl, *hkcu, *env;

	hklm = reg_key_create("HKEY_LOCAL_MACHINE", registry_root);
	cv = reg_key_create("SOFTWARE", hklm);
	cv = reg_key_create("Microsoft", cv);
	cv = reg_key_create("Windows NT", cv);
	cv = reg_key_create("CurrentVersion", cv);

	reg_add_string(cv, "ProductName",
		       "Axon OS Windows Compatibility Layer");
	reg_add_string(cv, "CurrentVersion", "6.3");
	reg_add_string(cv, "CurrentBuildNumber", "9600");
	reg_add_string(cv, "CSDVersion", "");
	reg_add_string(cv, "SystemRoot", "C:\\Windows");
	reg_add_dword(cv, "CurrentMajorVersionNumber", 6);
	reg_add_dword(cv, "CurrentMinorVersionNumber", 3);
	reg_add_string(cv, "RegisteredOrganization", "");
	reg_add_string(cv, "RegisteredOwner", "Axon OS User");

	ctrl = reg_key_create("SYSTEM", hklm);
	ctrl = reg_key_create("CurrentControlSet", ctrl);
	ctrl = reg_key_create("Control", ctrl);

	reg_add_string(ctrl, "SystemBootDevice", "");
	reg_add_dword(ctrl, "BootDriverFlags", 0);

	hkcu = reg_key_create("HKEY_CURRENT_USER", registry_root);
	env = reg_key_create("Environment", hkcu);

	reg_add_string(env, "TEMP",
		       "C:\\Users\\Default\\AppData\\Local\\Temp");
	reg_add_string(env, "TMP",
		       "C:\\Users\\Default\\AppData\\Local\\Temp");

	pr_info("registry: populated default keys\n");
}

int axon_registry_init(void)
{
	registry_root = reg_key_create("", NULL);
	if (!registry_root)
		return -ENOMEM;
	idr_init(&reg_handle_idr);
	reg_populate_defaults();
	pr_info("registry: initialized\n");
	return 0;
}

void axon_registry_exit(void)
{
	struct reg_open_handle *h;
	int id;

	mutex_lock(&reg_mutex);
	idr_for_each_entry(&reg_handle_idr, h, id) {
		idr_remove(&reg_handle_idr, id);
		kfree(h);
	}
	idr_destroy(&reg_handle_idr);
	mutex_unlock(&reg_mutex);
	reg_key_destroy_recursive(registry_root);
	registry_root = NULL;
	pr_info("registry: cleaned up\n");
}

__u32 nt_open_key(const __u64 *args)
{
	u32 __user *handle_out = (u32 __user *)args[0];
	void __user *oa_ptr = (void __user *)args[2];
	struct reg_key *key;
	char name_buf[512];
	void __user *ustr_ptr;
	int handle;

	if (!oa_ptr)
		return NT_STATUS_INVALID_PARAMETER;

	// OBJECT_ATTRIBUTES: ObjectName PUNICODE_STRING at +0x10
	if (copy_from_user(&ustr_ptr, oa_ptr + 0x10, sizeof(ustr_ptr)))
		return NT_STATUS_ACCESS_VIOLATION;
	if (read_unicode_string(name_buf, sizeof(name_buf), ustr_ptr))
		return NT_STATUS_INVALID_PARAMETER;

	pr_debug("NtOpenKey: path='%s'\n", name_buf);

	mutex_lock(&reg_mutex);
	key = reg_lookup_path(name_buf);
	mutex_unlock(&reg_mutex);

	if (!key)
		return NT_STATUS_OBJECT_NAME_NOT_FOUND;

	handle = reg_handle_alloc(key);
	if (handle < 0)
		return NT_STATUS_NO_MEMORY;

	if (handle_out && access_ok(handle_out, sizeof(u32))) {
		u32 h = (u32)handle;
		if (copy_to_user(handle_out, &h, sizeof(h))) {
			reg_handle_close(handle);
			return NT_STATUS_ACCESS_VIOLATION;
		}
	}

	pr_debug("NtOpenKey: handle=0x%x\n", handle);
	return NT_STATUS_SUCCESS;
}

__u32 nt_query_value_key(const __u64 *args)
{
	u32 handle = (u32)args[0];
	u32 info_class = (u32)args[2];
	void __user *out_buf = (void __user *)args[3];
	u32 out_len = (u32)args[4];
	u32 __user *result_len = (u32 __user *)args[5];
	struct reg_key *key;
	struct reg_value *val;
	char vname[256];
	u32 name_len, needed;

	key = reg_handle_lookup(handle);
	if (!key)
		return NT_STATUS_INVALID_HANDLE;

	if (read_unicode_string(vname, sizeof(vname),
				(void __user *)args[1]))
		return NT_STATUS_INVALID_PARAMETER;

	val = reg_find_value(key, vname);
	if (!val)
		return NT_STATUS_OBJECT_NAME_NOT_FOUND;

	name_len = strlen(val->name);

	if (info_class == KEY_VALUE_BASIC_INFORMATION) {
		needed = 8 + ALIGN(name_len + 1, 2);
		if (result_len && access_ok(result_len, 4))
			copy_to_user(result_len, &needed, 4);
		if (out_len < needed)
			return NT_STATUS_BUFFER_TOO_SMALL;
		if (out_buf && access_ok(out_buf, needed)) {
			u8 tmp[280];
			u32 off = 0;

			memset(tmp, 0, sizeof(tmp));
			memcpy(&tmp[off], &name_len, 4); off += 4;
			memcpy(&tmp[off], &val->type, 4); off += 4;
			memcpy(&tmp[off], val->name, name_len);
			copy_to_user(out_buf, tmp, 8 + ALIGN(name_len + 1, 2));
		}
		return NT_STATUS_SUCCESS;
	}

	if (info_class == KEY_VALUE_FULL_INFORMATION) {
		u32 data_off = 12 + ALIGN(name_len + 1, 2);
		u8 *tmp;

		needed = data_off + val->data_len;
		if (result_len && access_ok(result_len, 4))
			copy_to_user(result_len, &needed, 4);
		if (out_len < needed)
			return NT_STATUS_BUFFER_TOO_SMALL;

		tmp = kzalloc(needed, GFP_KERNEL);
		if (!tmp)
			return NT_STATUS_NO_MEMORY;

		memcpy(tmp + 0, &name_len, 4);
		memcpy(tmp + 4, &val->type, 4);
		memcpy(tmp + 8, &val->data_len, 4);
		memcpy(tmp + 12, val->name, name_len);
		if (val->data_len > 0)
			memcpy(tmp + data_off, val->data, val->data_len);

		if (out_buf && access_ok(out_buf, needed))
			copy_to_user(out_buf, tmp, needed);
		kfree(tmp);
		return NT_STATUS_SUCCESS;
	}

	return NT_STATUS_INVALID_PARAMETER;
}

__u32 nt_query_key(const __u64 *args)
{
	if (!reg_handle_lookup((u32)args[0]))
		return NT_STATUS_INVALID_HANDLE;
	return NT_STATUS_NOT_IMPLEMENTED;
}

__u32 nt_registry_close(u32 handle)
{
	if (!reg_handle_lookup(handle))
		return 0;
	reg_handle_close(handle);
	return NT_STATUS_SUCCESS;
}

bool nt_is_registry_handle(u32 handle)
{
	return reg_handle_lookup(handle) != NULL;
}
