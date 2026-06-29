# Axon Windows ABI — Kernel Module Makefile
# Builds axon-winabi.ko for the running kernel (or KDIR= specified).
#
# Usage:
#   make                    # build for running kernel
#   make KDIR=/lib/modules/6.8.0/build  # explicit kernel build dir
#   sudo make install       # install to /lib/modules/.../extra/
#   sudo make load          # insmod the built module

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD  := $(shell pwd)

MODULE_NAME := axon-winabi
obj-m += $(MODULE_NAME).o

# Core module objects — all linked into axon-winabi.ko
$(MODULE_NAME)-y := \
    module-main.o \
    pe-loader.o \
    binfmt_win.o \
    syscall_table.o \
    nt-syscalls.o \
    nt-section.o \
    nt-thread.o \
    nt-sync.o \
    nt-registry.o \
    nt-dll-loader.o

# KUnit test objects (only built when CONFIG_AXON_WINABI_TEST=y)
ifeq ($(CONFIG_AXON_WINABI_TEST),y)
$(MODULE_NAME)-y += axon-winabi-test.o
endif

ccflags-y := -Wno-unused-function -Wno-declaration-after-statement

.PHONY: all clean install load unload

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

install: all
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install
	depmod -a

load:
	insmod $(MODULE_NAME).ko

unload:
	rmmod $(MODULE_NAME)
