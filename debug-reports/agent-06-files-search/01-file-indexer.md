# Debug Report 01: File Indexer Performance & Error Handling

**Agent**: Debug Agent 6 — File System & Search  
**Date**: 2026-07-02  
**Files Analyzed**:
- `services/axon-context/file_indexer.py` (196 lines)
- `apps/axon-files/file_indexer.py` (400+ lines)
- `services/axon-search/indexer.py` (172 lines)

---

## 1. Two Separate File Indexers — Architecture Confusion

**Severity: HIGH (Design)**

There are **two completely independent file indexer implementations** doing overlapping work:

| Aspect | `services/axon-context/file_indexer.py` | `apps/axon-files/file_indexer.py` |
|---|---|---|
| DB file | `semantic_search.db` (sqlite-vec) | `files_index.db` (JSON embeddings) |
| Vector approach | `sqlite_vec` float[768] binary blobs | JSON-serialized embedding lists |
| Scan interval | 30 seconds (`time.sleep(30)`) | On-demand via D-Bus |
| Watch dirs | Documents, Notes, Projects (hardcoded) | Dynamic from caller |
| Chunking | First 1500 chars | First 2000 chars |
| Embedding prefix | None | `"File: {name}\nPath: {path}\nType: {type}\nSummary: {content}"` |

**Impact**: Two databases are built from overlapping file sets with different schemas, wasting CPU (duplicate Ollama embedding calls) and disk space. The context indexer also runs in a busy 30-second loop.

---

## 2. `os.walk` Efficiency

### `services/axon-context/file_indexer.py` (Line 175)
```python
for root, _, files in os.walk(watch_dir):
    if "/." in root or root.split("/")[-1].startswith("."):
        continue
```

**Issues**:
- **BUG: Hidden directory pruning is incomplete.** The `/.` substring check catches intermediate hidden dirs, but the `split("/")[-1]` check only catches the leaf directory. However, the real issue is that `os.walk` **still descends into** hidden directories — it just skips them after descent. This wastes significant I/O time on large hidden directories (e.g., `.cache/thumbnails`).
- **FIX**: Modify `dirnames` in-place during walk to prune:
  ```python
  for root, dirs, files in os.walk(watch_dir):
      dirs[:] = [d for d in dirs if not d.startswith(".")]
  ```

### `apps/axon-files/file_indexer.py` (Line 125)
```python
for dirpath, dirnames, filenames in os.walk(root_path):
    dirnames[:] = [d for d in dirnames if d not in ignored_dirs and not d.startswith(".")]
```

**This is correct** — in-place pruning via `dirnames[:]` prevents descent into excluded directories.

### `services/axon-search/indexer.py` (Line 147)
```python
for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
    dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
```

**Also correct** with in-place pruning and `followlinks=False` (avoids symlink loops).

---

## 3. Error Handling for `/proc/*/fd` and Permission Denied

### `services/axon-context/file_indexer.py`

- **Line 94-98**: `OSError` is caught for `stat()`, which covers `PermissionError`. 
- **Line 114-117**: `Exception` is caught for `read_text()`. Broad but functional.
- **Line 150-151**: Outer `except Exception` with `logger.exception()` provides full traceback.
- **NO `os.walk` error handler**: If a directory becomes unreadable during traversal, `os.walk` will raise `OSError`. The outer `scan_and_index` catches this, but it aborts the **entire scan** of all remaining directories. Should wrap the inner walk in a try/except.

**MISSING**: No exclusion of `/proc`, `/sys`, `/dev`, etc. from walk roots. If a user adds `/` as a watch dir, the indexer will attempt to walk `/proc/*/fd` and get permission errors.

### `apps/axon-files/file_indexer.py`

- **Line 196-199**: Generic `Exception` caught for `stat()`.
- **Line 246-247**: Generic `Exception` caught for file reading.
- **No protection against system directories** in `get_all_files()`.

**Rating**: Adequate for user home directories. Inadequate if non-home paths are ever passed as roots.

---

## 4. RESCAN_INTERVAL Appropriateness

### `services/axon-context/file_indexer.py`
- **`time.sleep(30)`** (Line 190): Scans every 30 seconds. This is **aggressively frequent** for a file indexer. On a system with thousands of files, this generates constant CPU and I/O load, plus repeated D-Bus calls to Brain for embeddings.
- **Memory concern**: The function opens DB, queries all existing rows, walks filesystem, and processes each file sequentially. Each `index_file()` call creates a D-Bus connection per file (no connection reuse). No memory leak per se, but **each file creates a fresh D-Bus proxy object** (line 77-80), which adds latency.

### `services/axon-search/search_service.py`
- **`RESCAN_INTERVAL = 15 * 60`** (900 seconds from constants.py): This is the configured 15-minute interval.
- **Actual behavior**: The `_index_loop` sleeps 20 seconds initially, then runs `_scan_once()` and waits on `_rescan_event.wait(RESCAN_INTERVAL)`. With watchdog, rescans are triggered on file changes.
- **This is reasonable** — 15 minutes is a sensible default for background indexing.

**Rating**: The context indexer's 30-second interval is excessive. The search service's 15-minute interval is appropriate.

---

## 5. Memory Leak Assessment

### `services/axon-context/file_indexer.py`
- **No obvious memory leak.** The `scan_and_index()` function is stateless between iterations — no accumulated lists or growing caches. Each file is processed individually and discarded.
- **Concern**: `remove_deleted_files()` loads ALL file IDs and paths into memory (line 156-157: `SELECT id, path FROM files`). For very large indexes, this could spike memory briefly.

### `apps/axon-files/file_indexer.py`
- **Potential issue**: `scan_directories()` loads `db_files` as a dict of ALL indexed files (line 182), plus `all_files` as a list of ALL discovered files, plus `visited_paths` as a set. All three grow linearly with file count. For a system with 100K+ files, this could consume significant memory (estimated ~50-100MB).
- **No explicit cleanup** — the function is typically called once, so this is a one-shot allocation.

**Rating**: No persistent memory leak. One-shot memory usage could be high for large file trees.

---

## 6. `cosine_similarity` Edge Cases

### `apps/axon-files/file_indexer.py` (Line 66-74)
```python
def cosine_similarity(v1, v2):
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2, strict=False))
    magnitude1 = math.sqrt(sum(a * a for a in v1))
    magnitude2 = math.sqrt(sum(b * b for b in v2))
    if magnitude1 == 0.0 or magnitude2 == 0.0:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)
```

**Edge case analysis**:
| Case | Behavior | Correct? |
|---|---|---|
| `v1=[]`, `v2=[]` | Returns 0.0 (falsy check) | Yes |
| `v1=None`, `v2=[1,2]` | Returns 0.0 (falsy check) | Yes |
| `v1=[0,0,0]`, `v2=[1,2,3]` | magnitude1=0.0, returns 0.0 | Yes |
| `v1=[1,2]`, `v2=[1,2,3]` | Returns 0.0 (length mismatch) | Yes |
| `v1=[5.0]`, `v2=[5.0]` | Returns 1.0 | Yes |

**Minor note**: `strict=False` in zip is the default; this is fine but slightly redundant. The function correctly handles all edge cases.

**Rating**: Correct and robust.

---

## 7. Embedding Fetch (`fetch_embedding_dbus`) Robustness

### `apps/axon-files/file_indexer.py` (Line 77-92)
```python
def fetch_embedding_dbus(prompt: str) -> list:
    try:
        bus = dbus.SessionBus()
        brain_obj = bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
        embeddings_json = brain_obj.GetEmbeddings(prompt, "", dbus_interface="org.axonos.Brain")
        ...
    except Exception as e:
        logger.exception("Error fetching embedding via D-Bus: %s", e)
    return []
```

**Issues**:
1. **No D-Bus timeout**: The call has no explicit timeout. If Brain service hangs, this blocks indefinitely. The scan loop could stall for minutes or forever.
2. **No connection reuse**: Creates a new `dbus.SessionBus()` on every call. This is expensive — each call re-establishes the bus connection.
3. **No retry logic**: If Brain is temporarily unavailable (e.g., loading a model), the embedding simply returns empty and the file is skipped forever (until next rescan).
4. **Broad exception handling**: `except Exception` catches everything including `KeyboardInterrupt` (in Python < 3.11).

### `services/axon-context/file_indexer.py` (Line 75-86)
- Similar issues: no timeout, no retry, new bus connection per call.
- Better: returns `None` instead of empty list, making failure explicit.

### `services/axon-search/search_service.py` (Line 133-152)
- **Better**: Uses `timeout=30` parameter on the D-Bus call.
- **Better**: Attempts `brain.PullModel()` once if embeddings fail.
- **Better**: Catches specific `dbus.exceptions.DBusException` and `ValueError`.

**Rating**: The context and apps indexers have fragile D-Bus calls. The search service has a more robust implementation.

---

## 8. Additional Findings

### Double `stat()` Call — `services/axon-context/file_indexer.py`
```python
# Line 94-98: stat() for size check
if p.stat().st_size > 512 * 1024:
    return
# Line 100: stat() again for mtime
mtime = p.stat().st_mtime
```
Two `stat()` syscalls per file. Minor inefficiency — should cache the stat result.

### Missing `sqlite_vec` Import Graceful Degradation
- `services/axon-context/file_indexer.py` line 11: `import sqlite_vec` — **no try/except**. If sqlite-vec is not installed, the entire indexer crashes on import.
- `services/axon-search/search_service.py` line 37-42: Properly handles missing sqlite-vec with `HAVE_SQLITE_VEC` flag.
- `apps/axon-files/file_indexer.py`: Does not use sqlite-vec (stores embeddings as JSON text), so no issue.

### Inconsistent Watch Directories
- Context indexer: `Documents`, `Notes`, `Projects`
- Search indexer: `Documents`, `Desktop`, `Projects`, `Notes`, `src`, `scripts`
- Apps indexer: Receives roots as a parameter from the caller

These should be unified to a single configuration source.

---

## Summary of File Indexer Issues

| # | Severity | Issue |
|---|---|---|
| 1 | **HIGH** | Two overlapping indexer systems with duplicate databases |
| 2 | **MEDIUM** | Context indexer's `os.walk` does not prune hidden dirs in-place |
| 3 | **MEDIUM** | Context indexer runs every 30 seconds (excessive) |
| 4 | **MEDIUM** | No D-Bus timeout on embedding calls in two indexers |
| 5 | **LOW** | Double `stat()` syscall per file in context indexer |
| 6 | **LOW** | Inconsistent watch directories across indexers |
| 7 | **LOW** | `sqlite_vec` import crash risk in context indexer |
| 8 | **LOW** | `remove_deleted_files()` loads all rows into memory |
