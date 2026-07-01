#!/usr/bin/env python3
"""Caching and rate limiting utilities for D-Bus services."""

import functools
import logging
import shlex
import subprocess
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import dbus

logger = logging.getLogger(__name__)

ALLOWED_COMMANDS: set[str] = {
    "ls",
    "cat",
    "grep",
    "find",
    "echo",
    "date",
    "whoami",
    "hostname",
    "uname",
    "df",
    "du",
    "free",
    "uptime",
    "ps",
    "top",
    "htop",
    "pwd",
    "wc",
    "head",
    "tail",
    "sort",
    "uniq",
    "diff",
    "file",
    "stat",
    "readlink",
    "realpath",
    "basename",
    "dirname",
    # NOTE: python/node/bash/sh intentionally excluded — they can execute
    # arbitrary code, defeating the purpose of the allowlist.
    # NOTE: apt/apt-get/dpkg/snap/flatpak/systemctl/g++/gcc/cargo/rustc/git
    # intentionally excluded — they can modify the system, install packages,
    # manage services, or compile arbitrary code.
    "nmcli",
    "bluetoothctl",
    "pactl",
    "paplay",
    "xdg-open",
    "gtk-launch",
    "gio",
    "notify-send",
    "zenity",
}

_SHELL_META_CHARS = frozenset("|;&$`\\(){}[]<>*?~!#")


def safe_exec(command: str, **kwargs: Any) -> subprocess.Popen | None:
    """Execute a command safely with whitelist validation.

    Parses the command with shlex.split() and checks the binary against
    ALLOWED_COMMANDS before executing. Refuses to run commands containing
    shell metacharacters that could enable injection.

    Args:
        command: Command string to execute.
        **kwargs: Additional arguments passed to subprocess.Popen.

    Returns:
        Popen object if command was allowed and started, None otherwise.
    """
    if any(c in command for c in _SHELL_META_CHARS):
        logger.warning(
            "safe_exec: blocked command containing shell metacharacters: %s", command[:100]
        )
        return None

    try:
        parts = shlex.split(command)
    except ValueError:
        logger.warning("safe_exec: failed to parse command: %s", command)
        return None

    if not parts:
        logger.warning("safe_exec: empty command")
        return None

    binary = parts[0]
    if binary not in ALLOWED_COMMANDS:
        logger.warning("safe_exec: blocked unwhitelisted command: %s", binary)
        return None

    defaults: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    defaults.update(kwargs)
    return subprocess.Popen(parts, **defaults)


def error_response(message: str, code: str = "UNKNOWN") -> str:
    """Create a standardized JSON error response for D-Bus methods.

    Args:
        message: Human-readable error description.
        code: Machine-readable error code.

    Returns:
        JSON string with error and code fields.
    """
    import json

    return json.dumps({"error": message, "code": code})


class TTLCache:
    """Thread-safe TTL (Time-To-Live) cache for frequently accessed data."""

    _MAX_ENTRIES = 10_000  # hard cap to prevent unbounded memory growth

    def __init__(self, ttl_seconds: int = 300) -> None:
        """Initialize cache with TTL.

        Args:
            ttl_seconds: Time in seconds before cache entry expires.
        """
        self.ttl_seconds = ttl_seconds
        self.cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def _evict_expired(self) -> None:
        """Remove all expired entries (called under lock)."""
        now = time.time()
        expired = [k for k, (_, ts) in self.cache.items() if now - ts >= self.ttl_seconds]
        for k in expired:
            del self.cache[k]

    def get(self, key: str) -> Any | None:
        """Retrieve value from cache if not expired.

        Args:
            key: Cache key to retrieve.

        Returns:
            Cached value or None if expired/not found.
        """
        with self._lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl_seconds:
                    return value
                del self.cache[key]
            return None

    def set(self, key: str, value: Any) -> None:
        """Store value in cache with current timestamp.

        Evicts expired entries when the cache exceeds MAX_ENTRIES.

        Args:
            key: Cache key to store.
            value: Value to cache.
        """
        with self._lock:
            if len(self.cache) >= self._MAX_ENTRIES:
                self._evict_expired()
            self.cache[key] = (value, time.time())

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self.cache.clear()


class RateLimiter:
    """Thread-safe rate limiter using sliding window algorithm."""

    def __init__(self, rate: int = 100, window_seconds: int = 60) -> None:
        """Initialize rate limiter.

        Args:
            rate: Maximum number of requests allowed.
            window_seconds: Time window in seconds.
        """
        self.rate = rate
        self.window_seconds = window_seconds
        self.requests: dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, identifier: str) -> bool:
        """Check if request is allowed for identifier.

        Args:
            identifier: Unique identifier for rate limit bucket.

        Returns:
            True if request is allowed, False if rate limit exceeded.
        """
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # Clean old requests
            self.requests[identifier] = [
                req_time for req_time in self.requests[identifier] if req_time > cutoff
            ]

            # Check limit
            if len(self.requests[identifier]) < self.rate:
                self.requests[identifier].append(now)
                return True
            return False


def cached(ttl_seconds: int = 300) -> Callable:
    """Decorator to cache D-Bus method results with TTL.

    Args:
        ttl_seconds: Time to live for cached results.

    Returns:
        Decorator function.
    """
    cache = TTLCache(ttl_seconds=ttl_seconds)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            cache_key = f"{func.__name__}:{args}:{kwargs}"
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            result = func(self, *args, **kwargs)
            cache.set(cache_key, result)
            return result

        return wrapper

    return decorator


def rate_limited(rate: int = 100, window_seconds: int = 60) -> Callable:
    """Decorator to rate limit D-Bus method calls.

    Args:
        rate: Maximum requests allowed.
        window_seconds: Time window for rate limit.

    Returns:
        Decorator function.
    """
    limiter = RateLimiter(rate=rate, window_seconds=window_seconds)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            from axon_logger import configure_app_logger

            logger = configure_app_logger(__name__)

            # Use sender info if available from D-Bus context
            identifier = getattr(self, "sender", "default")
            if not limiter.allow(identifier):
                logger.warning("Rate limit exceeded for %s calling %s", identifier, func.__name__)
                raise dbus.exceptions.DBusException(f"Rate limit exceeded for {func.__name__}")
            return func(self, *args, **kwargs)

        return wrapper

    return decorator
