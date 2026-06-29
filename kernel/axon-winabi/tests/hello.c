#include <windows.h>
#include <stdio.h>

int main(void) {
    const char *msg = "Hello from Windows ABI!\n";
    DWORD written;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), msg, (DWORD)strlen(msg), &written, NULL);
    return 0;
}
