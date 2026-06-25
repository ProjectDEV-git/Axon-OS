"""Tests for service_utils — TTLCache, RateLimiter, safe_exec, decorators."""

import json
import time

from services.service_utils import (
    ALLOWED_COMMANDS,
    RateLimiter,
    TTLCache,
    error_response,
    safe_exec,
)


class TestSafeExec:
    def test_allows_whitelisted_command(self):
        proc = safe_exec("echo hello")
        assert proc is not None
        proc.kill()

    def test_blocks_shell_metacharacters(self):
        assert safe_exec("echo hello | grep h") is None
        assert safe_exec("echo hello; rm -rf /") is None
        assert safe_exec("echo $(whoami)") is None
        assert safe_exec("echo `id`") is None

    def test_blocks_unwhitelisted_binary(self):
        assert safe_exec("rm -rf /") is None
        assert safe_exec("curl http://evil.com") is None

    def test_blocks_empty_command(self):
        assert safe_exec("") is None

    def test_blocks_invalid_shlex(self):
        assert safe_exec("echo 'unterminated") is None

    def test_allowlist_contains_common_commands(self):
        for cmd in ["ls", "cat", "grep", "find", "echo", "date", "whoami"]:
            assert cmd in ALLOWED_COMMANDS


class TestErrorResponse:
    def test_returns_json_with_error_and_code(self):
        result = error_response("not found", "NOT_FOUND")
        data = json.loads(result)
        assert data["error"] == "not found"
        assert data["code"] == "NOT_FOUND"

    def test_default_code(self):
        result = error_response("something broke")
        data = json.loads(result)
        assert data["code"] == "UNKNOWN"


class TestTTLCache:
    def test_set_and_get(self):
        cache = TTLCache(ttl_seconds=10)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_key(self):
        cache = TTLCache()
        assert cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self):
        cache = TTLCache(ttl_seconds=0)
        cache.set("key", "value")
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_clear(self):
        cache = TTLCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_overwrite_value(self):
        cache = TTLCache()
        cache.set("k", "old")
        cache.set("k", "new")
        assert cache.get("k") == "new"


class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = RateLimiter(rate=3, window_seconds=60)
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter(rate=2, window_seconds=60)
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is False

    def test_separate_buckets(self):
        limiter = RateLimiter(rate=1, window_seconds=60)
        assert limiter.allow("user1") is True
        assert limiter.allow("user2") is True
        assert limiter.allow("user1") is False

    def test_window_expiry_allows_new_requests(self):
        limiter = RateLimiter(rate=1, window_seconds=0)
        assert limiter.allow("user1") is True
        time.sleep(0.01)
        assert limiter.allow("user1") is True
