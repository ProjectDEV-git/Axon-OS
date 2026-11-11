"""Batching of streamed tokens so D-Bus signals are not flooded."""

import threading
import time
from collections.abc import Callable


class TokenBuffer:
    """Batch token signals to prevent D-Bus signal flooding.

    Accumulates tokens and flushes them either when the buffer is full
    or when the flush interval has elapsed, whichever comes first.
    """

    def __init__(
        self,
        emit_fn: Callable[[str, str], object],
        flush_interval: float = 0.1,
        max_tokens: int = 10,
    ) -> None:
        self._buffer: list[tuple[str, str]] = []  # (transaction_id, token)
        self._last_flush = time.monotonic()
        self._flush_interval = flush_interval
        self._max_tokens = max_tokens
        self._emit_fn = emit_fn
        self._lock = threading.Lock()

    def add(self, token: str, transaction_id: str) -> None:
        """Add a token to the buffer, flushing if thresholds are exceeded."""
        with self._lock:
            self._buffer.append((transaction_id, token))
            now = time.monotonic()
            if (
                len(self._buffer) >= self._max_tokens
                or now - self._last_flush >= self._flush_interval
            ):
                self._flush()

    def flush(self) -> None:
        """Force-flush any buffered tokens."""
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        """Emit all buffered tokens. Caller must hold _lock."""
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()
        for tx_id, tok in batch:
            self._emit_fn(tx_id, tok)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)
