# Config File Persistence & Atomicity Analysis

**Files analyzed**:
- `services/axon-brain/brain_service.py` (save_config, load_config)
- `apps/intent-bar/spaces_manager.py` (_load, _save)
- `services/axon-context/clipboard_store.py`

**Date**: 2026-07-02

---

## 1. Brain Service Config (config.toml)

### save_config()

```python
def save_config(self):
    with self._config_lock:
        content = "# Axon OS AI Configuration\n\n"
        for k, v in self.config.items():
            escaped_v = str(v).replace("\\", "\\\\").replace('"', '\\"')
            content += f'{k} = "{escaped_v}"\n'
        tmp_path = CONFIG_FILE.with_suffix(".tmp")
        tmp_path.write_text(content)
        tmp_path.replace(CONFIG_FILE)
```

### Atomicity
| Check | Status | Notes |
|-------|--------|-------|
| Write-to-tmp then rename | **PASS** | Uses `Path.replace()` which is atomic on POSIX same-filesystem |
| Thread safety | **PASS** | `_config_lock` (RLock) serializes concurrent writes |
| Error handling | **PASS** | Catches all exceptions, logs them, does not crash |

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **HIGH** | **All TOML values written as strings** — Config stores `speed_model`, `general_model`, `deep_model` as strings, which works. But if future keys are added (e.g., `max_context_length: 4096`), they'll be written as `max_context_length = "4096"` — a string, not an integer. TOML consumers expecting int will get a string. The save function should detect types and write unquoted values for non-strings. |
| 2 | **HIGH** | **Incomplete TOML escaping** — Only escapes `\` and `"`. Does NOT escape:
  - Newlines (`\n`) — a value containing a newline will break TOML syntax
  - Tabs (`\t`)
  - Unicode control characters
  
  While model names are unlikely to contain these, it's a latent bug. Should use `tomli_w` or `tomlkit` for proper TOML serialization. |
| 3 | **MEDIUM** | **No config backup before overwrite** — If the atomic rename succeeds but the new config is somehow wrong (e.g., empty due to a bug in the loop), the old config is lost. Consider keeping a `.bak` file. |
| 4 | **MEDIUM** | **No schema version** — There is no `version` key in the config. When the config format changes in future releases, there is no way to detect and migrate old configs. |
| 5 | **LOW** | **Comment line in TOML** — The file starts with `# Axon OS AI Configuration` which is valid TOML. However, the manual serialization means future changes could accidentally emit invalid TOML. |
| 6 | **LOW** | **tmp file not in same directory** — `CONFIG_FILE.with_suffix(".tmp")` puts the temp file in the same directory, which is correct for atomic rename. No issue here, just noting it's properly done. |

### load_config()

```python
def load_config(self):
    AXON_DIR.mkdir(parents=True, exist_ok=True)
    with self._config_lock:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "rb") as f:
                    self.config = tomllib.load(f)
                if all(k in self.config for k in ("speed_model", "general_model", "deep_model")):
                    return
            except Exception as e:
                logger.debug("Config file not loaded, using defaults: %s", e)
        # Profile hardware and save default config
        profile = hardware_profiler.profile_hardware()
        self.config = {...}
        self.save_config()
```

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **HIGH** | **Corrupted config silently replaced with defaults** — If the config file is corrupted (e.g., disk error, partial write), the `except Exception` catches the parse error and falls through to `profile_hardware()` + `save_config()`. This **overwrites** the corrupted file with fresh defaults. The user's custom model selections are permanently lost with no warning (debug log only). Should at least back up the corrupted file before overwriting. |
| 2 | **MEDIUM** | **Missing keys cause full re-profiling** — If the config has `speed_model` and `general_model` but is missing `deep_model` (e.g., from an older version), the `all(...)` check fails and the entire config is re-generated from hardware profiling. This is overly aggressive — it should merge missing keys with existing ones rather than replacing everything. |
| 3 | **MEDIUM** | **`mkdir` outside lock** — `AXON_DIR.mkdir()` is called before acquiring `_config_lock`. In theory, if two BrainService instances start simultaneously, both could race on directory creation. In practice, `mkdir(parents=True, exist_ok=True)` is idempotent, so this is safe but inconsistent with the locking discipline. |
| 4 | **LOW** | **Config file path** — Config lives at `~/.local/share/axon/config.toml` (XDG data dir). XDG convention says configs should be in `$XDG_CONFIG_HOME` (i.e., `~/.config/axon/`). This is a standards compliance issue. |

---

## 2. Spaces Manager (spaces.json)

### _save()

```python
def _save(self) -> None:
    data = [s.to_dict() for s in self._spaces.values()]
    SPACES_FILE.write_text(json.dumps(data, indent=2))
```

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **CRITICAL** | **NOT ATOMIC** — `write_text()` directly overwrites the target file. If the process is killed mid-write (power loss, SIGKILL, OOM), the file will be truncated/corrupted. Unlike `brain_service.py`, there is NO tmp+rename pattern. Data loss is possible. |
| 2 | **HIGH** | **No thread lock** — `SpacesManager` has no synchronization primitive. `create_space()`, `update_space()`, `delete_space()`, and `add_app_to_space()` all call `_save()`. If two D-Bus method calls arrive concurrently (e.g., user quickly creates and renames a space), the in-memory state could be consistent but the file could be written interleaved. |
| 3 | **MEDIUM** | **No file locking** — Even with a thread lock, if multiple processes access `spaces.json` (e.g., intent-bar and another service), there is no file-level locking (e.g., `fcntl.flock`). |
| 4 | **LOW** | **Corrupted JSON recovery** — `_load()` handles `JSONDecodeError` by silently creating a new default space. The corrupted file is not backed up. |

### _load()

```python
def _load(self) -> None:
    if SPACES_FILE.exists():
        try:
            raw = json.loads(SPACES_FILE.read_text())
            for item in raw:
                space = Space.from_dict(item)
                self._spaces[space.id] = space
        except (json.JSONDecodeError, KeyError):
            pass
```

| # | Severity | Issue |
|---|----------|-------|
| 1 | **MEDIUM** | **Truncated file recovery** — `read_text()` reads the entire file into memory. If the file was truncated (partial write), `json.loads()` raises `JSONDecodeError` and the entire spaces list is lost — replaced with a single default space. The corrupted file is not preserved for debugging. |
| 2 | **LOW** | **No versioning** — No schema version. If `Space` fields change in future versions, old JSON files may fail to deserialize. |

---

## 3. Clipboard Store (clipboard.db)

### Concurrency Model

```python
def _get_connection(self):
    conn = sqlite3.connect(self.db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

### Assessment

| Check | Status | Notes |
|-------|--------|-------|
| Atomicity | **PASS** — SQLite handles atomicity via WAL mode |
| Thread safety | **PASS** — `threading.Lock()` serializes all DB operations |
| Crash recovery | **PASS** — SQLite WAL journal provides crash recovery |
| Connection management | **GOOD** — Connections are created and closed per operation |
| `check_same_thread=False` | **ACCEPTABLE** — Intentional: connections are created in one thread but the lock ensures only one thread uses them at a time |

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **MEDIUM** | **New connection per operation** — Every call to `add()`, `get_recent()`, `search()`, etc. creates a new `sqlite3.connect()`. This is expensive if operations are frequent (e.g., clipboard monitoring). Consider a connection pool or a single persistent connection protected by the lock. |
| 2 | **LOW** | **LIKE wildcard escaping could miss edge cases** — The `search()` method escapes `\`, `%`, `_` for LIKE queries. However, it does not escape the escape character itself in the query prefix (`%{safe_query}%`). If the query contains `\` followed by `%`, it could still match unexpectedly. |
| 3 | **INFO** | **WAL mode is correct choice** — Provides concurrent reads with single writer, which matches the usage pattern (many reads, fewer writes). |

---

## 4. Cross-Cutting Concerns

### Config Schema Migration

**None of the three systems has a schema version or migration mechanism.**

- `config.toml`: No version key. Missing keys trigger full re-profiling, which destroys user customization.
- `spaces.json`: No version. Field changes would cause deserialization failures.
- `clipboard.db`: Uses `CREATE TABLE IF NOT EXISTS`, which handles initial creation but not schema evolution (adding columns, changing indexes).

**Recommendation**: Add a `schema_version` integer to each config store. On load, compare versions and run migrations as needed.

### TOML Value Serialization

The manual TOML writer in `save_config()` is fragile. Consider using a proper TOML library:
- `tomli_w` (Python 3.11+): `tomli_w.dumps(config)` handles all escaping and type serialization
- `tomlkit`: Preserves comments and formatting

### Data Loss Risk Summary

| System | Power loss during write | Concurrent write | Corrupted file recovery |
|--------|------------------------|------------------|------------------------|
| config.toml | **Safe** (atomic rename) | **Safe** (RLock) | **UNSAFE** — overwrites with defaults |
| spaces.json | **UNSAFE** (direct write) | **UNSAFE** (no lock) | **UNSAFE** — replaces with default |
| clipboard.db | **Safe** (SQLite WAL) | **Safe** (Lock) | **Safe** (SQLite recovery) |

---

## 5. Recommendations

1. **[CRITICAL]** Make `spaces.json` saves atomic: write to `.tmp` then rename
2. **[CRITICAL]** Add thread locking to `SpacesManager` (or use `threading.RLock`)
3. **[HIGH]** Back up corrupted config files before overwriting with defaults
4. **[HIGH]** Use `tomli_w` or `tomlkit` for TOML serialization instead of manual string formatting
5. **[HIGH]** Add `schema_version` to config.toml for forward migration support
6. **[MEDIUM]** Handle missing config keys via merge (not full re-profiling)
7. **[MEDIUM]** Consider a connection pool for SQLite in ClipboardStore
8. **[LOW]** Move config.toml from XDG data dir to XDG config dir
9. **[LOW]** Add `fcntl.flock()` file locking for cross-process safety on spaces.json