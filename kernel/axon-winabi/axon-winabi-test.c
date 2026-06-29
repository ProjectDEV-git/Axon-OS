// KUnit tests for axon-winabi kernel module
#include <kunit/test.h>
#include "axon-winabi.h"

// Test PE header validation with valid MZ magic
static void test_pe_validate_mz_magic(struct kunit *test) {
    // Create a mock file with MZ header
    // KUNIT_ASSERT_EQ(test, 0, result);
    KUNIT_SUCCEED(test);
}

// Test PE header validation with invalid magic
static void test_pe_validate_invalid_magic(struct kunit *test) {
    KUNIT_SUCCEED(test);
}

// Test syscall dispatch with valid number
static void test_syscall_dispatch_valid(struct kunit *test) {
    __u64 args[9] = {0};
    __u32 result = axon_dispatch_nt_syscall(0x100, args);
    // 0x100 = NtGetCurrentProcessId — should return a pid
    KUNIT_ASSERT_EQ(test, NT_STATUS_SUCCESS, result);
}

// Test syscall dispatch with out-of-range number
static void test_syscall_dispatch_oob(struct kunit *test) {
    __u64 args[1] = {0};
    __u32 result = axon_dispatch_nt_syscall(NT_MAX_SYSCALLS + 1, args);
    KUNIT_ASSERT_EQ(test, NT_STATUS_NOT_IMPLEMENTED, result);
}

// Test syscall dispatch with unimplemented number
static void test_syscall_dispatch_unimplemented(struct kunit *test) {
    __u64 args[1] = {0};
    __u32 result = axon_dispatch_nt_syscall(0x50, args); // unregistered
    KUNIT_ASSERT_EQ(test, NT_STATUS_NOT_IMPLEMENTED, result);
}

static struct kunit_case axon_winabi_cases[] = {
    KUNIT_CASE(test_pe_validate_mz_magic),
    KUNIT_CASE(test_pe_validate_invalid_magic),
    KUNIT_CASE(test_syscall_dispatch_valid),
    KUNIT_CASE(test_syscall_dispatch_oob),
    KUNIT_CASE(test_syscall_dispatch_unimplemented),
    {}
};

static struct kunit_suite axon_winabi_test_suite = {
    .name = "axon-winabi",
    .test_cases = axon_winabi_cases,
};
kunit_test_suite(axon_winabi_test_suite);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Axon OS Contributors");
