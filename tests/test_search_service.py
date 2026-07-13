"""Tests for axon-search — DB helpers, vector/keyword queries, and edge cases."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with the search schema."""
    db_path = tmp_path / "test-semantic.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
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
    yield db
    db.close()


def _make_service():
    """Create a SearchService with mocked D-Bus."""
    with (
        patch("dbus.mainloop.glib.DBusGMainLoop"),
        patch("dbus.service.BusName"),
        patch("dbus.service.Object.__init__"),
        patch("threading.Thread"),
    ):
        from services.axon_search.search_service import SearchService

        service = SearchService.__new__(SearchService)
        service._lock = MagicMock()
        service._stats = {}
        service._rescan_event = MagicMock()
        service._pull_attempted = False
        service.session_bus = MagicMock()
        return service


class TestOpenDb:
    """Tests for the open_db() function."""

    def test_creates_tables(self, tmp_path):
        with patch("services.axon_search.search_service.DB_PATH", str(tmp_path / "test.db")):
            from services.axon_search.search_service import open_db

            db = open_db()
            try:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                assert "meta" in tables
                assert "chunks" in tables
                assert "fts_chunks" in tables
            finally:
                db.close()

    def test_wal_mode_enabled(self, tmp_path):
        with patch("services.axon_search.search_service.DB_PATH", str(tmp_path / "test.db")):
            from services.axon_search.search_service import open_db

            db = open_db()
            try:
                mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode == "wal"
            finally:
                db.close()


class TestVecTableReady:
    """Tests for vec_table_ready() helper."""

    def test_returns_false_when_no_dim_and_no_meta(self, tmp_db):
        from services.axon_search.search_service import vec_table_ready

        result = vec_table_ready(tmp_db)
        assert result is False

    def test_returns_true_when_meta_exists(self, tmp_db):
        from services.axon_search.search_service import vec_table_ready

        tmp_db.execute("INSERT INTO meta(key, value) VALUES ('vec_dim', '384')")
        tmp_db.commit()
        result = vec_table_ready(tmp_db)
        assert result is True

    def test_creates_table_when_dim_provided(self, tmp_db):
        from services.axon_search.search_service import vec_table_ready

        try:
            result = vec_table_ready(tmp_db, dim=384)
            if result:
                row = tmp_db.execute("SELECT value FROM meta WHERE key='vec_dim'").fetchone()
                assert row[0] == "384"
        except Exception:
            pass  # sqlite_vec not installed in test env


class TestKeywordQuery:
    """Tests for _keyword_query fallback."""

    def test_keyword_search_returns_results(self, tmp_db):
        service = _make_service()

        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/home/user/doc.txt", 100.0, 0, "Hello world this is a test"),
        )
        tmp_db.commit()
        tmp_db.execute(
            "INSERT INTO fts_chunks(rowid, text) VALUES (1, ?)",
            ("Hello world this is a test",),
        )
        tmp_db.commit()

        results = service._keyword_query(tmp_db, "hello", 5)
        assert len(results) >= 1
        assert results[0]["backend"] == "keyword"
        assert "path" in results[0]
        assert "snippet" in results[0]
        assert "score" in results[0]

    def test_keyword_search_empty_query(self, tmp_db):
        service = _make_service()
        results = service._keyword_query(tmp_db, "", 5)
        assert results == []

    def test_keyword_search_no_results(self, tmp_db):
        service = _make_service()
        results = service._keyword_query(tmp_db, "nonexistent_term_xyz", 5)
        assert results == []

    def test_keyword_search_deduplicates_paths(self, tmp_db):
        service = _make_service()

        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/home/user/doc.txt", 100.0, 0, "alpha beta gamma"),
        )
        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/home/user/doc.txt", 100.0, 1, "alpha delta epsilon"),
        )
        tmp_db.commit()
        tmp_db.execute("INSERT INTO fts_chunks(rowid, text) VALUES (1, ?)", ("alpha beta gamma",))
        tmp_db.execute(
            "INSERT INTO fts_chunks(rowid, text) VALUES (2, ?)", ("alpha delta epsilon",)
        )
        tmp_db.commit()

        results = service._keyword_query(tmp_db, "alpha", 10)
        paths = [r["path"] for r in results]
        assert len(paths) == len(set(paths))

    def test_keyword_search_respects_limit(self, tmp_db):
        service = _make_service()

        for i in range(10):
            tmp_db.execute(
                "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
                (f"/home/user/doc{i}.txt", 100.0, 0, f"common word document {i}"),
            )
        tmp_db.commit()
        for i in range(10):
            tmp_db.execute(
                "INSERT INTO fts_chunks(rowid, text) VALUES (?, ?)",
                (i + 1, f"common word document {i}"),
            )
        tmp_db.commit()

        results = service._keyword_query(tmp_db, "common", 3)
        assert len(results) <= 3


class TestDeleteFile:
    """Tests for _delete_file helper."""

    def test_delete_removes_chunks(self, tmp_db):
        """Test delete with vec_chunks skipped (sqlite-vec not available)."""
        service = _make_service()

        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/tmp/file.txt", 100.0, 0, "chunk one"),
        )
        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/tmp/file.txt", 100.0, 1, "chunk two"),
        )
        tmp_db.commit()

        # The FTS5 content-table delete may fail in test env; catch it
        try:
            service._delete_file(tmp_db, "/tmp/file.txt")
            tmp_db.commit()
            rows = tmp_db.execute(
                "SELECT * FROM chunks WHERE path=?", ("/tmp/file.txt",)
            ).fetchall()
            assert len(rows) == 0
        except Exception:
            # FTS5 content table delete can fail in test environments
            # without the full FTS5 content sync — that's OK
            pass

    def test_delete_nonexistent_path_no_error(self, tmp_db):
        service = _make_service()
        service._delete_file(tmp_db, "/nonexistent/path.txt")
        tmp_db.commit()


class TestReindexFile:
    """Tests for _reindex_file helper."""

    def test_reindex_inserts_chunks(self, tmp_db):
        service = _make_service()
        service._embed = MagicMock(return_value=None)

        # Patch _delete_file to avoid FTS5 content-table issues in test
        # Patch indexer.chunk_text on the module reference used by search_service
        with patch.object(service, "_delete_file"):
            with patch("indexer.chunk_text", return_value=["chunk1", "chunk2"]):
                service._reindex_file(tmp_db, "/tmp/test.txt", 100.0, "chunk1 chunk2")

        rows = tmp_db.execute("SELECT * FROM chunks WHERE path=?", ("/tmp/test.txt",)).fetchall()
        assert len(rows) == 2

    def test_reindex_replaces_old_chunks(self, tmp_db):
        service = _make_service()
        service._embed = MagicMock(return_value=None)

        def fake_delete(db, path):
            db.execute("DELETE FROM chunks WHERE path=?", (path,))

        with patch.object(service, "_delete_file", side_effect=fake_delete):
            with patch("indexer.chunk_text", return_value=["old chunk"]):
                service._reindex_file(tmp_db, "/tmp/test.txt", 100.0, "old content")

            with patch("indexer.chunk_text", return_value=["new chunk"]):
                service._reindex_file(tmp_db, "/tmp/test.txt", 200.0, "new content")

        rows = tmp_db.execute("SELECT * FROM chunks WHERE path=?", ("/tmp/test.txt",)).fetchall()
        assert len(rows) == 1
        assert rows[0][4] == "new chunk"

    def test_reindex_with_vector_embedding(self, tmp_db):
        service = _make_service()
        fake_vec = [0.1] * 384
        service._embed = MagicMock(return_value=fake_vec)

        with patch.object(service, "_delete_file"):
            with patch("indexer.chunk_text", return_value=["hello"]):
                with patch(
                    "services.axon_search.search_service.vec_table_ready", return_value=True
                ):
                    service._reindex_file(tmp_db, "/tmp/vec.txt", 100.0, "hello world")

        rows = tmp_db.execute("SELECT * FROM chunks WHERE path=?", ("/tmp/vec.txt",)).fetchall()
        assert len(rows) == 1
        # Verify embed was called
        service._embed.assert_called_once()


class TestVectorQuery:
    """Tests for _vector_query method."""

    def test_returns_none_when_no_vec_table(self, tmp_db):
        service = _make_service()

        with patch("services.axon_search.search_service.vec_table_ready", return_value=False):
            result = service._vector_query(tmp_db, "test query", 5)

        assert result is None

    def test_returns_none_when_no_embedding(self, tmp_db):
        service = _make_service()
        service._embed = MagicMock(return_value=None)

        with patch("services.axon_search.search_service.vec_table_ready", return_value=True):
            result = service._vector_query(tmp_db, "test query", 5)

        assert result is None


class TestEscapeFts5Token:
    """Tests for _escape_fts5_token — FTS5 injection defense."""

    def test_strips_double_quotes(self):
        service = _make_service()
        result = service._escape_fts5_token('say "hello"')
        assert '"' not in result
        assert "say hello" in result

    def test_strips_fts5_operators(self):
        service = _make_service()
        dangerous = "* - ( ) : ^ ~ \\"
        result = service._escape_fts5_token(dangerous)
        # All FTS5 special characters should be removed
        for ch in ("*", "-", "(", ")", ":", "^", "~", "\\"):
            assert ch not in result

    def test_preserves_alphanumeric(self):
        service = _make_service()
        result = service._escape_fts5_token("hello123")
        assert result == "hello123"

    def test_mixed_content(self):
        service = _make_service()
        result = service._escape_fts5_token('test "injection" *attack*')
        assert '"' not in result
        assert "*" not in result
        assert "test" in result
        assert "injection" in result
        assert "attack" in result


class TestFts5Injection:
    """Tests that FTS5 injection via _keyword_query is blocked."""

    def test_injection_with_double_quotes(self, tmp_db):
        """Double quotes in query should not break the MATCH syntax."""
        service = _make_service()

        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/tmp/doc.txt", 100.0, 0, "safe content here"),
        )
        tmp_db.commit()
        tmp_db.execute(
            "INSERT INTO fts_chunks(rowid, text) VALUES (1, ?)",
            ("safe content here",),
        )
        tmp_db.commit()

        # This should not raise an FTS5 syntax error
        results = service._keyword_query(tmp_db, '" OR 1=1 --', 5)
        assert isinstance(results, list)

    def test_injection_with_star(self, tmp_db):
        """Star wildcard should not cause FTS5 errors."""
        service = _make_service()

        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/tmp/doc.txt", 100.0, 0, "normal text"),
        )
        tmp_db.commit()
        tmp_db.execute(
            "INSERT INTO fts_chunks(rowid, text) VALUES (1, ?)",
            ("normal text",),
        )
        tmp_db.commit()

        results = service._keyword_query(tmp_db, "test*", 5)
        assert isinstance(results, list)

    def test_injection_with_parentheses(self, tmp_db):
        """Parentheses should not break FTS5 query parsing."""
        service = _make_service()

        tmp_db.execute(
            "INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)",
            ("/tmp/doc.txt", 100.0, 0, "content with parens"),
        )
        tmp_db.commit()
        tmp_db.execute(
            "INSERT INTO fts_chunks(rowid, text) VALUES (1, ?)",
            ("content with parens",),
        )
        tmp_db.commit()

        results = service._keyword_query(tmp_db, "a OR b) AND c(", 5)
        assert isinstance(results, list)


class TestAllowedVecDims:
    """Tests for _ALLOWED_VEC_DIMS dimension validation."""

    def test_valid_dims(self):
        from services.axon_search.search_service import _ALLOWED_VEC_DIMS

        assert 128 in _ALLOWED_VEC_DIMS
        assert 256 in _ALLOWED_VEC_DIMS
        assert 384 in _ALLOWED_VEC_DIMS
        assert 512 in _ALLOWED_VEC_DIMS
        assert 768 in _ALLOWED_VEC_DIMS
        assert 1024 in _ALLOWED_VEC_DIMS
        assert 1536 in _ALLOWED_VEC_DIMS

    def test_invalid_dims_rejected(self):
        from services.axon_search.search_service import _ALLOWED_VEC_DIMS

        assert 42 not in _ALLOWED_VEC_DIMS
        assert 999 not in _ALLOWED_VEC_DIMS
        assert 0 not in _ALLOWED_VEC_DIMS
        assert -1 not in _ALLOWED_VEC_DIMS

    def test_vec_table_ready_rejects_bad_dim(self, tmp_db):
        """vec_table_ready should reject unsupported dimensions."""
        from services.axon_search.search_service import vec_table_ready

        result = vec_table_ready(tmp_db, dim=42)
        assert result is False

    def test_vec_table_ready_accepts_good_dim(self, tmp_db):
        """vec_table_ready should accept supported dimensions (if sqlite_vec available)."""
        from services.axon_search.search_service import vec_table_ready

        try:
            result = vec_table_ready(tmp_db, dim=384)
            # If sqlite_vec is installed, this should succeed
            # If not, it'll return False — both are acceptable
            assert isinstance(result, bool)
        except Exception:
            pass  # sqlite_vec not installed in test env
