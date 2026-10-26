CC = gcc
CFLAGS = -Wall -Wextra -O2 -D_GNU_SOURCE
LDFLAGS =

SRCS = win-tap-bridge.c tap.c socket.c bridge.c
OBJS = $(SRCS:.c=.o)
TARGET = win-tap-bridge

all: $(TARGET)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

clean:
	rm -f $(OBJS) $(TARGET) test_bridge

# Adversarial parser tests (no TAP/root needed), under ASan/UBSan.
test: test_bridge.c bridge.c bridge.h
	$(CC) -Wall -Wextra -Werror -O1 -g -D_GNU_SOURCE -fsanitize=address,undefined \
		-fno-omit-frame-pointer -o test_bridge test_bridge.c bridge.c
	./test_bridge

.PHONY: all clean test
