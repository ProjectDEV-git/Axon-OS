CC = x86_64-w64-mingw32-gcc
CFLAGS = -Wall -O2 -I.
LDFLAGS = -shared -lws2_32 -lntdll

SRCS = dllmain.c ioctl.c pipe.c
OBJS = $(SRCS:.c=.o)
TARGET = sys_netmp.dll

all: $(TARGET)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^

clean:
	rm -f $(OBJS) $(TARGET)
