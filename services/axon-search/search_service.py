#!/usr/bin/env python3
"""Axon Semantic Search — ambient vector index over the user's files.

Exposes org.axonos.Search on the session bus. Files under a few home
directories are chunked, embedded through org.axonos.Brain.GetEmbeddings
(Ollama `nomic-embed-text`), and stored in a sqlite-vec virtual table at
~/.axon/semantic-index.db. When embeddings are unavailable (Ollama offline,
model not pulled, sqlite-vec missing) queries fall back to SQLite FTS5
keyword search so the Intent Bar always gets an answer.
"""

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indexer

AXON_DIR = Path.home() / ".axon"
DB_PATH = AXON_DIR / "semantic-index.db"
EMBED_MODEL = "nomic-embed-text"
RESCAN_INTERVAL = 15 * 60  # seconds between full rescans

try:
    import sqlite_vec

    HAVE_SQLITE_VEC = True
except ImportError:
    HAVE_SQLITE_VEC = False


def open_db():
    """Open (and migrate) the index database. One connection per thread."""
    AXON_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    if HAVE_SQLITE_VEC:
        try:
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
        except Exception:
            pass
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS chunks ("
        " id INTEGER PRIMARY KEY,"
        " path TEXT NOT NULL,"
        " mtime REAL NOT NULL,"
        " chunk_idx INTEGER NOT NULL,"
        " text TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks"
        " USING fts5(text, content='chunks', content_rowid='id')"
    )
    db.commit()
    return db


def vec_table_ready(db, dim=None):
    """Ensure the vec0 table exists; created lazily once the dim is known."""
    row = db.execute("SELECT value FROM meta WHERE key='vec_dim'").fetchone()
    if row:
        return True
    if dim is None:
        return False
    try:
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks"
            f" USING vec0(embedding float[{int(dim)}])"
        )
        db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('vec_dim', ?)",
            (str(int(dim)),),
        )
        db.commit()
        return True
    except sqlite3.OperationalError:
        return False


class SearchService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        try:
            self.bus_name = dbus.service.BusName("org.axonos.Search", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            print("org.axonos.Search service is already running.")
            sys.exit(1)
        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/Search")

        self._lock = threading.Lock()
        self._stats = {
            "files": 0,
            "chunks": 0,
            "vector_backend": False,
            "last_scan": 0.0,
            "indexing": False,
        }
        self._rescan_event = threading.Event()
        self._pull_attempted = False
        threading.Thread(target=self._index_loop, daemon=True).start()
        print("Axon Search D-Bus service registered at /org/axonos/Search")

    # ------------------------------------------------------------------
    # Brain helpers
    # ------------------------------------------------------------------

    def _brain(self):
        try:
            obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            return dbus.Interface(obj, "org.axonos.Brain")
        except dbus.exceptions.DBusException:
            return None

    def _embed(self, text):
        """Return an embedding vector (list[float]) or None."""
        brain = self._brain()
        if brain is None:
            return None
        try:
            raw = brain.GetEmbeddings(text, EMBED_MODEL, timeout=30)
            vec = json.loads(raw)
            if isinstance(vec, list) and vec and isinstance(vec[0], (int, float)):
                return vec
            # Brain returned {"error": ...} — try pulling the model once.
            if not self._pull_attempted:
                self._pull_attempted = True
                try:
                    brain.PullModel(EMBED_MODEL)
                except dbus.exceptions.DBusException:
                    pass
        except (dbus.exceptions.DBusException, ValueError):
            pass
        return None

    # ------------------------------------------------------------------
    # Index loop
    # ------------------------------------------------------------------

    def _index_loop(self):
        time.sleep(20)  # let the session settle before the first scan
        while True:
            try:
                self._scan_once()
            except Exception as exc:  # never kill the loop
                print(f"[axon-search] scan error: {exc}")
            self._rescan_event.wait(RESCAN_INTERVAL)
            self._rescan_event.clear()

    def _scan_once(self):
        db = open_db()
        self._stats["indexing"] = True
        files = chunks = 0
        try:
            known_mtimes = {
                row[0]: row[1]
                for row in db.execute("SELECT path, mtime FROM chunks GROUP BY path").fetchall()
            }
            for path in indexer.iter_candidate_files(Path.home()):
                try:
                    mtime = Path(path).stat().st_mtime
                except OSError:
                    continue

                known_mtime = known_mtimes.get(path)
                if known_mtime is not None and abs(known_mtime - mtime) < 0.5:
                    files += 1
                    continue

                text = indexer.read_text(path)
                if text is None:
                    continue
                self._reindex_file(db, path, mtime, text)
                files += 1
                chunks += 1
            # Drop rows for files that no longer exist.
            for (path,) in db.execute("SELECT DISTINCT path FROM chunks").fetchall():
                if not Path(path).exists():
                    self._delete_file(db, path)
            db.commit()
            total_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            self._stats.update(
                files=files,
                chunks=total_chunks,
                vector_backend=vec_table_ready(db),
                last_scan=time.time(),
            )
        finally:
            self._stats["indexing"] = False
            db.close()

    def _delete_file(self, db, path):
        ids = [r[0] for r in db.execute("SELECT id FROM chunks WHERE path=?", (path,)).fetchall()]
        for cid in ids:
            db.execute(
                "INSERT INTO fts_chunks(fts_chunks, rowid, text)"
                " VALUES ('delete', ?,"
                " (SELECT text FROM chunks WHERE id=?))",
                (cid, cid),
            )
            try:
                db.execute("DELETE FROM vec_chunks WHERE rowid=?", (cid,))
            except sqlite3.OperationalError:
                pass
        db.execute("DELETE FROM chunks WHERE path=?", (path,))

    def _reindex_file(self, db, path, mtime, text):
        self._delete_file(db, path)
        for idx, chunk in enumerate(indexer.chunk_text(text)):
            cur = db.execute(
                "INSERT INTO chunks(path, mtime, chunk_idx, text)" " VALUES (?,?,?,?)",
                (path, mtime, idx, chunk),
            )
            cid = cur.lastrowid
            db.execute("INSERT INTO fts_chunks(rowid, text) VALUES (?,?)", (cid, chunk))
            vec = self._embed(f"search_document: {chunk}")
            if vec and vec_table_ready(db, dim=len(vec)):
                try:
                    db.execute(
                        "INSERT INTO vec_chunks(rowid, embedding) VALUES (?,?)",
                        (cid, json.dumps(vec)),
                    )
                except sqlite3.OperationalError:
                    pass
        db.commit()

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.Search", in_signature="si", out_signature="s")
    def Query(self, query, limit):
        """Semantic (vector) search with FTS5 keyword fallback.

        Returns a JSON array of {path, snippet, score, backend}.
        """
        limit = max(1, min(int(limit) or 8, 25))
        db = open_db()
        try:
            results = self._vector_query(db, str(query), limit)
            if results is None:
                results = self._keyword_query(db, str(query), limit)
            return json.dumps(results)
        finally:
            db.close()

    def _vector_query(self, db, query, limit):
        if not vec_table_ready(db):
            return None
        vec = self._embed(f"search_query: {query}")
        if not vec:
            return None
        try:
            rows = db.execute(
                "SELECT c.path, c.text, v.distance"
                " FROM vec_chunks v JOIN chunks c ON c.id = v.rowid"
                " WHERE v.embedding MATCH ? AND v.k = ?"
                " ORDER BY v.distance",
                (json.dumps(vec), limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            return None
        out, seen = [], set()
        for path, text, dist in rows:
            if path in seen:
                continue
            seen.add(path)
            out.append(
                {
                    "path": path,
                    "snippet": text[:220],
                    "score": round(1.0 / (1.0 + float(dist)), 4),
                    "backend": "vector",
                }
            )
            if len(out) >= limit:
                break
        return out

    def _keyword_query(self, db, query, limit):
        terms = " OR ".join(f'"{t}"' for t in query.replace('"', " ").split() if t)
        if not terms:
            return []
        try:
            rows = db.execute(
                "SELECT c.path, snippet(fts_chunks, 0, '', '', '…', 24), rank"
                " FROM fts_chunks JOIN chunks c ON c.id = fts_chunks.rowid"
                " WHERE fts_chunks MATCH ? ORDER BY rank LIMIT ?",
                (terms, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out, seen = [], set()
        for path, snip, rank in rows:
            if path in seen:
                continue
            seen.add(path)
            out.append(
                {
                    "path": path,
                    "snippet": snip[:220],
                    "score": round(-float(rank), 4),
                    "backend": "keyword",
                }
            )
            if len(out) >= limit:
                break
        return out

    @dbus.service.method("org.axonos.Search", in_signature="", out_signature="b")
    def Reindex(self):
        """Trigger a rescan now."""
        self._rescan_event.set()
        return True

    @dbus.service.method("org.axonos.Search", in_signature="", out_signature="s")
    def GetStats(self):
        return json.dumps(self._stats)


if __name__ == "__main__":
    loop = GLib.MainLoop()
    service = SearchService()
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()
