"""Long-term memory of user facts for the Axon Brain.

Facts ("prefers metric units", "works in ~/Projects/acme") are stored in
SQLite and the most relevant ones are added to the system prompt. Recall
uses embeddings when an embed function is available and falls back to
word overlap otherwise.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_FACTS = 500
MAX_FACT_CHARS = 300
_WORD_RE = re.compile(r"[a-z0-9]{3,}")

EmbedFn = Callable[[str], list[float] | None]


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryStore:
    """SQLite-backed fact store. Thread-safe; opens a connection per call."""

    def __init__(self, db_path: Path, embed: EmbedFn | None = None) -> None:
        self.db_path = Path(db_path)
        self.embed = embed
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS facts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL UNIQUE, "
                    "vec TEXT, created REAL NOT NULL)"
                )
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), timeout=5)

    def _vector(self, text: str) -> list[float] | None:
        if self.embed is None:
            return None
        try:
            vec = self.embed(text)
        except Exception:
            return None
        return vec or None

    def add(self, text: str) -> int:
        """Store a fact. Returns its id (existing id for a duplicate).

        Raises:
            ValueError: If the fact is empty.
        """
        text = " ".join(str(text).split())[:MAX_FACT_CHARS]
        if not text:
            raise ValueError("empty memory")
        vec = self._vector(text)
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    row = conn.execute("SELECT id FROM facts WHERE text = ?", (text,)).fetchone()
                    if row:
                        return int(row[0])
                    cur = conn.execute(
                        "INSERT INTO facts (text, vec, created) VALUES (?, ?, ?)",
                        (text, json.dumps(vec) if vec else None, time.time()),
                    )
                    conn.execute(
                        "DELETE FROM facts WHERE id NOT IN "
                        "(SELECT id FROM facts ORDER BY created DESC LIMIT ?)",
                        (MAX_FACTS,),
                    )
                    return int(cur.lastrowid or 0)
            finally:
                conn.close()

    def entries(self) -> list[dict[str, Any]]:
        """All facts, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, text, created FROM facts ORDER BY created DESC, id DESC"
            ).fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "text": r[1], "created": r[2]} for r in rows]

    def forget(self, fact_id: int) -> bool:
        """Delete one fact. Returns True if it existed."""
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute("DELETE FROM facts WHERE id = ?", (int(fact_id),))
                    return cur.rowcount > 0
            finally:
                conn.close()

    def clear(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("DELETE FROM facts")
            finally:
                conn.close()

    def recall(self, query: str, k: int = 5, min_score: float = 0.1) -> list[str]:
        """The *k* facts most relevant to *query*."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT text, vec FROM facts").fetchall()
        finally:
            conn.close()
        if not rows:
            return []
        qvec = self._vector(query) if any(r[1] for r in rows) else None
        qwords = _words(query)
        scored: list[tuple[float, str]] = []
        for text, raw in rows:
            score = 0.0
            if qvec and raw:
                score = _cosine(qvec, json.loads(raw))
            else:
                fw = _words(text)
                if qwords and fw:
                    score = len(qwords & fw) / math.sqrt(len(qwords) * len(fw))
            if score >= min_score:
                scored.append((score, text))
        scored.sort(key=lambda s: -s[0])
        return [t for _, t in scored[:k]]


def memory_prompt(facts: list[str]) -> str:
    """System-prompt section listing remembered facts."""
    if not facts:
        return ""
    body = "\n".join(f"- {f}" for f in facts)
    return f"## What you remember about the user\n<untrusted_context>\n{body}\n</untrusted_context>"
