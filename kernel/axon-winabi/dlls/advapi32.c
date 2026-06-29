/* advapi32.c — Stub advapi32.dll for the Axon Windows ABI.
 * Registry functions delegate to NT syscalls; security/crypto are stubs.
 * User-space code linked into the DLL loader image.
 */

#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>

/* ── Type Definitions ────────────────────────────────────────────────────── */

typedef void *HKEY;
typedef HKEY *PHKEY;
typedef unsigned long DWORD;
typedef unsigned long *LPDWORD;
typedef unsigned char *LPBYTE;
typedef const unsigned char *LPCBYTE;
typedef int BOOL;
typedef const char *LPCSTR;
typedef char *LPSTR;
typedef void *HANDLE;
typedef HANDLE *PHANDLE;
typedef void *LPVOID;
typedef unsigned long long HCRYPTPROV;
typedef long LONG;
typedef unsigned char BYTE;
typedef DWORD *PDWORD;
typedef void *LPSECURITY_ATTRIBUTES;

#define ERROR_SUCCESS  0
#define TRUE           1
#define FALSE          0

/* Predefined registry key handles (matching Windows values) */
#define HKEY_CLASSES_ROOT   ((HKEY)(unsigned long long)0x80000000)
#define HKEY_CURRENT_USER   ((HKEY)(unsigned long long)0x80000001)
#define HKEY_LOCAL_MACHINE  ((HKEY)(unsigned long long)0x80000002)

/* TOKEN_INFORMATION_CLASS enum (subset — only the type name matters for stubs) */
typedef enum {
	TokenUser = 1, TokenGroups, TokenPrivileges, TokenOwner,
	TokenPrimaryGroup, TokenDefaultDacl, TokenSource, TokenType,
	TokenImpersonationLevel, TokenStatistics, TokenSessionId = 12,
	TokenElevationType = 18, TokenLinkedToken, TokenElevation,
	TokenIntegrityLevel = 25, TokenUIAccess, TokenLogonSid = 28,
} TOKEN_INFORMATION_CLASS;

/* ── NT Syscall Wrappers ─────────────────────────────────────────────────── */
/* Raw NT syscall — invokes the Axon kernel dispatcher (syscall instruction).
 * Args pointer in r10, syscall number in rax. Returns NT status code.
 */
static inline uint32_t nt_syscall(uint32_t nr, const uint64_t *args)
{
	uint32_t status;
	register uint64_t r10 __asm__("r10") = (uint64_t)args;

	__asm__ volatile(
		"syscall"
		: "=a"(status)
		: "a"((uint64_t)nr), "r"(r10)
		: "rcx", "r11", "memory");
	return status;
}

/* NT syscall numbers — must match nt-registry.c / syscall_table.c */
#define NR_NT_OPEN_KEY        0x0F4
#define NR_NT_QUERY_VALUE_KEY 0x0F7
#define NR_NT_CLOSE           0x00F

/* ── Registry Functions ──────────────────────────────────────────────────── */

LONG RegOpenKeyExA(HKEY hKey, LPCSTR lpSubKey, DWORD ulOptions,
		   DWORD samDesired, PHKEY phkResult)
{
	uint64_t args[5];

	if (!phkResult)
		return 0xC000000D; /* STATUS_INVALID_PARAMETER */

	args[0] = (uint64_t)phkResult;  /* handle out */
	args[1] = (uint64_t)hKey;       /* root key */
	args[2] = (uint64_t)lpSubKey;   /* subkey name */
	args[3] = (uint64_t)ulOptions;
	args[4] = (uint64_t)samDesired;

	nt_syscall(NR_NT_OPEN_KEY, args);

	/* If the kernel returned a valid handle, use it; otherwise fake one */
	if (*phkResult == NULL)
		*phkResult = (HKEY)(unsigned long long)0x10001;

	return ERROR_SUCCESS;
}

LONG RegQueryValueExA(HKEY hKey, LPCSTR lpValueName, LPDWORD lpReserved,
		      LPDWORD lpType, LPBYTE lpData, LPDWORD lpcbData)
{
	uint64_t args[6];
	(void)lpReserved;

	args[0] = (uint64_t)hKey;
	args[1] = (uint64_t)lpValueName;
	args[2] = (uint64_t)lpType;
	args[3] = (uint64_t)lpData;
	args[4] = (uint64_t)lpcbData;
	args[5] = 0; /* reserved */

	nt_syscall(NR_NT_QUERY_VALUE_KEY, args);

	/* If no data was returned, zero out the buffer and set size to 0 */
	if (lpData && lpcbData && *lpcbData == 0) {
		memset(lpData, 0, *lpcbData);
		if (lpType)
			*lpType = 1; /* REG_SZ */
	}

	return ERROR_SUCCESS;
}

LONG RegCloseKey(HKEY hKey)
{
	uint64_t args[1];

	args[0] = (uint64_t)hKey;
	nt_syscall(NR_NT_CLOSE, args);
	return ERROR_SUCCESS;
}

LONG RegCreateKeyExA(HKEY hKey, LPCSTR lpSubKey, DWORD Reserved,
		     LPSTR lpClass, DWORD dwOptions, DWORD samDesired,
		     LPSECURITY_ATTRIBUTES lpSecurityAttributes,
		     PHKEY phkResult, LPDWORD lpdwDisposition)
{
	LONG status;
	(void)Reserved;
	(void)lpClass;
	(void)dwOptions;
	(void)lpSecurityAttributes;

	status = RegOpenKeyExA(hKey, lpSubKey, 0, samDesired, phkResult);
	if (status == ERROR_SUCCESS) {
		if (lpdwDisposition)
			*lpdwDisposition = 2; /* REG_OPENED_EXISTING_KEY */
		return ERROR_SUCCESS;
	}

	/* Key doesn't exist — return a fake handle */
	if (phkResult)
		*phkResult = (HKEY)(unsigned long long)0x10002;
	if (lpdwDisposition)
		*lpdwDisposition = 1; /* REG_CREATED_NEW_KEY */

	return ERROR_SUCCESS;
}

LONG RegSetValueExA(HKEY hKey, LPCSTR lpValueName, DWORD Reserved,
		    DWORD dwType, const BYTE *lpData, DWORD cbData)
{
	/* Stub: registry writes are not yet supported */
	(void)hKey;
	(void)lpValueName;
	(void)Reserved;
	(void)dwType;
	(void)lpData;
	(void)cbData;
	return ERROR_SUCCESS;
}

/* ── Security Functions ──────────────────────────────────────────────────── */

BOOL OpenProcessToken(HANDLE ProcessHandle, DWORD DesiredAccess,
		      PHANDLE TokenHandle)
{
	/* Stub: token operations not supported */
	(void)ProcessHandle;
	(void)DesiredAccess;
	(void)TokenHandle;
	return FALSE;
}

BOOL GetTokenInformation(HANDLE TokenHandle,
			 TOKEN_INFORMATION_CLASS TokenInformationClass,
			 LPVOID TokenInformation, DWORD TokenInformationLength,
			 PDWORD ReturnLength)
{
	/* Stub: token queries not supported */
	(void)TokenHandle;
	(void)TokenInformationClass;
	(void)TokenInformation;
	(void)TokenInformationLength;
	(void)ReturnLength;
	return FALSE;
}

/* ── Crypto Functions ────────────────────────────────────────────────────── */

BOOL CryptAcquireContextA(HCRYPTPROV *phProv, LPCSTR pszContainer,
			  LPCSTR pszProvider, DWORD dwProvType,
			  DWORD dwFlags)
{
	/* Stub: crypto provider not available */
	(void)phProv;
	(void)pszContainer;
	(void)pszProvider;
	(void)dwProvType;
	(void)dwFlags;
	return FALSE;
}

BOOL CryptGenRandom(HCRYPTPROV hProv, DWORD dwLen, BYTE *pbBuffer)
{
	int fd;
	ssize_t total = 0;

	(void)hProv;

	if (!pbBuffer || dwLen == 0)
		return FALSE;

	fd = open("/dev/urandom", O_RDONLY);
	if (fd < 0)
		return FALSE;

	while (total < (ssize_t)dwLen) {
		ssize_t n = read(fd, pbBuffer + total, dwLen - total);

		if (n <= 0) {
			close(fd);
			return FALSE;
		}
		total += n;
	}

	close(fd);
	return TRUE;
}
