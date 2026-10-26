/*
 * win_tap_filter — eBPF userspace loader
 *
 * Loads the eBPF object and attaches it as a TC classifier
 * to the winrunner-tap0 interface. Separate binary so the
 * main runner doesn't need root.
 *
 * Usage: win_tap_filter-loader [path/to/win_tap_filter.bpf.o]
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>
#include <net/if.h>

#define TAP_DEVICE_NAME "winrunner-tap0"
#define DEFAULT_OBJ_PATH "/usr/lib/win_tap_filter/win_tap_filter.bpf.o"

int main(int argc, char *argv[])
{
    const char *obj_path = (argc > 1) ? argv[1] : DEFAULT_OBJ_PATH;
    struct bpf_object *obj = NULL;
    struct bpf_program *prog = NULL;
    int ifindex;
    int err;

    /* Get interface index */
    ifindex = if_nametoindex(TAP_DEVICE_NAME);
    if (ifindex == 0) {
        fprintf(stderr, "Interface %s not found\n", TAP_DEVICE_NAME);
        return 1;
    }

    /* Load eBPF object */
    obj = bpf_object__open(obj_path);
    err = libbpf_get_error(obj);
    if (err) {
        /* bpf_object__open returns an error-encoded pointer, not NULL; the
         * old code then passed it to bpf_object__close (invalid free). */
        fprintf(stderr, "Failed to open eBPF object %s: %s\n", obj_path, strerror((int)-err));
        return 1;
    }

    err = bpf_object__load(obj);
    if (err) {
        fprintf(stderr, "Failed to load eBPF object: %d\n", err);
        goto cleanup;
    }

    /* Find the TC program */
    prog = bpf_object__find_program_by_name(obj, "tc_dscp_mark");
    if (!prog) {
        fprintf(stderr, "Program tc_dscp_mark not found\n");
        err = 1;
        goto cleanup;
    }

    /* Create TC hook on ingress */
    DECLARE_LIBBPF_OPTS(bpf_tc_hook, hook,
        .ifindex = ifindex,
        .attach_point = BPF_TC_INGRESS,
    );

    err = bpf_tc_hook_create(&hook);
    if (err && err != -EEXIST) {
        fprintf(stderr, "Failed to create TC hook: %d\n", err);
        goto cleanup;
    }

    /* Attach the eBPF program */
    DECLARE_LIBBPF_OPTS(bpf_tc_opts, opts,
        .prog_fd = bpf_program__fd(prog),
        .flags = BPF_F_ALLOW_MULTI,
    );

    err = bpf_tc_attach(&hook, &opts);
    if (err) {
        fprintf(stderr, "Failed to attach TC program: %d\n", err);
        goto cleanup;
    }

    printf("eBPF TC classifier attached to %s (ifindex %d)\n",
           TAP_DEVICE_NAME, ifindex);

cleanup:
    /* Closing the object after a successful attach is fine: the TC link
     * holds its own reference to the program. */
    bpf_object__close(obj);
    /* Exit status is 0..255; a raw negative errno truncates unpredictably. */
    return err ? 1 : 0;
}
