"""Static analysis of shell scripts / commands for rogue behavior.

Pure stdlib + regex so it is fast and unit-testable. The AI audit in
shield.py builds on top of these findings; this module alone must already
catch the classic smash-and-grab patterns.
"""

from __future__ import annotations

import re

# (compiled regex, severity, human description)
# severity: "high" — touches secrets / destroys data / persists silently
#           "medium" — exfiltration-capable or system-level writes
#           "low" — worth a mention
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\.ssh/(id_[a-z0-9]+|authorized_keys|known_hosts)"),
        "high",
        "Reads or writes SSH keys (~/.ssh)",
    ),
    (re.compile(r"\.(gnupg|pki)/"), "high", "Accesses GPG / PKI key material"),
    (
        re.compile(r"\.(mozilla|thunderbird|config/(google-)?chrom\w+|config/BraveSoftware)\b"),
        "high",
        "Reads browser/mail profile data (cookies, passwords, history)",
    ),
    (
        re.compile(r"\b(cookies|logins|signons)\.sqlite\b"),
        "high",
        "Targets browser credential databases",
    ),
    (
        re.compile(
            r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[a-zA-Z]*\s+[\"']?"
            r"(/|\$HOME|~|/home(/[^/\s\"']+)?/?)(\s|[\"']|$)"
        ),
        "high",
        "Recursively force-deletes / or the home directory",
    ),
    (
        re.compile(r"\bdd\s+[^|\n]*of=/dev/(sd|nvme|vd|mmcblk)"),
        "high",
        "Writes raw data directly to a disk device",
    ),
    (re.compile(r"\bmkfs\.\w+"), "medium", "Formats a filesystem"),
    (
        re.compile(r"\bchmod\s+[0-7]*[42]7{2,3}\b|\bchmod\s+\+s\b"),
        "medium",
        "Sets world-writable or setuid permissions",
    ),
    (
        re.compile(
            r"(curl|wget)[^|\n]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"
            r"|(curl|wget)[^|\n]*\|\s*(sudo\s+)?(python[0-9.]*|perl|ruby|node)\b"
        ),
        "high",
        "Pipes a remote download straight into a shell or interpreter",
    ),
    (
        re.compile(r"\b(ba|z|da)?sh\s+(-c\s+)?[\"']?(<\(|\$\()\s*(curl|wget)\b"),
        "high",
        "Runs a remote download as a script (process/command substitution)",
    ),
    (
        re.compile(r"\.(aws/credentials|kube/config|docker/config\.json|netrc|git-credentials)\b"),
        "high",
        "Reads cloud, container or git credentials",
    ),
    (
        re.compile(
            r"\b(curl|wget)\b[^\n]*(?<!\S)(-d|--data\S*|--post-data|--post-file|-F|--form"
            r"|--upload-file|-T)(?=\s|=|@|$)"
            r"|\|\s*(nc|ncat|socat|telnet)\b"
        ),
        "medium",
        "Uploads data to a remote host",
    ),
    (
        re.compile(r"\bbase64\s+(-d|--decode)[^\n]*\|\s*(ba)?sh\b"),
        "high",
        "Decodes hidden base64 payload into a shell",
    ),
    (re.compile(r"\beval\s+[\"']?\$\("), "medium", "eval of dynamically generated code"),
    (
        re.compile(r"(\bcrontab\s+-|/etc/cron|\bsystemctl\s+enable|/etc/systemd/system/)"),
        "medium",
        "Installs persistent startup hooks (cron/systemd)",
    ),
    (
        re.compile(r">>?\s*(~|\$HOME)?/\.(bashrc|profile|bash_profile|zshrc)\b"),
        "medium",
        "Appends to shell startup files",
    ),
    (
        re.compile(r"/etc/(passwd|shadow|sudoers)\b"),
        "high",
        "Touches system account/sudo databases",
    ),
    (
        re.compile(r"\bhistory\s+-c\b|\bunset\s+HISTFILE\b|\bshred\b"),
        "medium",
        "Covers its tracks (clears history / shreds files)",
    ),
    (
        re.compile(r"\b(xmrig|minerd|stratum\+tcp)\b", re.IGNORECASE),
        "high",
        "Cryptocurrency miner indicators",
    ),
    (
        re.compile(r"/dev/tcp/\d"),
        "medium",
        "Opens raw TCP connections via /dev/tcp (reverse-shell idiom)",
    ),
    (re.compile(r"\b(bash|sh)\s+-i\s+>&\s*/dev/tcp/"), "high", "Classic reverse shell one-liner"),
    (
        re.compile(
            r"\bsudo\s+",
        ),
        "low",
        "Requests root privileges",
    ),
]

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def analyze_script(text: str) -> list[dict]:
    """Return findings: [{line, severity, description, snippet}], deduped."""
    findings: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pattern, severity, description in _PATTERNS:
            if pattern.search(line) and (lineno, description) not in seen:
                seen.add((lineno, description))
                findings.append(
                    {
                        "line": lineno,
                        "severity": severity,
                        "description": description,
                        "snippet": stripped[:160],
                    }
                )
    findings.sort(key=lambda f: (-_SEVERITY_RANK[f["severity"]], f["line"]))
    return findings


def risk_level(findings: list[dict]) -> str:
    """Overall verdict: "high", "medium", "low" or "none"."""
    best = 0
    for f in findings:
        best = max(best, _SEVERITY_RANK.get(f["severity"], 0))
    return {0: "none", 1: "low", 2: "medium", 3: "high"}[best]


def format_findings(findings: list[dict], limit: int = 8) -> str:
    """Human-readable bullet list for dialogs and terminal output."""
    lines = [
        f"• line {f['line']} [{f['severity'].upper()}] {f['description']}" for f in findings[:limit]
    ]
    if len(findings) > limit:
        lines.append(f"… and {len(findings) - limit} more findings")
    return "\n".join(lines)
