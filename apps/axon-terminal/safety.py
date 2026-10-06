"""Pure helpers for Axon Terminal safety checks.

These functions are intentionally small and unit-testable so the UI can ask
whether a command should be run directly, allowed once, or sandboxed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


def _import_audit():
    """Import the sandbox's static auditor, which lives in services/axon-sandbox.

    The services directory sits next to apps/ both in the repo and in the
    image (/usr/lib/axon), and under ~/.local/share/axon-os for install.sh.
    """
    candidates = (
        Path(__file__).resolve().parents[2] / "services" / "axon-sandbox",
        Path.home() / ".local" / "share" / "axon-os" / "services" / "axon-sandbox",
    )
    for directory in candidates:
        if (directory / "audit.py").is_file():
            if str(directory) not in sys.path:
                sys.path.append(str(directory))
            break
    try:
        import audit as audit_module  # type: ignore
    except Exception:  # pragma: no cover - optional runtime dependency
        return None
    return audit_module


audit = _import_audit()


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
    "| python",
    "| perl",
    "| ruby",
    "rm -rf",
    "rm -r -f",
    "rm -fr",
    "chmod 777",
    "chmod +s",
    "sudo ",
    "doas ",
    "mkfs.",
    "dd if=",
    "> /dev/sd",
)


def is_insertable_suggestion(command: str) -> bool:
    """True if an AI suggestion is a single plain line safe to type at the prompt.

    Newlines would submit the command (or several), and control characters
    can drive the terminal, so such suggestions are dropped.
    """
    if not command or not command.strip():
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in command)


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
            findings = [
                {
                    "line": 1,
                    "severity": "medium",
                    "description": "Suspicious shell pattern",
                    "snippet": command[:160],
                }
            ]

    return SafetyDecision(
        risk=risk,
        findings=findings,
        sandbox_recommended=risk in {"medium", "high"},
    )
