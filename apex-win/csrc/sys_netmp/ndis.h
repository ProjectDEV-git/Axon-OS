/*
 * sys_netmp — NDIS type definitions
 */

#ifndef SYS_NETMP_NDIS_H
#define SYS_NETMP_NDIS_H

#include <windows.h>

/* NDIS packet header (simplified) */
typedef struct _NDIS_PACKET_HEADER {
    USHORT EtherType;
    UCHAR  DestMac[6];
    UCHAR  SrcMac[6];
} NDIS_PACKET_HEADER;

/* NDIS link status */
typedef struct _NDIS_LINK_STATUS {
    DWORD LinkSpeed;    /* in 100 bps units */
    DWORD LinkState;    /* 0=down, 1=up */
    DWORD MediaState;   /* 0=disconnected, 1=connected */
} NDIS_LINK_STATUS;

#endif /* SYS_NETMP_NDIS_H */
