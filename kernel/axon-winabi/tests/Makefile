# Cross-compile Windows test programs for Axon Windows ABI testing
# Requires: mingw-w64 (apt install mingw-w64)

CC_WIN64 = x86_64-w64-mingw32-gcc
CC_WIN32 = i686-w64-mingw32-gcc
CFLAGS = -Wall -Wextra -Os -nostdlib -nostartfiles -fno-builtin

all: hello.exe hello_nt.exe

hello.exe: hello.c
	$(CC_WIN64) -o $@ $< -lkernel32

hello_nt.exe: hello_nt.c
	$(CC_WIN64) $(CFLAGS) -Wl,--entry=_start -o $@ $<

clean:
	rm -f *.exe
