"""Tests for the AI panel's Axon status summary."""

from __future__ import annotations

from services.diagnostics import FAIL, INFO, OK, WARN, CheckResult
from services.status_summary import build_status, index_row, pull_text


def unit(name: str, status: str = OK, summary: str = "running", hint: str = "") -> CheckResult:
    return CheckResult("Axon services", f"{name}.service", status, summary, hint=hint)


HEALTHY = [
    unit("axon-brain"),
    unit("axon-search"),
    unit("axon-voice"),
    CheckResult("AI", "Ollama", OK, "running, version 0.5"),
]


def test_all_good() -> None:
    s = build_status(HEALTHY, "Local · llama3.2:3b", {"files": 1234, "vector_backend": True})
    assert s.status == OK and s.headline == "All good" and s.problem is None
    assert [(r.name, r.text) for r in s.rows] == [
        ("Axon Brain", "running"),
        ("Ollama", "running, version 0.5"),
        ("Model", "Local · llama3.2:3b"),
        ("Semantic index", "1,234 files (semantic search)"),
        ("Voice", "running"),
    ]


def test_worst_problem_and_its_fix_are_shown() -> None:
    results = [
        unit("axon-brain"),
        unit("axon-voice", WARN, "not running (inactive)", hint="systemctl --user restart"),
        CheckResult(
            "AI", "Ollama", FAIL, "Ollama is not responding", hint="sudo systemctl start ollama"
        ),
    ]
    s = build_status(results, "Local · x")
    assert s.status == FAIL
    assert s.problem is not None and s.problem.name == "Ollama"
    assert s.headline == "Ollama: Ollama is not responding"
    assert "systemctl start ollama" in s.problem.hint


def test_download_row() -> None:
    pull = {"model": "llama3.2", "completed": 1024**3, "total": 2 * 1024**3, "status": "pulling"}
    assert pull_text(pull) == "llama3.2: 50% (1.0 GB of 2.0 GB)"
    assert pull_text({"model": "x", "status": "verifying"}) == "x: verifying"
    s = build_status(HEALTHY, "m", pull=pull)
    assert any(r.name == "Download" for r in s.rows)


def test_index_row_variants() -> None:
    assert index_row({"indexing": True}, []).text == "indexing…"
    assert index_row({"files": 3}, []).text == "3 files (keyword search)"
    row = index_row(None, [unit("axon-search", FAIL, "failed")])
    assert row.status == FAIL and row.text == "failed"
    assert index_row(None, [unit("axon-search")]).status == INFO


def test_missing_results_are_unknown_not_ok() -> None:
    s = build_status([], "Model: unknown")
    assert s.status == WARN
    assert all(r.text == "unknown" for r in s.rows if r.name in ("Axon Brain", "Ollama", "Voice"))
