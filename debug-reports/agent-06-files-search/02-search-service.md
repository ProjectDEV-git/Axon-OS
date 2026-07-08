# Debug Report 02: Search Service Correctness

**Agent**: Debug Agent 6 — File System & Search  
**Date**: 2026-07-02  
**Files Analyzed**:
- `services/axon-search/search_service.py` (451 lines)
- `services/axon-search/global_search_service.py` (219 lines)
- `services/axon-search/indexer.py` (172 lines)

---

## 1. SQL Injection / LIKE Injection Analysis

### `services/axon-search/search_service.py`

#### Vector Query (Line 369-375)
```python
rows = db.execute(
    "SELECT c.path, c.text, v.distance"
    " FROM vec_chunks v JOIN chunks c ON c.id = v.rowid"
    " WHERE v.embedding MATCH ? AND v.k = ?"
    " ORDER BY v.distance",
    (json.dumps(vec), limit * 3),
).fetchall()
```
**Safe**: Uses parameterized queries. The `json.dumps(vec)` produces a JSON array string consumed by sqlite-vec's MATCH syntax, not raw SQL.

#### Keyword Query (Line 395-397)
```python
def _keyword_query(self, db, query, limit):
    terms = " OR ".join(f'"{t}"' for t in query.replace('"', " ").split() if t)
```

**ISSUE: Potential FTS5 operator leakage.** The query text is split on whitespace, double-quotes are replaced with spaces, and each token is wrapped in double-quotes. This handles the basic case, but:

- **Edge case**: A token containing `OR` (e.g., searching for `"cat OR dog"` as a single concept) would be interpreted as FTS5 boolean OR, not as a literal string. This is a **semantic correctness issue**, not a security issue.
- **Edge case**: Tokens containing `*` (FTS5 prefix operator) are not escaped. A query like `"test*"` would match `testing`, `tester`, etc. This may be unintended behavior.
- **Mitigation**: The double-quoting of each token provides reasonable protection. No direct SQL injection is possible because the MATCH clause receives its input as a bound parameter.

**Security Assessment**: **No SQL injection vulnerability.** All queries use parameterized values.

#### `apps/axon-files/file_indexer.py` LIKE Query (Line 380-391)
```python
escaped_query = query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
query_like = f"%{escaped_query}%"
cursor.execute(
    "SELECT ... WHERE file_name LIKE ? ESCAPE '\\' OR file_path LIKE ? ESCAPE '\\' ...",
    (query_like, query_like, query_like, limit),
)
```
**Safe**: Proper LIKE injection prevention with escape characters.

#### `services/axon-brain/conversation_store.py` search_messages (Line 198-208)
**Safe**: Same correct escaping pattern.

---

## 2. CRITICAL BUG: GlobalSearch File Search is Broken

### Confirmed via code inspection

**`global_search_service.py` line 100:**
```python
raw = svc.Search(query)
```

**`search_service.py` D-Bus methods (confirmed):**
```
Line 346: @dbus.service.method("org.axonos.Search", in_signature="si", out_signature="s")
Line 347:     def Query(self, query, limit):

Line 425: @dbus.service.method("org.axonos.Search", in_signature="", out_signature="b")
Line 426:     def Reindex(self):

Line 431: @dbus.service.method("org.axonos.Search", in_signature="", out_signature="s")
Line 432:     def GetStats(self):
```

**There is no `Search` method on `org.axonos.Search`.** The method is called `Query(self, query, limit)` with signature `(si)` → `(s)`.

**Consequence**: When GlobalSearch calls `svc.Search(query)`, the D-Bus proxy raises `org.freedesktop.DBus.Error.UnknownMethod`. This exception is caught by the bare `except Exception: pass` at line 113, causing **file search results to always be empty** in the GlobalSearch.

**Fix required**: Change `svc.Search(query)` to `svc.Query(query, 8)` (or appropriate limit).

---

## 3. Semantic Search Relevance & Threshold Tuning

### Vector Score Computation (`search_service.py`, Line 387)
```python
"score": round(1.0 / (1.0 + float(dist)), 4),
```

**Analysis**: The `distance` from sqlite-vec is the L2 distance (Euclidean). The transformation `1/(1+d)` maps:
- Distance 0.0 → score 1.0 (perfect match)
- Distance 1.0 → score 0.5
- Distance 5.0 → score 0.167
- Distance 10.0 → score 0.091

**Concern**: There is **no score threshold** — all results from the vec query are returned up to the limit. Low-relevance results with distance > 5.0 (score < 0.17) are still included. A minimum score threshold (e.g., 0.3) would improve perceived relevance.

### `apps/axon-files/file_indexer.py` Semantic Search (Line 352-375)
```python
# Loads ALL rows with embeddings, computes cosine similarity in Python
for row in rows:
    emb_list = json.loads(row["embedding"])
    sim = cosine_similarity(query_emb, emb_list)
```

**CRITICAL PERFORMANCE ISSUE**: This performs a **full table scan** loading every indexed file's embedding into Python memory, then computes cosine similarity one-by-one. This is O(n) in Python where n = number of indexed files. For 10,000 files with 768-dim embeddings, this loads ~30MB of JSON and runs ~10K cosine similarity computations on every search query.

The search service (`search_service.py`) correctly uses sqlite-vec for O(log n) approximate nearest neighbor search. The apps indexer does not.

### Keyword Score (`search_service.py`, Line 417)
```python
"score": round(-float(rank), 4),
```
FTS5's `rank` is a negative number (more negative = better match). Negating it makes higher = better. The score range varies by query and document set. No normalization is applied.

---

## 4. Unicode Handling in Full-Text Search

### FTS5 Unicode Support (`search_service.py`)
SQLite FTS5 uses a **simple tokenizer** by default that splits on whitespace and punctuation. It does **not** handle:
- **CJK characters** (Chinese, Japanese, Korean) — these lack whitespace between words, so FTS5 would treat an entire CJK string as one token.
- **Stemming** — no word stemming for English or other languages.
- **Case folding** — FTS5 is case-sensitive by default for non-ASCII characters.

**Evidence**: The `_keyword_query` method splits on whitespace and quotes each token:
```python
terms = " OR ".join(f'"{t}"' for t in query.replace('"', " ").split() if t)
```
A query like `"你好世界"` (Chinese for "hello world") would become `"你好世界"` — a single quoted token. FTS5 would need an exact match, which is fragile for CJK text.

**Recommendation**: Use `fts5 tokenize=unicode61` for better internationalization:
```sql
CREATE VIRTUAL TABLE fts_chunks USING fts5(text, content='chunks', content_rowid='id', tokenize='unicode61');
```

### `indexer.py` read_text (Line 158-172)
**Good**: Falls back to latin-1 if UTF-8 fails. Binary detection via null byte check is pragmatic.

---

## 5. Search Result De-duplication

### `search_service.py` — Vector & Keyword Queries
Both use `seen = set()` to de-duplicate by file path. When a file has multiple chunks, only the best-scoring chunk per file is returned. **Correct behavior**.

### `global_search_service.py` (Line 92-173)
**No cross-source de-duplication.** The `Search` method fans out to three sources (files, apps, settings) and merges results. If a `.desktop` file matches both the file and app searches, it appears twice.

Results from different sources use different scoring scales:
- File search: vector distance or FTS5 rank (variable scale)
- App search: hardcoded 0.8
- Settings search: hardcoded 0.5

Sorting by raw score across these scales is not meaningful.

---

## 6. D-Bus Proxy Reliability in GlobalSearch

### Permanent Proxy Caching Without Reconnection
```python
def _get_search(self):
    if self._search is None:
        obj = self.session_bus.get_object("org.axonos.Search", "/org/axonos/Search")
        self._search = dbus.Interface(obj, "org.axonos.Search")
    return self._search
```

Once cached, the proxy is never invalidated. If the Search/Brain/Context service restarts, the cached proxy becomes stale. All subsequent calls will fail with `org.freedesktop.DBus.Error.NoReply` or similar. **No proxy invalidation or reconnection logic exists.**

**Fix**: Wrap D-Bus calls with try/except that resets the cached proxy on failure:
```python
def _get_search(self):
    return self._get_proxy("org.axonos.Search", "/org/axonos/Search", "org.axonos.Search")
```
(Using the existing `_get_proxy` helper which always creates fresh proxies.)

---

## 7. Thread Safety

### `search_service.py`
- `open_db()` creates a new connection per call, which is safe for concurrent access.
- WAL mode handles concurrent readers correctly.
- **No retry logic for `SQLITE_BUSY`** — if `_scan_once()` (write) and `Query()` (read) overlap heavily, WAL can queue writes. Under extreme load, this could cause timeouts.

### `global_search_service.py`
- `self._lock` protects `_recent_queries`, which is correct.
- `_search_files`, `_search_apps`, `_search_settings` run in threads and use `results_lock` for the shared results list. **Correct**.
- The proxy attributes (`_brain`, `_search`, `_context`) are set without locking. If two threads call `_get_search()` simultaneously on first access, both may create proxies. This is harmless (idempotent) but technically a race condition.

---

## 8. Watchdog & Polling Fallback

### `_watch_loop` Polling (Line 232-263)
```python
def _watch_loop(self):
    known: dict[str, float | None] = {}
    while True:
        for path in indexer.iter_candidate_files(Path.home()):
            mtime = Path(path).stat().st_mtime
            ...
        time.sleep(10)
```

Every 10 seconds, this walks the entire home directory tree and stats every candidate file. For a home directory with 50,000 candidate files, this means ~50,000 `stat()` calls every 10 seconds — approximately 5,000 syscalls/second just for polling. This is wasteful but not catastrophic on modern Linux (stat on warm page cache is ~1-5 microseconds).

**Recommendation**: Increase polling interval to 30-60 seconds, or only stat files modified since last scan (track mtime of last scan).

---

## Summary of Search Service Issues

| # | Severity | Issue |
|---|---|---|
| 1 | **CRITICAL** | GlobalSearch calls `svc.Search(query)` but Search service only exposes `Query(query, limit)` — method name mismatch. File search is always broken. |
| 2 | **HIGH** | No proxy reconnection in GlobalSearch — if backend services restart, search permanently fails |
| 3 | **MEDIUM** | No score threshold on vector results — low-relevance matches still returned |
| 4 | **MEDIUM** | FTS5 default tokenizer does not handle CJK/Unicode word segmentation |
| 5 | **MEDIUM** | Apps indexer performs full table scan for semantic search (loads all embeddings into Python) |
| 6 | **MEDIUM** | No cross-source de-duplication or normalized scoring in GlobalSearch |
| 7 | **LOW** | Keyword scores not normalized across queries |
| 8 | **LOW** | Watchdog fallback polling is O(n) on every 10-second tick |
| 9 | **INFO** | FTS5 boolean `OR` and `*` prefix operators not escaped in keyword query tokens |
| 10 | **INFO** | No `SQLITE_BUSY` retry logic for concurrent WAL access |
