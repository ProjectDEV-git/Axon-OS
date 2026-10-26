/*
 * win_tap_filter — eBPF TC classifier for DSCP marking
 *
 * Attached to winrunner-tap0 ingress/egress.
 * Matches UDP packets (protocol 17) and sets DSCP EF (TOS 0xB8)
 * for QoS prioritization of real-time traffic (games, VoIP).
 *
 * SPDX-License-Identifier: GPL-2.0
 */

#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>
#include "win_tap_filter.h"

/* Ethernet header */
struct ethhdr {
    __u8  h_dest[6];
    __u8  h_source[6];
    __u16 h_proto;
} __attribute__((packed));

/* IPv4 header (minimal) */
struct iphdr {
    __u8  ihl_version;
    __u8  tos;
    __u16 tot_len;
    __u16 id;
    __u16 frag_off;
    __u8  ttl;
    __u8  protocol;
    __u16 check;
    __u32 saddr;
    __u32 daddr;
} __attribute__((packed));

#define ETH_P_IP    0x0800
#define IPPROTO_UDP 17
#define ETH_HLEN_   14
#define IP_OFF_TOS  (ETH_HLEN_ + 1)
#define IP_OFF_CSUM (ETH_HLEN_ + 10)

SEC("tc")
int tc_dscp_mark(struct __sk_buff *skb)
{
    void *data     = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;

    /* Parse Ethernet header */
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return TC_ACT_OK;

    if (bpf_ntohs(eth->h_proto) != ETH_P_IP)
        return TC_ACT_OK;

    /* Parse IPv4 header */
    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return TC_ACT_OK;

    /* Version must be 4 and IHL at least 5 (20 bytes); anything else is
     * malformed and left untouched. */
    if ((ip->ihl_version >> 4) != 4 || (ip->ihl_version & 0x0F) < 5)
        return TC_ACT_OK;

    /* Only mark UDP packets */
    if (ip->protocol != IPPROTO_UDP)
        return TC_ACT_OK;

    __u8 old_tos = ip->tos;
    __u8 new_tos = (old_tos & 0x03) | DSCP_EF;
    if (new_tos == old_tos)
        return TC_ACT_OK;

    /* Rewriting TOS in place without fixing the IPv4 header checksum made
     * every marked packet fail validation and get dropped by the receiving
     * stack. Update the checksum incrementally for the changed 16-bit word
     * (TOS is the low byte of the first word: version/IHL | TOS). */
    __u16 old_word = bpf_htons(((__u16)ip->ihl_version << 8) | old_tos);
    __u16 new_word = bpf_htons(((__u16)ip->ihl_version << 8) | new_tos);
    if (bpf_l3_csum_replace(skb, IP_OFF_CSUM, old_word, new_word, sizeof(__u16)) < 0)
        return TC_ACT_OK;
    /* Helpers that modify the packet invalidate data pointers: write via
     * bpf_skb_store_bytes rather than through `ip`. */
    if (bpf_skb_store_bytes(skb, IP_OFF_TOS, &new_tos, sizeof(new_tos), 0) < 0)
        return TC_ACT_OK;

    return TC_ACT_OK;
}

char _license[] SEC("license") = "GPL";
