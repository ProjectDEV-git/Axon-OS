"""Pure helpers for the Axon Semantic Search indexer.

Kept free of D-Bus/GTK imports so the logic is unit-testable.
"""

from __future__ import annotations

import os
from pathlib import Path

# File types worth embedding: prose, notes, code, configs.
INDEX_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".org",
    ".py",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".rb",
    ".lua",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".css",
    ".html",
    ".sql",
    ".tex",
    ".csv",
}

# Directory names never descended into.
EXCLUDE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".cache",
    ".cargo",
    ".rustup",
    ".npm",
    ".local",
    ".config",
    ".mozilla",
    ".thunderbird",
    "snap",
    ".steam",
    ".var",
    ".axon",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "target",
}

# Files larger than this are skipped (embedding huge blobs is wasteful).
MAX_FILE_BYTES = 512 * 1024

# Chunking parameters (characters).
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

# Home subdirectories scanned by default (relative to $HOME).
DEFAULT_ROOTS = ("Documents", "Desktop", "Projects", "Notes", "src", "scripts")


def should_index(path: str | Path) -> bool:
    """True when *path* is a small enough text-like file we want to embed."""
    p = Path(path)
    if p.suffix.lower() not in INDEX_EXTENSIONS:
        return False
    if any(part in EXCLUDE_DIRS or part.startswith(".") for part in p.parts[:-1]):
        return False
    if p.name.startswith("."):
        return False
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return True


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split *text* into overlapping chunks, preferring paragraph boundaries."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        window = text[start:end]
        # Prefer to break on a paragraph, then a newline, then a space.
        if end < len(text):
            for sep in ("\n\n", "\n", " "):
                cut = window.rfind(sep)
                if cut > size // 2:
                    end = start + cut
                    window = text[start:end]
                    break
        chunk = window.strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def iter_candidate_files(home: str | Path, roots: tuple[str, ...] = DEFAULT_ROOTS):
    """Yield indexable file paths beneath the given home-relative roots."""
    home = Path(home)
    seen: set[str] = set()
    for rel in roots:
        base = home / rel
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if full in seen:
                    continue
                if should_index(full):
                    seen.add(full)
                    yield full


def read_text(path: str | Path) -> str | None:
    """Read a file as UTF-8 text; None when unreadable or binary-ish."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except Exception:
            return None
