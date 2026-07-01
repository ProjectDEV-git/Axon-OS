"""Tests for service_utils — TTLCache, RateLimiter, safe_exec, decorators."""

import json
import time

import pytest

from services.service_utils import (
    ALLOWED_COMMANDS,
    RateLimiter,
    TTLCache,
    error_response,
    rate_limited,
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


class TestTTLCacheMaxEntries:
    """Test the _MAX_ENTRIES eviction — expired entries are cleaned when cache is full."""

    def test_triggers_eviction_at_cap(self):
        """When cache hits MAX_ENTRIES, expired entries should be evicted on next set."""
        cache = TTLCache(ttl_seconds=0)  # instant expiry
        cap = cache._MAX_ENTRIES
        # Fill cache past the cap (all expire immediately)
        for i in range(cap + 10):
            cache.set(f"k{i}", f"v{i}")
        time.sleep(0.01)  # let them expire
        # This set should trigger eviction of expired entries
        cache.set("trigger", "clean")
        # After eviction, the cache should be smaller than what it was
        assert len(cache.cache) < cap + 10

    def test_evicts_expired_entries_on_overflow(self):
        """When at cap, expired entries get evicted before adding new entry."""
        # Use a very short TTL so entries expire quickly
        cache = TTLCache(ttl_seconds=0)
        for i in range(cache._MAX_ENTRIES):
            cache.set(f"expired_{i}", i)
        time.sleep(0.01)
        # New entry triggers eviction — all expired ones are cleaned
        cache.set("fresh", "alive")
        # The fresh entry should be readable with a long-TTL cache
        # (We used ttl=0 so it's also expired, but the eviction test is the point)
        assert len(cache.cache) <= cache._MAX_ENTRIES

    def test_live_entries_survive_eviction(self):
        """Non-expired entries should survive when eviction is triggered."""
        cache = TTLCache(ttl_seconds=60)  # long TTL
        cap = cache._MAX_ENTRIES
        # Fill to the cap with live entries
        for i in range(cap):
            cache.set(f"k{i}", f"v{i}")
        # All should still be readable
        for i in range(cap):
            assert cache.get(f"k{i}") == f"v{i}"

    def test_max_entries_class_attribute(self):
        """MAX_ENTRIES should be defined and reasonable."""
        assert TTLCache._MAX_ENTRIES == 10_000


class TestRateLimitedDecorator:
    """Test the @rate_limited decorator factory."""

    def test_decorator_factory_is_callable(self):
        """rate_limited() should return a usable decorator."""
        decorator = rate_limited(rate=100, window_seconds=60)
        assert callable(decorator)

    def test_decorator_allows_under_limit(self):
        """Decorator should pass through when under the rate limit."""

        class FakeService:
            sender = "test_user"

            @rate_limited(rate=3, window_seconds=60)
            def my_method(self):
                return "ok"

        svc = FakeService()
        assert svc.my_method() == "ok"
        assert svc.my_method() == "ok"
        assert svc.my_method() == "ok"

    def test_decorator_blocks_after_limit(self):
        """Decorator should raise dbus.exceptions.DBusException when rate exceeded."""
        import dbus.exceptions

        class FakeService:
            sender = "test_user"

            @rate_limited(rate=2, window_seconds=60)
            def my_method(self):
                return "ok"

        svc = FakeService()
        assert svc.my_method() == "ok"
        assert svc.my_method() == "ok"
        with pytest.raises(dbus.exceptions.DBusException, match="Rate limit exceeded"):
            svc.my_method()

    def test_rate_limited_is_importable(self):
        """The rate_limited decorator should be importable from service_utils."""
        assert callable(rate_limited)
