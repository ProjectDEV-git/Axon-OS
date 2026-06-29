/*
 * user32.dll stub library for Axon Windows ABI
 * Phase 3: stub implementations returning success/default values
 * Future: wire to GTK4/Wayland backend
 */

#include <stdio.h>
#include <wchar.h>

/* ── Windows-compatible type definitions ── */

typedef void          *HWND;
typedef void          *HDC;
typedef void          *HMENU;
typedef void          *HINSTANCE;
typedef unsigned int   UINT;
typedef unsigned long long WPARAM;
typedef long long      LPARAM;
typedef long long      LRESULT;
typedef unsigned long  DWORD;
typedef int            BOOL;
typedef const char    *LPCSTR;
typedef const wchar_t *LPCWSTR;
typedef char          *LPSTR;
typedef void          *LPVOID;
typedef void          *HBRUSH;
typedef unsigned int   COLORREF;

typedef struct tagRECT {
    long left;
    long top;
    long right;
    long bottom;
} RECT, *LPRECT;

typedef struct tagPOINT {
    long x;
    long y;
} POINT;

typedef struct tagMSG {
    HWND   hwnd;
    UINT   message;
    WPARAM wParam;
    LPARAM lParam;
    DWORD  time;
    POINT  pt;
} MSG, *LPMSG;

#define TRUE   1
#define FALSE  0
#define IDOK   1

#define SM_CXSCREEN 0
#define SM_CYSCREEN 1

/* ── Message Box ── */

int MessageBoxA(HWND hWnd, LPCSTR lpText, LPCSTR lpCaption, UINT uType)
{
    (void)hWnd;
    (void)uType;
    fprintf(stderr, "[user32] MessageBoxA: %s — %s\n",
            lpCaption ? lpCaption : "(null)",
            lpText ? lpText : "(null)");
    return IDOK;
}

int MessageBoxW(HWND hWnd, LPCWSTR lpText, LPCWSTR lpCaption, UINT uType)
{
    (void)hWnd;
    (void)uType;
    fprintf(stderr, "[user32] MessageBoxW: (wide string)\n");
    (void)lpText;
    (void)lpCaption;
    return IDOK;
}

/* ── Window Creation / Destruction ── */

HWND CreateWindowExA(DWORD dwExStyle, LPCSTR lpClassName, LPCSTR lpWindowName,
                     DWORD dwStyle, int X, int Y, int nWidth, int nHeight,
                     HWND hWndParent, HMENU hMenu, HINSTANCE hInstance,
                     LPVOID lpParam)
{
    (void)dwExStyle; (void)lpClassName; (void)lpWindowName;
    (void)dwStyle; (void)X; (void)Y; (void)nWidth; (void)nHeight;
    (void)hWndParent; (void)hMenu; (void)hInstance; (void)lpParam;
    return (HWND)1;
}

BOOL ShowWindow(HWND hWnd, int nCmdShow)
{
    (void)hWnd;
    (void)nCmdShow;
    return TRUE;
}

BOOL UpdateWindow(HWND hWnd)
{
    (void)hWnd;
    return TRUE;
}

BOOL DestroyWindow(HWND hWnd)
{
    (void)hWnd;
    return TRUE;
}

/* ── Window Procedures ── */

LRESULT DefWindowProcA(HWND hWnd, UINT Msg, WPARAM wParam, LPARAM lParam)
{
    (void)hWnd; (void)Msg; (void)wParam; (void)lParam;
    return 0;
}

LRESULT DefWindowProcW(HWND hWnd, UINT Msg, WPARAM wParam, LPARAM lParam)
{
    (void)hWnd; (void)Msg; (void)wParam; (void)lParam;
    return 0;
}

/* ── Message Loop ── */

BOOL TranslateMessage(const MSG *lpMsg)
{
    (void)lpMsg;
    return FALSE;
}

LRESULT DispatchMessageA(const MSG *lpMsg)
{
    (void)lpMsg;
    return 0;
}

BOOL GetMessageA(LPMSG lpMsg, HWND hWnd, UINT wMsgFilterMin, UINT wMsgFilterMax)
{
    (void)lpMsg; (void)hWnd; (void)wMsgFilterMin; (void)wMsgFilterMax;
    return FALSE;
}

BOOL PeekMessageA(LPMSG lpMsg, HWND hWnd, UINT wMsgFilterMin,
                  UINT wMsgFilterMax, UINT wRemoveMsg)
{
    (void)lpMsg; (void)hWnd; (void)wMsgFilterMin;
    (void)wMsgFilterMax; (void)wRemoveMsg;
    return FALSE;
}

BOOL PostMessageA(HWND hWnd, UINT Msg, WPARAM wParam, LPARAM lParam)
{
    (void)hWnd; (void)Msg; (void)wParam; (void)lParam;
    return FALSE;
}

LRESULT SendMessageA(HWND hWnd, UINT Msg, WPARAM wParam, LPARAM lParam)
{
    (void)hWnd; (void)Msg; (void)wParam; (void)lParam;
    return 0;
}

/* ── Window Information ── */

HWND GetDesktopWindow(void)
{
    return (HWND)1;
}

HWND GetForegroundWindow(void)
{
    return (HWND)1;
}

int GetSystemMetrics(int nIndex)
{
    switch (nIndex) {
    case SM_CXSCREEN: return 1920;
    case SM_CYSCREEN: return 1080;
    default:          return 0;
    }
}

BOOL SetWindowTextA(HWND hWnd, LPCSTR lpString)
{
    (void)hWnd;
    (void)lpString;
    return TRUE;
}

int GetWindowTextA(HWND hWnd, LPSTR lpString, int nMaxCount)
{
    (void)hWnd;
    if (lpString && nMaxCount > 0)
        lpString[0] = '\0';
    return 0;
}

/* ── Device Context ── */

HDC GetDC(HWND hWnd)
{
    (void)hWnd;
    return (HDC)1;
}

int ReleaseDC(HWND hWnd, HDC hDC)
{
    (void)hWnd;
    (void)hDC;
    return 1;
}

/* ── Painting ── */

BOOL InvalidateRect(HWND hWnd, const RECT *lpRect, BOOL bErase)
{
    (void)hWnd;
    (void)lpRect;
    (void)bErase;
    return TRUE;
}

BOOL GetClientRect(HWND hWnd, LPRECT lpRect)
{
    (void)hWnd;
    if (lpRect) {
        lpRect->left   = 0;
        lpRect->top    = 0;
        lpRect->right  = 800;
        lpRect->bottom = 600;
    }
    return TRUE;
}
