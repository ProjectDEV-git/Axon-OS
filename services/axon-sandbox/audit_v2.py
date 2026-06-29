"""AST-based static analysis of shell scripts for rogue behavior.

Provides deeper analysis than regex-only audit.py by parsing scripts into
command structures and detecting obfuscation patterns. Falls back to
regex-based audit.py for patterns that don't need AST analysis.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

# Import regex-based patterns from v1 for fast first-pass
import audit as audit_v1


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}[self]


@dataclass
class CommandNode:
    """Represents a parsed shell command."""

    name: str
    args: list[str]
    lineno: int
    raw: str
    pipe_next: CommandNode | None = None
    redirect_to: str | None = None
    background: bool = False


@dataclass
class Finding:
    """A security finding from AST analysis."""

    line: int
    severity: Severity
    description: str
    snippet: str
    source: str = "ast"  # "ast" or "regex"


# ---------------------------------------------------------------------------
# AST-level threat rules
# ---------------------------------------------------------------------------


@dataclass
class ThreatRule:
    """A rule for matching against parsed command ASTs."""

    name: str
    severity: Severity
    description: str
    match_fn: Callable[[CommandNode], bool]


def _is_dangerous_rm(node: CommandNode) -> bool:
    """Detect rm -rf / or rm -rf ~ patterns."""
    if node.name != "rm":
        return False
    args_str = " ".join(node.args)
    has_recursive = "-r" in args_str or "-R" in args_str
    has_force = "-f" in args_str
    # Also check combined flags like -rf, -fr
    has_combined = bool(re.search(r"-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r", args_str))
    if not (has_recursive and (has_force or has_combined)):
        return False
    for arg in node.args:
        if arg in ("/", "~", "$HOME", "${HOME}"):
            return True
        if arg.startswith("/") and len(arg) <= 2:
            return True
    return False


def _is_curl_pipe_sh(node: CommandNode) -> bool:
    """Detect curl/wget | sh/bash patterns."""
    if node.name not in ("curl", "wget"):
        return False
    if node.pipe_next and node.pipe_next.name in ("sh", "bash", "sudo"):
        return True
    # Check for piped to shell in args (e.g., curl ... | sh)
    return False


def _is_base64_decode_pipe(node: CommandNode) -> bool:
    """Detect base64 -d | sh patterns."""
    if node.name != "base64":
        return False
    has_decode = any(a in ("-d", "--decode") for a in node.args)
    if not has_decode:
        return False
    if node.pipe_next and node.pipe_next.name in ("sh", "bash"):
        return True
    return False


def _is_reverse_shell(node: CommandNode) -> bool:
    """Detect bash -i >& /dev/tcp/... patterns."""
    if node.name not in ("bash", "sh"):
        return False
    args_str = " ".join(node.args)
    return "-i" in args_str and "/dev/tcp/" in args_str


def _is_dd_to_disk(node: CommandNode) -> bool:
    """Detect dd of=/dev/sdX patterns."""
    if node.name != "dd":
        return False
    for arg in node.args:
        if arg.startswith("of=/dev/") and any(d in arg for d in ("sd", "nvme", "vd", "mmcblk")):
            return True
    return False


def _is_mkfs(node: CommandNode) -> bool:
    """Detect mkfs.* commands."""
    return node.name.startswith("mkfs.")


def _is_cryptominer(node: CommandNode) -> bool:
    """Detect cryptocurrency miner indicators."""
    miners = {"xmrig", "minerd", "cpuminer", "cgminer", "bfgminer", "ethminer"}
    if node.name in miners:
        return True
    args_str = " ".join(node.args)
    return "stratum+tcp" in args_str or "stratum+ssl" in args_str


def _is_eval_dynamic(node: CommandNode) -> bool:
    """Detect eval of dynamically generated code."""
    if node.name != "eval":
        return False
    args_str = " ".join(node.args)
    return "$(" in args_str or "`" in args_str


def _is_history_clear(node: CommandNode) -> bool:
    """Detect history clearing / evidence destruction."""
    if node.name == "history" and "-c" in node.args:
        return True
    if node.name == "unset" and "HISTFILE" in node.args:
        return True
    if node.name == "shred":
        return True
    return False


def _is_sudo_command(node: CommandNode) -> bool:
    """Detect sudo usage."""
    return node.name == "sudo"


def _is_raw_tcp(node: CommandNode) -> bool:
    """Detect /dev/tcp reverse shell idiom."""
    args_str = " ".join(node.args)
    return "/dev/tcp/" in args_str


def _is_persistence_install(node: CommandNode) -> bool:
    """Detect cron/systemd persistence hooks."""
    if node.name == "crontab" and "-e" in node.args:
        return True
    if node.name == "systemctl" and "enable" in node.args:
        return True
    args_str = " ".join(node.args)
    return "/etc/cron" in args_str or "/etc/systemd/system/" in args_str


# Threat rules database
_THREAT_RULES: list[ThreatRule] = [
    ThreatRule(
        "dangerous_rm",
        Severity.HIGH,
        "Recursively force-deletes / or the home directory",
        _is_dangerous_rm,
    ),
    ThreatRule(
        "curl_pipe_sh",
        Severity.HIGH,
        "Pipes a remote download straight into a shell",
        _is_curl_pipe_sh,
    ),
    ThreatRule(
        "base64_decode_pipe",
        Severity.HIGH,
        "Decodes hidden base64 payload into a shell",
        _is_base64_decode_pipe,
    ),
    ThreatRule(
        "reverse_shell", Severity.HIGH, "Classic reverse shell one-liner", _is_reverse_shell
    ),
    ThreatRule(
        "dd_to_disk", Severity.HIGH, "Writes raw data directly to a disk device", _is_dd_to_disk
    ),
    ThreatRule("cryptominer", Severity.HIGH, "Cryptocurrency miner indicators", _is_cryptominer),
    ThreatRule(
        "eval_dynamic", Severity.MEDIUM, "eval of dynamically generated code", _is_eval_dynamic
    ),
    ThreatRule("mkfs", Severity.MEDIUM, "Formats a filesystem", _is_mkfs),
    ThreatRule(
        "persistence",
        Severity.MEDIUM,
        "Installs persistent startup hooks (cron/systemd)",
        _is_persistence_install,
    ),
    ThreatRule(
        "raw_tcp",
        Severity.MEDIUM,
        "Opens raw TCP connections via /dev/tcp (reverse-shell idiom)",
        _is_raw_tcp,
    ),
    ThreatRule(
        "history_clear",
        Severity.MEDIUM,
        "Covers its tracks (clears history / shreds files)",
        _is_history_clear,
    ),
    ThreatRule("sudo", Severity.LOW, "Requests root privileges", _is_sudo_command),
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _tokenize_line(line: str) -> list[str]:
    """Safely tokenize a shell command line."""
    try:
        return shlex.split(line)
    except ValueError:
        # Unclosed quotes, etc. — split on whitespace as fallback
        return line.split()


def _parse_pipeline(line: str, lineno: int, raw: str) -> CommandNode | None:
    """Parse a simple pipeline (cmd1 | cmd2 | cmd3) into linked CommandNodes."""
    # Split on pipe, but respect quotes
    parts = _split_on_pipe(line)
    if not parts:
        return None

    nodes: list[CommandNode] = []
    for part in parts:
        tokens = _tokenize_line(part.strip())
        if not tokens:
            continue

        # Extract command name (skip env vars like VAR=val)
        cmd_name = ""
        cmd_args = []
        for i, tok in enumerate(tokens):
            if "=" in tok and not tok.startswith("-") and i == 0:
                continue  # Skip env var assignments
            if tok.startswith("-"):
                cmd_args.append(tok)
            elif not cmd_name:
                cmd_name = tok
            else:
                cmd_args.append(tok)

        if not cmd_name:
            continue

        # Check for redirects
        redirect_to = None
        clean_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] in (">", ">>") and i + 1 < len(cmd_args):
                redirect_to = cmd_args[i + 1]
                i += 2
            elif cmd_args[i].startswith(">"):
                redirect_to = cmd_args[i][1:]
                i += 1
            else:
                clean_args.append(cmd_args[i])
                i += 1

        nodes.append(
            CommandNode(
                name=cmd_name,
                args=clean_args,
                lineno=lineno,
                raw=raw.strip(),
                redirect_to=redirect_to,
            )
        )

    # Link pipeline nodes
    for i in range(len(nodes) - 1):
        nodes[i].pipe_next = nodes[i + 1]

    return nodes[0] if nodes else None


def _split_on_pipe(line: str) -> list[str]:
    """Split a line on pipe characters, respecting quotes."""
    parts = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == "|" and not in_single and not in_double:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append("".join(current))
    return parts


# ---------------------------------------------------------------------------
# Obfuscation detection
# ---------------------------------------------------------------------------

_OBFUSCATION_PATTERNS = [
    (re.compile(r"\$\{[A-Z]+:(\d+):(\d+)\}"), "Substring extraction obfuscation"),
    (re.compile(r"\$\(printf\s+['\"]\\x[0-9a-f]+"), "Hex escape obfuscation via printf"),
    (
        re.compile(r"\\x[0-9a-fA-F]{2}.*\\x[0-9a-fA-F]{2}.*\\x[0-9a-fA-F]{2}"),
        "Multiple hex escapes (obfuscation attempt)",
    ),
    (
        re.compile(r"\$\(echo\s+[A-Za-z0-9+/=]{20,}\s*\|\s*base64\s+-d\)"),
        "Base64 encoded command injection",
    ),
    (re.compile(r"eval\s+\$\(.*\$\(.*\$\("), "Nested eval/subshell obfuscation"),
    (re.compile(r"\$\{IFS\}"), "IFS manipulation (obfuscation technique)"),
    (re.compile(r"\$'\\x[0-9a-fA-F]+'"), "ANSI-C quoting obfuscation"),
]


def _detect_obfuscation(text: str) -> list[Finding]:
    """Detect obfuscation patterns that bypass simple regex."""
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pattern, desc in _OBFUSCATION_PATTERNS:
            if pattern.search(line):
                findings.append(
                    Finding(
                        line=lineno,
                        severity=Severity.HIGH,
                        description=f"Obfuscation detected: {desc}",
                        snippet=stripped[:160],
                        source="ast",
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def analyze_script_ast(text: str) -> list[Finding]:
    """Deep analysis using AST parsing + regex fast-pass.

    Combines:
    1. Regex fast-pass from audit_v1 for simple patterns
    2. AST parsing for command-level analysis
    3. Obfuscation detection for evasion attempts
    """
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()

    # Phase 1: Regex fast-pass (from v1)
    for f in audit_v1.analyze_script(text):
        key = (f["line"], f["description"])
        if key not in seen:
            seen.add(key)
            findings.append(
                Finding(
                    line=f["line"],
                    severity=Severity(f["severity"]),
                    description=f["description"],
                    snippet=f["snippet"],
                    source="regex",
                )
            )

    # Phase 2: AST analysis
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Parse into command AST
        node = _parse_pipeline(stripped, lineno, stripped)
        if not node:
            continue

        # Walk the pipeline and check each node
        current: CommandNode | None = node
        while current:
            for rule in _THREAT_RULES:
                try:
                    if rule.match_fn(current):
                        key = (lineno, rule.description)
                        if key not in seen:
                            seen.add(key)
                            findings.append(
                                Finding(
                                    line=lineno,
                                    severity=rule.severity,
                                    description=rule.description,
                                    snippet=stripped[:160],
                                    source="ast",
                                )
                            )
                except Exception:
                    pass  # Don't let rule errors break analysis
            current = current.pipe_next

    # Phase 3: Obfuscation detection
    for finding in _detect_obfuscation(text):
        key = (finding.line, finding.description)
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    # Sort by severity (high first), then by line number
    findings.sort(key=lambda f: (-f.severity.rank, f.line))
    return findings


def risk_level(findings: list[Finding]) -> str:
    """Overall verdict: "high", "medium", "low" or "none"."""
    best = 0
    for f in findings:
        best = max(best, f.severity.rank)
    return {0: "none", 1: "low", 2: "medium", 3: "high"}[best]


def format_findings(findings: list[Finding], limit: int = 8) -> str:
    """Human-readable bullet list for dialogs and terminal output."""
    lines = [
        f"• line {f.line} [{f.severity.value.upper()}] {f.description}" for f in findings[:limit]
    ]
    if len(findings) > limit:
        lines.append(f"… and {len(findings) - limit} more findings")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backward-compatible API (matches audit.py interface)
# ---------------------------------------------------------------------------


def analyze_script_compat(text: str) -> list[dict]:
    """Return findings as dicts for backward compatibility with shield.py."""
    findings = analyze_script_ast(text)
    return [
        {
            "line": f.line,
            "severity": f.severity.value,
            "description": f.description,
            "snippet": f.snippet,
        }
        for f in findings
    ]


def risk_level_compat(findings: list[dict]) -> str:
    """Backward-compatible risk_level that accepts dict findings."""
    best = 0
    for f in findings:
        rank = {"low": 1, "medium": 2, "high": 3}.get(f.get("severity", ""), 0)
        best = max(best, rank)
    return {0: "none", 1: "low", 2: "medium", 3: "high"}[best]


def format_findings_compat(findings: list[dict], limit: int = 8) -> str:
    """Backward-compatible format_findings that accepts dict findings."""
    lines = [
        f"• line {f['line']} [{f['severity'].upper()}] {f['description']}" for f in findings[:limit]
    ]
    if len(findings) > limit:
        lines.append(f"… and {len(findings) - limit} more findings")
    return "\n".join(lines)
