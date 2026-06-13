"""Pure helpers for Axon Terminal safety checks.

These functions are intentionally small and unit-testable so the UI can ask
whether a command should be run directly, allowed once, or sandboxed.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    import audit  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    audit = None


@dataclass(frozen=True)
class SafetyDecision:
    risk: str
    findings: list[dict]
    sandbox_recommended: bool


def format_findings(findings: list[dict], limit: int = 8) -> str:
    lines = [
        f"• line {f.get('line', '?')} [{str(f.get('severity', 'low')).upper()}] {f.get('description', '')}"
        for f in findings[:limit]
    ]
    if len(findings) > limit:
        lines.append(f"… and {len(findings) - limit} more findings")
    return "\n".join(lines) if lines else "No specific issues were identified."


DANGEROUS_HINTS = (
    "curl ",
    "wget ",
    "| sh",
    "| bash",
    "rm -rf",
    "chmod 777",
    "sudo ",
)


def assess_command(command: str) -> SafetyDecision:
    """Return a lightweight safety verdict for a shell command.

    If the optional sandbox audit helper is available, use it; otherwise fall
    back to a tiny heuristic so the UI can still prompt the user.
    """
    findings: list[dict] = []
    risk = "none"

    if audit is not None:
        try:
            findings = audit.analyze_script(command)
            risk = audit.risk_level(findings)
        except Exception:
            findings = []
            risk = "none"

    if risk == "none":
        lowered = command.lower()
        if any(hint in lowered for hint in DANGEROUS_HINTS):
            risk = "medium"
            findings = [{
                "line": 1,
                "severity": "medium",
                "description": "Suspicious shell pattern",
                "snippet": command[:160],
            }]

    return SafetyDecision(
        risk=risk,
        findings=findings,
        sandbox_recommended=risk in {"medium", "high"},
    )

