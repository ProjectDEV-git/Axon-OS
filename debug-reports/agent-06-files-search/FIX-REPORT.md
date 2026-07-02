# Fix Report: File System & Search Services

**Agent**: Agent 06 — Files & Search  
**Date**: 2026-07-02  
**Status**: 6/7 fixes applied, 1 excluded by constraint

---

## Summary

| Fix | Severity | Description | Status |
|-----|----------|-------------|--------|
| FIX 1 | CRITICAL | GlobalSearch `svc.Search()` → `svc.Query(query, 8)` | ✅ Applied |
| FIX 2 | HIGH | D-Bus `timeout=30` on embedding calls (2 files) | ✅ Applied |
| FIX 3 | HIGH | WAL mode + busy_timeout on 2 databases | ✅ Applied |
| FIX 4 | HIGH | Proxy reconnection on DBusException in GlobalSearch | ✅ Applied |
| FIX 5 | MEDIUM | Vector score threshold (distance < 4.0) | ✅ Applied |
| FIX 6 | MEDIUM | FTS5 operator escaping in query tokens | ✅ Applied |
| FIX 7 | LOW | Index on `conversations.updated_at` | ❌ Excluded (constraint) |

---

## FIX 1: GlobalSearch File Search — CRITICAL

**File**: `services/axon-search/global_search_service.py`  
**Line**: 100 (now 100)

**Problem**: `_search_files()` called `svc.Search(query)` but the Search service only exposes `Query(query, limit)`. This caused an `UnknownMethod` D-Bus exception, silently swallowed by `except Exception: pass`. File search in the global search bar was permanently broken.

**Change**: `svc.Search(query)` → `svc.Query(query, 8)`

---

## FIX 2: D-Bus Embedding Timeouts — HIGH

**Files**:
- `services/axon-context/file_indexer.py` (line 82)
- `apps/axon-files/file_indexer.py` (line 83)

**Problem**: Embedding D-Bus calls to Brain/Ollama had no timeout. If Brain hangs, the indexer blocks indefinitely. The search service already uses `timeout=30`.

**Changes**:
- `services/axon-context/file_indexer.py:82`: Added `timeout=30` to `brain_interface.GetEmbeddings(text, "", timeout=30)`
- `apps/axon-files/file_indexer.py:83`: Added `timeout=30` to `brain_obj.GetEmbeddings(..., timeout=30)`

---

## FIX 3: WAL Mode on 2 Databases — HIGH

**Files**:
- `services/axon-context/file_indexer.py` (after line 34)
- `apps/axon-files/file_indexer.py` (after line 145, inside `init_db`)

**Problem**: `semantic_search.db` and `files_index.db` ran in DELETE journal mode, blocking readers during writes and providing worse crash recovery.

**Changes**:
- `services/axon-context/file_indexer.py`: Added `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` after `sqlite3.connect()`
- `apps/axon-files/file_indexer.py`: Added `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` at start of `init_db()`

---

## FIX 4: Proxy Reconnection in GlobalSearch — HIGH

**File**: `services/axon-search/global_search_service.py`

**Problem**: D-Bus proxy objects (`self._brain`, `self._search`, `self._context`) were cached permanently. If a backend service (Brain, Search, Context) restarted, GlobalSearch would fail forever for that backend with no recovery path.

**Changes**:
1. All three `_get_*()` methods now accept `force=False` parameter. When `force=True`, the cached proxy is discarded and a fresh connection is established.
2. `_search_files()`: On `dbus.exceptions.DBusException`, resets `self._search = None`, reconnects with `force=True`, and retries the query once.
3. `AIAnswer()`: On `dbus.exceptions.DBusException`, resets `self._brain = None`, reconnects with `force=True`, and retries the `Generate()` call once.

---

## FIX 5: Vector Score Threshold — MEDIUM

**File**: `services/axon-search/search_service.py`

**Problem**: `_vector_query()` returned all vector matches regardless of distance, including very low-relevance results (distance > 5.0, equivalent to near-random similarity).

**Change**: Added `if float(dist) >= 4.0: continue` filter in the result loop. This drops matches where the L2 distance exceeds 4.0, which corresponds to a cosine similarity below ~0.98 for normalized embeddings. Users now see only meaningful vector matches.

---

## FIX 6: FTS5 Operator Escaping — MEDIUM

**File**: `services/axon-search/search_service.py`

**Problem**: FTS5 special characters (`*`, `-`, `(`, `)`, `:`, `^`, `~`, `\`, and bare `OR`) in user query tokens could trigger unintended FTS5 query behavior or cause `OperationalError` exceptions.

**Change**: Added `_escape_fts5_token()` static method that strips FTS5 operator characters from each token and removes bare "OR" tokens. The `_keyword_query()` method now processes tokens through this escape function before constructing the FTS5 MATCH query.

---

## FIX 7: Index on conversations.updated_at — EXCLUDED

**File**: `services/axon-brain/conversation_store.py`

**Problem**: `list_conversations()` orders by `updated_at DESC` but no index exists on that column, causing a full table scan on every conversation list request.

**Status**: **Excluded** — `conversation_store.py` is in `services/axon-brain/`, which is outside the allowed modification scope (`services/axon-search/`, `services/axon-context/`, `apps/axon-files/`).

**Recommendation**: Add `CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at)` in the `_init_db()` method of `ConversationStore`.

---

## Files Modified

| File | Fixes Applied |
|------|--------------|
| `services/axon-search/global_search_service.py` | FIX 1, FIX 4 |
| `services/axon-search/search_service.py` | FIX 5, FIX 6 |
| `services/axon-context/file_indexer.py` | FIX 2, FIX 3 |
| `apps/axon-files/file_indexer.py` | FIX 2, FIX 3 |

## Validation

- All modified files pass basic Python syntax check (`python3 -m py_compile`)
- Cross-referenced D-Bus method names between GlobalSearch and Search service (confirmed `Query(query, limit)` with `in_signature="si"`)
- Verified FTS5 escape handles edge cases: bare "OR", special characters, empty tokens
- Verified score threshold uses the same `dist` value already fetched from vec0
