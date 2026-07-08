# Debug Summary: File System & Search Services

**Agent**: Debug Agent 6  
**Date**: 2026-07-02  
**Scope**: File indexing, search, semantic search, SQLite database management  
**Files reviewed**: 6 source files, 3 test files, 1 constants file  

---

## CRITICAL BUGS

### 1. GlobalSearch File Search is Permanently Broken
**File**: `services/axon-search/global_search_service.py:100`  
**Impact**: The unified search (`org.axonos.GlobalSearch`) **never returns file results**.

`GlobalSearchService._search_files()` calls `svc.Search(query)` but the Search service only exposes `Query(query, limit)`. The D-Bus method name mismatch causes an `UnknownMethod` exception, silently caught by `except Exception: pass`. Only app search and settings search work in the global search bar.

**Fix**: Change line 100 from `svc.Search(query)` to `svc.Query(query, 8)`.

### 2. Two Duplicate File Indexer Systems
**Files**: `services/axon-context/file_indexer.py` + `apps/axon-files/file_indexer.py`  
**Impact**: Wasted CPU, memory, and disk. The context indexer runs every 30 seconds making redundant Ollama embedding calls on the same files the search service already indexes.

The context indexer writes to `semantic_search.db` (sqlite-vec), the apps indexer writes to `files_index.db` (JSON embeddings), and the search service writes to `semantic-index.db` (sqlite-vec + FTS5). Three databases indexing overlapping file sets.

---

## HIGH-SEVERITY WARNINGS

### 3. No D-Bus Timeout on Embedding Calls (2 indexers)
**Files**: `services/axon-context/file_indexer.py:80`, `apps/axon-files/file_indexer.py:83`  
The embedding D-Bus calls have no timeout. If Brain/Ollama hangs, the indexer blocks indefinitely. The search service correctly uses `timeout=30`.

### 4. Missing WAL Mode on 2 Databases
**Files**: `services/axon-context/file_indexer.py:34`, `apps/axon-files/file_indexer.py:145`  
`semantic_search.db` and `files_index.db` run in DELETE journal mode, blocking readers during writes and providing worse crash recovery.

### 5. No Proxy Reconnection in GlobalSearch
**File**: `services/axon-search/global_search_service.py:55-80`  
D-Bus proxy objects are cached permanently. If any backend service (Brain, Search, Context) restarts, the GlobalSearch silently fails all queries to that service forever.

---

## MEDIUM-SEVERITY WARNINGS

| # | Area | Issue |
|---|---|---|
| 6 | **Indexer** | Context indexer's `os.walk` does not prune hidden dirs in-place — wastes I/O descending into `.cache`, `.git` etc. |
| 7 | **Indexer** | Context indexer runs every 30 seconds (excessive for background work). Search service's 15-minute interval is appropriate. |
| 8 | **Semantic Search** | No score threshold on vector results — low-relevance matches (distance > 5.0) still returned to users. |
| 9 | **FTS5** | Default tokenizer does not handle CJK text. Queries in Chinese/Japanese/Korean will have poor recall. |
| 10 | **Apps Indexer** | Semantic search loads ALL embeddings into Python for brute-force cosine similarity. Does not scale beyond ~5K files. |
| 11 | **SQLite** | No schema migration strategy for any of the 4 databases. |
| 12 | **SQLite** | No backup/export mechanism for conversation history. |
| 13 | **GlobalSearch** | No cross-source de-duplication. Different sources use incomparable scoring scales. |
| 14 | **SQLite** | WAL sidecar files (-wal, -shm) not restricted to owner-only permissions. |

---

## LOW-SEVERITY / INFO

| # | Area | Issue |
|---|---|---|
| 15 | Indexer | Double `stat()` syscall per file in context indexer |
| 16 | Indexer | Inconsistent watch directories across indexers |
| 17 | Indexer | `sqlite_vec` import crash risk in context indexer (no try/except) |
| 18 | Search | Keyword FTS5 operators (`OR`, `*`) not escaped in query tokens |
| 19 | Search | No `SQLITE_BUSY` retry logic for concurrent WAL access |
| 20 | SQLite | Missing index on `conversations.updated_at` for sorted listing |
| 21 | SQLite | Per-call connection creation/closing (no pooling) |
| 22 | SQLite | No periodic VACUUM or `PRAGMA optimize` |
| 23 | SQLite | No disk usage monitoring or data retention policy |

---

## KEY RECOMMENDATIONS (Priority Order)

1. **Fix GlobalSearch file search** — Change `svc.Search(query)` to `svc.Query(query, 8)`. One-line fix, immediate impact.

2. **Consolidate file indexers** — Merge the context and apps indexers into the search service's indexer. Use `services/axon-search/indexer.py` as the shared module. Eliminate `semantic_search.db` and `files_index.db`.

3. **Add D-Bus timeouts** to embedding calls in the context and apps indexers (30 seconds, matching search service).

4. **Enable WAL mode** on `semantic_search.db` and `files_index.db`.

5. **Add proxy reconnection** in GlobalSearch — catch D-Bus exceptions and reset cached proxies.

6. **Add FTS5 `unicode61` tokenizer** for better international text support.

7. **Add a schema migration system** — even a simple version-number approach.

8. **Add conversation backup** using SQLite's `connection.backup()` API.

---

## VALIDATION PERFORMED

- Static code analysis of all 6 source files
- Cross-reference of D-Bus method names between services (confirmed `Search` vs `Query` mismatch)
- SQL injection analysis of all query paths (parameterized queries confirmed safe)
- Edge case analysis of `cosine_similarity` (all correct)
- Test coverage review (tests exist for search_service, indexer, and conversation_store)
- WAL mode verification across all databases
- Connection management pattern review
- Index coverage analysis for conversation queries

---

## REPORT FILES

| File | Contents |
|---|---|
| `01-file-indexer.md` | File indexer performance, error handling, efficiency (8 findings) |
| `02-search-service.md` | Search correctness, SQL safety, Unicode, dedup (10 findings) |
| `03-sqlite-management.md` | WAL mode, indexes, connections, backup, migrations (10 findings) |
| `SUMMARY.md` | This file — consolidated critical/high/medium/low findings |
