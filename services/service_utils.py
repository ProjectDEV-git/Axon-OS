#!/usr/bin/env python3
"""Caching and rate limiting utilities for D-Bus services."""

import functools
import time
from collections import defaultdict
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    """Simple TTL (Time-To-Live) cache for frequently accessed data."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        """Initialize cache with TTL.

        Args:
            ttl_seconds: Time in seconds before cache entry expires.
        """
        self.ttl_seconds = ttl_seconds
        self.cache: Dict[str, Tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        """Retrieve value from cache if not expired.

        Args:
            key: Cache key to retrieve.

        Returns:
            Cached value or None if expired/not found.
        """
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl_seconds:
                return value
            del self.cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        """Store value in cache with current timestamp.

        Args:
            key: Cache key to store.
            value: Value to cache.
        """
        self.cache[key] = (value, time.time())

    def clear(self) -> None:
        """Clear all cached entries."""
        self.cache.clear()


class RateLimiter:
    """Rate limiter using token bucket algorithm."""

    def __init__(self, rate: int = 100, window_seconds: int = 60) -> None:
        """Initialize rate limiter.

        Args:
            rate: Maximum number of requests allowed.
            window_seconds: Time window in seconds.
        """
        self.rate = rate
        self.window_seconds = window_seconds
        self.requests: Dict[str, list] = defaultdict(list)

    def allow(self, identifier: str) -> bool:
        """Check if request is allowed for identifier.

        Args:
            identifier: Unique identifier for rate limit bucket.

        Returns:
            True if request is allowed, False if rate limit exceeded.
        """
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old requests
        self.requests[identifier] = [
            req_time for req_time in self.requests[identifier]
            if req_time > cutoff
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
            identifier = getattr(self, 'sender', 'default')
            if not limiter.allow(identifier):
                logger.warning(
                    "Rate limit exceeded for %s calling %s",
                    identifier,
                    func.__name__
                )
                raise dbus.exceptions.DBusException(
                    f"Rate limit exceeded for {func.__name__}"
                )
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
