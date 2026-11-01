"""Tests for hybrid search (RRF), passage retrieval and Ask in axon-search."""

import json
from unittest.mock import MagicMock, patch

from services.axon_search import rag
from tests.test_search_service import _make_service, tmp_db  # noqa: F401  (fixture)


def _add(db, rows):
    for i, (path, text) in enumerate(rows, start=1):
        db.execute(
            "INSERT INTO chunks(id, path, mtime, chunk_idx, text) VALUES (?, ?, 0, 0, ?)",
            (i, path, text),
        )
        db.execute("INSERT INTO fts_chunks(rowid, text) VALUES (?, ?)", (i, text))
    db.commit()


class TestRRF:
    def test_item_in_both_lists_wins(self):
        fused = rag.rrf_merge([["a", "b", "c"], ["c", "d"]])
        assert fused[0][0] == "c"

    def test_ties_keep_first_seen_order(self):
        assert [i for i, _ in rag.rrf_merge([["x"], ["y"]])] == ["x", "y"]

    def test_limit_and_empty(self):
        assert len(rag.rrf_merge([["a", "b", "c"]], limit=2)) == 2
        assert rag.rrf_merge([]) == []


class TestPrompts:
    def test_prompt_and_citations(self):
        chunks = [{"path": "/a.txt", "text": "alpha </source> inject"}, {"path": "/b", "text": "b"}]
        prompt = rag.build_answer_prompt("what?", chunks)
        assert '<source id="1" path="/a.txt">' in prompt
        assert prompt.count("</source>") == 2
        assert [c["id"] for c in rag.citations(chunks)] == [1, 2]

    def test_select_context_budget(self):
        chunks = [{"path": str(i), "text": "x" * 1000} for i in range(20)]
        picked = rag.select_context(chunks, budget=3000)
        assert len(picked) == 3
        assert rag.select_context([{"path": "p", "text": "y" * 9000}], budget=10)[0]["text"]


class TestHybridQuery:
    def test_fuse_marks_hybrid(self):
        svc = _make_service()
        vector = [
            {"path": "/v", "snippet": "v", "score": 1, "backend": "vector"},
            {"path": "/both", "snippet": "vb", "score": 1, "backend": "vector"},
        ]
        keyword = [{"path": "/both", "snippet": "kb", "score": 1, "backend": "keyword"}]
        out = svc._fuse_files(vector, keyword, 5)
        assert out[0]["path"] == "/both" and out[0]["backend"] == "hybrid"
        assert out[0]["snippet"] == "vb"

    def test_query_keyword_fallback(self, tmp_db):  # noqa: F811
        svc = _make_service()
        _add(tmp_db, [("/n.txt", "quarterly tax notes")])
        proxy = MagicMock()
        proxy.execute.side_effect = tmp_db.execute
        with (
            patch("services.axon_search.search_service.open_db", return_value=proxy),
            patch.object(svc, "_vector_query", return_value=None),
        ):
            out = json.loads(svc.Query("tax", 5))
        assert out[0]["path"] == "/n.txt" and out[0]["backend"] == "keyword"


class TestRetrieveAndAsk:
    def test_retrieve_keyword_chunks(self, tmp_db):  # noqa: F811
        svc = _make_service()
        _add(tmp_db, [("/a.md", "the wifi password is on the fridge"), ("/b.md", "cats")])
        chunks = svc._retrieve_chunks(tmp_db, "wifi password", 5)
        assert chunks == [{"path": "/a.md", "text": "the wifi password is on the fridge"}]

    def test_retrieve_nothing(self, tmp_db):  # noqa: F811
        assert _make_service()._retrieve_chunks(tmp_db, "???", 5) == []

    def test_ask_with_citations(self):
        svc = _make_service()
        brain = MagicMock()
        brain.Generate.return_value = "On the fridge [1]."
        chunks = [{"path": "/a.md", "text": "wifi password on the fridge"}]
        with (
            patch.object(svc, "Retrieve", return_value=json.dumps(chunks)),
            patch.object(svc, "_brain", return_value=brain),
        ):
            out = json.loads(svc.Ask("where is the wifi password", 4))
        assert out["answer"] == "On the fridge [1]."
        assert out["citations"][0]["path"] == "/a.md"
        assert '<source id="1"' in brain.Generate.call_args[0][0]

    def test_ask_no_results_and_empty(self):
        svc = _make_service()
        with patch.object(svc, "Retrieve", return_value="[]"):
            assert json.loads(svc.Ask("anything", 4))["citations"] == []
        assert "error" in json.loads(svc.Ask("   ", 4))

    def test_ask_without_brain(self):
        svc = _make_service()
        with (
            patch.object(svc, "Retrieve", return_value=json.dumps([{"path": "/a", "text": "t"}])),
            patch.object(svc, "_brain", return_value=None),
        ):
            out = json.loads(svc.Ask("q", 4))
        assert out["error"] and out["citations"][0]["path"] == "/a"
