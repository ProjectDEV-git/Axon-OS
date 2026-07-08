# Debug Report 03: SQLite Database Management

**Agent**: Debug Agent 6 — File System & Search  
**Date**: 2026-07-02  
**Files Analyzed**:
- `services/axon-brain/conversation_store.py` (211 lines)
- `services/axon-search/search_service.py` (451 lines) — DB management aspects
- `services/axon-context/file_indexer.py` (196 lines) — DB management aspects
- `apps/axon-files/file_indexer.py` (400+ lines) — DB management aspects
- `services/constants.py` (51 lines)

---

## 1. WAL Mode Configuration

### `conversation_store.py` — Correct
```python
def _get_connection(self):
    conn = sqlite3.connect(self.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
```

**Good practices**:
- WAL mode enabled on every connection (idempotent — only takes effect once).
- Foreign keys enforced.
- `row_factory = sqlite3.Row` for named column access.

### `search_service.py` — Correct
```python
def open_db():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    if HAVE_SQLITE_VEC:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
```

**Good**: WAL mode, extension loading disabled after use (security best practice).

### `services/axon-context/file_indexer.py` — **MISSING WAL MODE**
```python
def __init__(self):
    self.conn = sqlite3.connect(str(DB_PATH))
```

**BUG**: No WAL mode, no `check_same_thread`, no PRAGMA configuration. This database runs in the default `DELETE` journal mode, which:
- Blocks readers during writes.
- Is slower for concurrent access.
- Does not handle crash recovery as gracefully as WAL.

### `apps/axon-files/file_indexer.py` — **MISSING WAL MODE**
```python
def init_db(self):
    conn = sqlite3.connect(self.db_path, timeout=30.0)
```

**Missing WAL mode**. Has `timeout=30.0` which helps with locking contention but does not solve the fundamental issue.

### WAL Mode Summary

| Database | WAL Mode | Foreign Keys | Timeout |
|---|---|---|---|
| `conversations.db` | Yes | Yes | Default (5s) |
| `semantic-index.db` | Yes | N/A | Default |
| `semantic_search.db` | **NO** | N/A | Default |
| `files_index.db` | **NO** | N/A | 30s |

---

## 2. Indexes for Conversation Lookups

### `conversation_store.py` Schema
```sql
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    system_prompt TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
```

**Analysis**:

| Query Pattern | Index Coverage | Status |
|---|---|---|
| `get_messages(conversation_id)` | `idx_messages_conversation` | Good |
| `list_conversations()` ORDER BY `updated_at DESC` | No index on `updated_at` | **Missing** |
| `search_messages(query)` WHERE `content LIKE ?` | No index (full scan expected for LIKE) | Acceptable |
| `delete_conversation(id)` | Primary key on `conversations.id` | Good |
| `create_conversation()` | Primary key on `conversations.id` | Good |

**Missing Index**: `updated_at` on conversations. For `list_conversations()` with `ORDER BY updated_at DESC`, SQLite performs a full table scan and sort. With thousands of conversations, this degrades. Add:
```sql
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
```

**Missing Index**: `title` for search. If conversations are ever searched by title, an index on `title` would help.

### `search_service.py` Schema
```sql
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)
```

This is the only index. For the `_scan_once` cleanup query:
```python
for (path,) in db.execute("SELECT DISTINCT path FROM chunks").fetchall():
```
An index on `path` helps, but the `DISTINCT` still requires scanning. This is fine for the periodic rescan.

---

## 3. Connection Pool / Concurrent Access

### `conversation_store.py` — Per-Thread Connection Pattern
```python
def _get_connection(self):
    conn = getattr(self._local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")  # health check
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
    return conn
```

**Analysis**:
- Uses `threading.local()` for per-thread connections. Each D-Bus handler thread gets its own connection.
- **Health check**: `SELECT 1` verifies the connection is alive before reuse.
- **BUT**: The `_close_connection` method is called after each operation, closing the per-thread connection. This means the "reuse" path in `_get_connection` is rarely hit — most calls create a new connection.

**Concern**: Every `create_conversation`, `add_message`, `get_messages`, etc. creates and closes a connection. This adds overhead (~0.1ms per operation per the docstring comment). For high-throughput scenarios (e.g., streaming conversation messages), this is suboptimal.

**Better approach**: Use a connection pool (e.g., `queue.Queue`) or keep connections alive per-thread and only close them on shutdown.

### `search_service.py` — Connection per Call
```python
def _scan_once(self):
    db = open_db()
    # ... do work ...
    db.close()

def Query(self, query, limit):
    db = open_db()
    try:
        results = self._vector_query(db, str(query), limit)
        return json.dumps(results)
    finally:
        db.close()
```

**Analysis**: Each D-Bus method call opens and closes a database connection. This is safe for concurrency (each call gets its own connection) but adds connection overhead. For a D-Bus service handling frequent queries, a persistent connection per thread would be more efficient.

### `apps/axon-files/file_indexer.py` — Connection per Operation
```python
def search_local(self, query_text, ...):
    conn = sqlite3.connect(self.db_path, timeout=30.0)
    # ... query ...
    conn.close()
```

Same pattern — open/query/close per call.

### Concurrency Risk Assessment

| Service | Thread Model | DB Concurrency | Risk |
|---|---|---|---|
| conversation_store | D-Bus (GLib mainloop) | Per-call connections + WAL | Low — WAL handles reads during writes |
| search_service | Background thread + D-Bus threads | Per-call connections + WAL | Low — WAL handles concurrent access |
| context file_indexer | Single background thread | Persistent connection | None — single-threaded |
| apps file_indexer | Called from D-Bus | Per-call connections | Low |

**Overall**: No critical concurrency bugs. The per-call connection pattern is safe but suboptimal. WAL mode in the critical databases (conversations, search index) prevents most locking issues.

---

## 4. Backup / Export Mechanism

### `conversation_store.py`
**No backup mechanism exists.** The conversation database grows indefinitely. There is:
- No backup API
- No export function
- No data retention/cleanup policy
- No VACUUM schedule

Over months of use, the database will grow as old conversations accumulate. With foreign keys enabled and CASCADE delete, deleting a conversation removes its messages, but there is no automated cleanup.

### `search_service.py`
**No backup mechanism.** The semantic index can be rebuilt by re-scanning files (`Reindex()` D-Bus method), so backup is less critical. However, the embedding generation is expensive (Ollama inference), so losing the index means expensive re-computation.

### `files_index.db`
**No backup mechanism.** Same as search — can be rebuilt by re-scanning, but at computational cost.

### `semantic_search.db`
**No backup mechanism.** Can be rebuilt from Ollama embeddings.

### Recommendation
Add a simple backup mechanism for `conversations.db`:
```python
def backup(self, backup_path):
    """Create a backup of the conversations database."""
    conn = self._get_connection()
    try:
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
    finally:
        self._close_connection(conn)
```

SQLite's `connection.backup()` API (Python 3.7+) provides online backup without locking the database.

---

## 5. Schema Future-Proofing (Migration Strategy)

### `conversation_store.py`
**No migration strategy.** The schema is created with `CREATE TABLE IF NOT EXISTS`, which works for initial setup but does not handle schema changes. If a future version adds columns (e.g., `model_used`, `token_count`, `metadata`), there is no mechanism to ALTER existing tables.

**Recommendation**: Add a `schema_version` table and migration logic:
```python
def _init_db(self):
    conn = self._get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current_version = row[0] if row else 0
        if current_version < 1:
            # Create initial tables
            ...
        if current_version < 2:
            # Migration: add metadata column
            conn.execute("ALTER TABLE conversations ADD COLUMN metadata TEXT")
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES (?)", (LATEST_VERSION,))
        conn.commit()
    finally:
        self._close_connection(conn)
```

### `search_service.py`
Uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`, which is idempotent. The vec0 virtual table is created lazily via `vec_table_ready()`. This approach handles first-run gracefully but has the same limitation: no way to modify existing schemas.

### `files_index.db`
Same `CREATE TABLE IF NOT EXISTS` approach. No migration.

### `semantic_search.db`
Same pattern. No migration.

### Overall Assessment
All four databases use an immutable-schema pattern (create-if-not-exists). This is acceptable for an early-stage project but will become a pain point as the schema evolves. For a 1.0 release, a lightweight migration system is recommended.

---

## 6. Additional Findings

### `conversation_store.py` — File Permissions
```python
try:
    os.chmod(self.db_path, 0o600)
except OSError:
    pass
```
**Good practice**: Restricts database file to owner-only access. This protects conversation history from other users on the system.

**Minor issue**: The `.db-wal` and `.db-shm` files created by WAL mode are not chmod'd. These sidecar files contain recent uncommitted data and should also be restricted:
```python
for suffix in ("-wal", "-shm"):
    try:
        os.chmod(self.db_path + suffix, 0o600)
    except OSError:
        pass
```

### `conversation_store.py` — Connection Leak in _get_connection
```python
def _get_connection(self):
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
    ...
    self._local.conn = conn
    return conn
```

**Issue**: The health check `conn.execute("SELECT 1")` creates a cursor but does not fetch/close it. This is a minor resource leak. In practice, Python's garbage collector handles this, but explicit cursor management is cleaner:
```python
try:
    conn.execute("SELECT 1").fetchone()
    return conn
```

### Database File Locations

| Database | Path | Purpose |
|---|---|---|
| `conversations.db` | `~/.local/share/axon/conversations.db` | Chat history |
| `semantic-index.db` | `~/.local/share/axon/semantic-index.db` | Search chunks + vectors |
| `semantic_search.db` | `~/.local/share/axon/semantic_search.db` | Context indexer (sqlite-vec) |
| `files_index.db` | `~/.local/share/axon/files_index.db` | Files app index |

**Concern**: Four separate databases in the same directory. Total disk usage could be significant (especially `semantic_search.db` with 768-dim vector embeddings). No disk usage monitoring or cleanup.

### VACUUM and Maintenance
No database performs periodic `VACUUM` or `PRAGMA optimize`. Over time:
- WAL mode databases accumulate `-wal` files.
- Deleted rows leave free pages.
- Indexes become fragmented.

Recommendation: Run `PRAGMA optimize` on shutdown and periodic `VACUUM` (monthly or when DB exceeds a size threshold).

---

## Summary of SQLite Management Issues

| # | Severity | Issue |
|---|---|---|
| 1 | **HIGH** | `semantic_search.db` and `files_index.db` missing WAL mode — degrades concurrent access |
| 2 | **MEDIUM** | No schema migration strategy for any database |
| 3 | **MEDIUM** | No backup/export mechanism for conversations database |
| 4 | **MEDIUM** | WAL sidecar files (-wal, -shm) not restricted to owner-only permissions |
| 5 | **LOW** | Missing index on `conversations.updated_at` for sorted listing |
| 6 | **LOW** | Per-call connection creation/closing adds overhead (no connection pooling) |
| 7 | **LOW** | No periodic VACUUM or `PRAGMA optimize` |
| 8 | **LOW** | No disk usage monitoring or data retention policy |
| 9 | **INFO** | Four separate databases with no unified management |
| 10 | **INFO** | Minor cursor leak in connection health check |
