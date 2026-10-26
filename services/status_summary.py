"""Turn diagnostics results into the short Axon status shown in the AI panel.

Reuses :mod:`diagnostics` checks (``services`` and ``ai``) plus what the
Brain and Search services already report, so no new health checks exist
here. Pure functions only: the caller gathers the data off the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from diagnostics import FAIL, INFO, OK, WARN, CheckResult
from i18n import _

_RANK = {OK: 0, INFO: 0, WARN: 1, FAIL: 2}


@dataclass
class StatusRow:
    """One line of the status section."""

    name: str
    status: str
    text: str


@dataclass
class StatusSummary:
    """Everything the status section shows."""

    status: str
    headline: str
    rows: list[StatusRow] = field(default_factory=list)
    problem: CheckResult | None = None


def _find(results: list[CheckResult], name: str) -> CheckResult | None:
    return next((r for r in results if r.name == name), None)


def _unit_row(results: list[CheckResult], unit: str, label: str) -> StatusRow:
    result = _find(results, f"{unit}.service")
    if result is None:
        return StatusRow(label, WARN, _("unknown"))
    return StatusRow(label, result.status, result.summary)


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def pull_text(pull: dict[str, Any]) -> str:
    """e.g. "llama3.2: 45% (0.9 GB of 2.0 GB)" or "llama3.2: verifying"."""
    name = str(pull.get("model") or "model")
    done, total = int(pull.get("completed") or 0), int(pull.get("total") or 0)
    if total > 0:
        pct = min(100, round(done * 100 / total))
        return _("{name}: {pct}% ({done} of {total})").format(
            name=name, pct=pct, done=_human(done), total=_human(total)
        )
    return f"{name}: {pull.get('status') or _('starting')}"


def index_row(search_stats: dict[str, Any] | None, results: list[CheckResult]) -> StatusRow:
    """Semantic file index row from Search ``GetStats`` (or its unit state)."""
    if search_stats is None:
        unit = _unit_row(results, "axon-search", _("Semantic index"))
        return unit if unit.status != OK else StatusRow(_("Semantic index"), INFO, _("no details"))
    if search_stats.get("indexing"):
        return StatusRow(_("Semantic index"), INFO, _("indexing…"))
    files = int(search_stats.get("files") or 0)
    if search_stats.get("vector_backend"):
        detail = _("{files} files (semantic search)").format(files=f"{files:,}")
    else:
        detail = _("{files} files (keyword search)").format(files=f"{files:,}")
    return StatusRow(_("Semantic index"), OK, detail)


def build_status(
    results: list[CheckResult],
    model_label: str,
    search_stats: dict[str, Any] | None = None,
    pull: dict[str, Any] | None = None,
) -> StatusSummary:
    """Summarise Brain, Ollama, model, downloads, index and voice.

    Args:
        results: ``run_diagnostics(only=["services", "ai"]).results``.
        model_label: The local/cloud indicator text, e.g. "Local · llama3.2:3b".
        search_stats: Search ``GetStats`` JSON, if the service answered.
        pull: Latest Brain ``PullProgress`` as ``{model, completed, total, status}``.
    """
    ollama = _find(results, "Ollama")
    rows = [
        _unit_row(results, "axon-brain", _("Axon Brain")),
        StatusRow("Ollama", ollama.status, ollama.summary)
        if ollama
        else StatusRow("Ollama", WARN, _("unknown")),
        StatusRow(_("Model"), INFO, model_label),
    ]
    if pull:
        rows.append(StatusRow(_("Download"), INFO, pull_text(pull)))
    rows.append(index_row(search_stats, results))
    rows.append(_unit_row(results, "axon-voice", _("Voice")))

    problems = sorted(
        (r for r in results if r.status in (WARN, FAIL)), key=lambda r: -_RANK[r.status]
    )
    problem = problems[0] if problems else None
    worst = max((_RANK[r.status] for r in rows), default=0)
    if problem is not None:
        worst = max(worst, _RANK[problem.status])
    status = {0: OK, 1: WARN, 2: FAIL}[worst]
    if problem is None and status == OK:
        headline = _("All good")
    elif problem is not None:
        headline = f"{problem.name}: {problem.summary}"
    else:
        headline = _("Needs attention")
    return StatusSummary(status, headline, rows, problem)
