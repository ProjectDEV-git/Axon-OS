/*
 * win_tap_filter — eBPF program definitions
 */

#ifndef WIN_TAP_FILTER_H
#define WIN_TAP_FILTER_H

/* TAP device name to attach to */
#define TAP_DEVICE_NAME "winrunner-tap0"

/* DSCP value for Expedited Forwarding */
#define DSCP_EF 0xB8

#endif /* WIN_TAP_FILTER_H */
