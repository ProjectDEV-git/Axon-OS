#!/usr/bin/env python3
"""SQLite-backed clipboard history store for Axon Context service."""

import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from constants import AXON_DIR


class ClipboardStore:
    """Thread-safe clipboard history with SQLite persistence."""

    def __init__(self, db_path=None, max_entries=50, max_entry_len=500):
        if db_path is None:
            db_path = str(AXON_DIR / "clipboard.db")

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.max_entries = max_entries
        self.max_entry_len = max_entry_len
        self._lock = threading.Lock()
        self._local = threading.local()
        self._all_connections: list[sqlite3.Connection] = []
        self._init_db()

    def _get_connection(self):
        """Return a per-thread SQLite connection, reusing if still open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        self._local.conn = conn
        with self._lock:
            self._all_connections.append(conn)
        return conn

    def close(self):
        """Close the per-thread connection explicitly."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def close_all(self):
        """Close every connection ever created, including those from dead threads."""
        with self._lock:
            conns = list(self._all_connections)
            self._all_connections.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self.close()

    def __del__(self) -> None:
        self.close_all()

    def _init_db(self):
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clipboard_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                content_type TEXT DEFAULT 'text',
                timestamp REAL NOT NULL,
                pinned INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_clipboard_timestamp
            ON clipboard_entries(timestamp DESC)
        """)
        conn.commit()
        # Connection stays open via threading.local() for reuse.

    def add(self, content, content_type="text"):
        """Add a clipboard entry. Returns True if added (not duplicate)."""
        if not content or not content.strip():
            return False

        content = content[: self.max_entry_len].strip()

        with self._lock:
            conn = self._get_connection()
            # Check for duplicate (most recent entry)
            row = conn.execute(
                "SELECT content FROM clipboard_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row["content"] == content:
                return False

            conn.execute(
                "INSERT INTO clipboard_entries (content, content_type, timestamp) VALUES (?, ?, ?)",
                (content, content_type, time.time()),
            )

            # Prune old entries (keep pinned + max_entries most recent)
            conn.execute(
                """
                DELETE FROM clipboard_entries WHERE id NOT IN (
                    SELECT id FROM clipboard_entries WHERE pinned = 1
                    UNION
                    SELECT id FROM clipboard_entries ORDER BY id DESC LIMIT ?
                )
            """,
                (self.max_entries,),
            )

            conn.commit()
            return True

    def get_recent(self, limit=10):
        """Get most recent clipboard entries."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT id, content, content_type, timestamp, pinned "
                "FROM clipboard_entries ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def search(self, query, limit=20):
        """Search clipboard history by content."""
        # Escape LIKE wildcards to prevent unintended matches
        safe_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT id, content, content_type, timestamp, pinned "
                "FROM clipboard_entries WHERE content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                (f"%{safe_query}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def pin(self, entry_id):
        """Pin a clipboard entry to prevent pruning."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("UPDATE clipboard_entries SET pinned = 1 WHERE id = ?", (entry_id,))
            conn.commit()

    def unpin(self, entry_id):
        """Unpin a clipboard entry."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("UPDATE clipboard_entries SET pinned = 0 WHERE id = ?", (entry_id,))
            conn.commit()

    def delete(self, entry_id):
        """Delete a specific clipboard entry."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM clipboard_entries WHERE id = ?", (entry_id,))
            conn.commit()

    def clear(self):
        """Clear all non-pinned clipboard entries."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM clipboard_entries WHERE pinned = 0")
            conn.commit()

    def get_count(self):
        """Get total number of clipboard entries."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("SELECT COUNT(*) as cnt FROM clipboard_entries").fetchone()
            return row["cnt"]

    def to_deque(self, maxlen=None):
        """Export recent entries as a deque (for backward compatibility)."""
        from collections import deque

        limit = maxlen or self.max_entries
        entries = self.get_recent(limit)
        return deque([e["content"] for e in entries], maxlen=limit)
