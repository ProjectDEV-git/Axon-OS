/* shell32.c — Stub shell32.dll for the Axon Windows ABI.
 * Provides shell operations: execute, path queries, file operations.
 * User-space code linked into the DLL loader image.
 */

#include <string.h>

/* ── Type Definitions ────────────────────────────────────────────────────── */

typedef void *HWND;
typedef void *HINSTANCE;
typedef int BOOL;
typedef int INT;
typedef unsigned int UINT;
typedef const char *LPCSTR;
typedef char *LPSTR;
typedef const unsigned short *LPCWSTR;
typedef void *LPVOID;
typedef void *LPCVOID;
typedef UINT FILEOP_FLAGS;

#define TRUE  1
#define FALSE 0

/* SHFILEOPSTRUCTA — used by SHFileOperationA */
typedef struct {
	HWND   hwnd;
	UINT   wFunc;
	LPCSTR pFrom;
	LPCSTR pTo;
	FILEOP_FLAGS fFlags;
	BOOL   fAnyOperationsAborted;
	LPVOID hNameMappings;
	LPCSTR lpszProgressTitle;
} SHFILEOPSTRUCTA, *LPSHFILEOPSTRUCTA;

/* ── Shell Functions ─────────────────────────────────────────────────────── */

HINSTANCE ShellExecuteA(HWND hwnd, LPCSTR lpOperation, LPCSTR lpFile,
			LPCSTR lpParameters, LPCSTR lpDirectory, INT nShowCmd)
{
	/* Stub: return success status (value > 32 per Win32 convention) */
	(void)hwnd;
	(void)lpOperation;
	(void)lpFile;
	(void)lpParameters;
	(void)lpDirectory;
	(void)nShowCmd;
	return (HINSTANCE)(long)32;
}

HINSTANCE ShellExecuteW(HWND hwnd, LPCWSTR lpOperation, LPCWSTR lpFile,
			LPCWSTR lpParameters, LPCWSTR lpDirectory, INT nShowCmd)
{
	/* Stub: same success return as ANSI version */
	(void)hwnd;
	(void)lpOperation;
	(void)lpFile;
	(void)lpParameters;
	(void)lpDirectory;
	(void)nShowCmd;
	return (HINSTANCE)(long)32;
}

LPSTR *CommandLineToArgvW(LPCWSTR lpCmdLine, int *pNumArgs)
{
	/* Stub: argument parsing not implemented */
	(void)lpCmdLine;
	if (pNumArgs)
		*pNumArgs = 0;
	return NULL;
}

BOOL SHGetSpecialFolderPathA(HWND hwnd, LPSTR pszPath, int csidl,
			      BOOL fCreate)
{
	/* Stub: map all special folders to /tmp */
	(void)hwnd;
	(void)csidl;
	(void)fCreate;

	if (!pszPath)
		return FALSE;

	strcpy(pszPath, "/tmp");
	return TRUE;
}

int SHFileOperationA(LPSHFILEOPSTRUCTA lpFileOp)
{
	/* Stub: file operations not implemented */
	(void)lpFileOp;
	return 0;
}

void SHChangeNotify(int wEventId, UINT uFlags, LPCVOID dwItem1,
		    LPCVOID dwItem2)
{
	/* No-op: shell change notifications are not tracked */
	(void)wEventId;
	(void)uFlags;
	(void)dwItem1;
	(void)dwItem2;
}
