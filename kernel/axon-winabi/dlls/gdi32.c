/*
 * gdi32.dll stub library for Axon Windows ABI
 * Phase 3: stub implementations returning success/default values
 * Future: wire to Cairo/Skia rendering backend
 */

#include <stddef.h>

/* ── Windows-compatible type definitions ── */

typedef void          *HDC;
typedef void          *HBITMAP;
typedef void          *HFONT;
typedef void          *HPEN;
typedef void          *HBRUSH;
typedef unsigned int   UINT;
typedef unsigned long  DWORD;
typedef int            BOOL;
typedef const char    *LPCSTR;
typedef unsigned int   COLORREF;

#define TRUE  1
#define FALSE 0

/* ── Device Context Management ── */

HDC CreateCompatibleDC(HDC hdc)
{
    (void)hdc;
    return (HDC)2;
}

BOOL DeleteDC(HDC hdc)
{
    (void)hdc;
    return TRUE;
}

/* ── Bitmap ── */

HBITMAP CreateBitmap(int nWidth, int nHeight, UINT nPlanes,
                     UINT nBitCount, const void *lpBits)
{
    (void)nWidth; (void)nHeight; (void)nPlanes;
    (void)nBitCount; (void)lpBits;
    return (HBITMAP)1;
}

HBITMAP CreateCompatibleBitmap(HDC hdc, int cx, int cy)
{
    (void)hdc; (void)cx; (void)cy;
    return (HBITMAP)1;
}

/* ── Object Selection ── */

void *SelectObject(HDC hdc, void *h)
{
    (void)hdc; (void)h;
    return (void *)1;
}

BOOL DeleteObject(void *ho)
{
    (void)ho;
    return TRUE;
}

/* ── Bit Block Transfer ── */

BOOL BitBlt(HDC hdc, int x, int y, int cx, int cy,
             HDC hdcSrc, int x1, int y1, DWORD rop)
{
    (void)hdc; (void)x; (void)y; (void)cx; (void)cy;
    (void)hdcSrc; (void)x1; (void)y1; (void)rop;
    return TRUE;
}

/* ── Text Rendering ── */

int SetBkMode(HDC hdc, int mode)
{
    (void)hdc; (void)mode;
    return 1;
}

COLORREF SetTextColor(HDC hdc, COLORREF color)
{
    (void)hdc; (void)color;
    return 0;
}

BOOL TextOutA(HDC hdc, int x, int y, LPCSTR lpString, int c)
{
    (void)hdc; (void)x; (void)y; (void)lpString; (void)c;
    return TRUE;
}

/* ── Shape Drawing ── */

BOOL Rectangle(HDC hdc, int left, int top, int right, int bottom)
{
    (void)hdc; (void)left; (void)top; (void)right; (void)bottom;
    return TRUE;
}

BOOL Ellipse(HDC hdc, int left, int top, int right, int bottom)
{
    (void)hdc; (void)left; (void)top; (void)right; (void)bottom;
    return TRUE;
}

/* ── Font / Pen / Brush Creation ── */

HFONT CreateFontA(int cHeight, int cWidth, int cEscapement,
                  int cOrientation, int cWeight, DWORD bItalic,
                  DWORD bUnderline, DWORD bStrikeOut, DWORD iCharSet,
                  DWORD iOutPrecision, DWORD iClipPrecision,
                  DWORD iQuality, DWORD iPitchAndFamily,
                  LPCSTR pszFaceName)
{
    (void)cHeight; (void)cWidth; (void)cEscapement; (void)cOrientation;
    (void)cWeight; (void)bItalic; (void)bUnderline; (void)bStrikeOut;
    (void)iCharSet; (void)iOutPrecision; (void)iClipPrecision;
    (void)iQuality; (void)iPitchAndFamily; (void)pszFaceName;
    return (HFONT)1;
}

HPEN CreatePen(int iStyle, int cWidth, COLORREF color)
{
    (void)iStyle; (void)cWidth; (void)color;
    return (HPEN)1;
}

HBRUSH CreateSolidBrush(COLORREF color)
{
    (void)color;
    return (HBRUSH)1;
}
