#!/usr/bin/env python3
"""Axon Rogue Software Shield — audit + sandbox untrusted scripts/binaries.

Usage:
    axon-shield [--no-net] [--yes-sandbox] <script-or-binary> [args...]

Flow:
 1. Static audit (audit.py regex rules) of the target script.
 2. Optional AI audit through org.axonos.Brain (skipped when offline).
 3. If anything suspicious is found the user chooses, via a desktop dialog
    (zenity) or terminal prompt: run sandboxed, allow once, or block.
 4. Sandboxed runs use bubblewrap: read-only home, secrets masked out,
    writable /tmp, optional network blackout (--no-net).

Exit codes: target's own code; 125 = blocked; 126 = cannot execute.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_v2 as audit

AI_AUDIT_TIMEOUT = 25  # seconds; the shield never hard-blocks on the AI


def read_target(path: str) -> str | None:
    """Script text, or None for binaries / unreadable files."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if raw[:4] == b"\x7fELF" or b"\x00" in raw[:1024]:
        return None
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def ai_audit(script_text: str) -> str:
    """One-paragraph AI verdict via the Brain service; "" when unavailable."""
    try:
        import dbus
        bus = dbus.SessionBus()
        obj = bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
        brain = dbus.Interface(obj, "org.axonos.Brain")
        prompt = (
            "Audit this shell script for malicious or destructive behavior "
            "(credential theft, data exfiltration, destructive deletes, "
            "persistence, obfuscated payloads). Reply with one short "
            "paragraph starting with SAFE: or SUSPICIOUS:.\n\n"
            + script_text[:6000]
        )
        return str(brain.Generate(prompt, "", "", False,
                                  timeout=AI_AUDIT_TIMEOUT)).strip()
    except Exception:
        return ""


def ask_user(target: str, findings: list, ai_verdict: str) -> str:
    """Returns one of: sandbox | allow | block."""
    body = (f"Suspicious behavior detected in:\n{target}\n\n"
            + audit.format_findings_compat(findings))
    if ai_verdict:
        body += f"\n\nAI audit: {ai_verdict[:300]}"

    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        if shutil.which("zenity"):
            proc = subprocess.run(
                ["zenity", "--question", "--title=Axon Rogue Shield",
                 "--icon=security-high",
                 f"--text={body}",
                 "--ok-label=Run Sandboxed (read-only home)",
                 "--cancel-label=Block Execution",
                 "--extra-button=Allow Once (unrestricted)",
                 "--width=520"],
                capture_output=True, text=True,
            )
            if proc.stdout.strip().startswith("Allow Once"):
                return "allow"
            return "sandbox" if proc.returncode == 0 else "block"

    # Terminal fallback
    print("\n⚠  Axon Rogue Shield — suspicious script behavior detected")  # noqa: T201
    print(body)  # noqa: T201
    try:
        choice = input("\n[S]andbox / [a]llow once / [b]lock (default S): ")
    except EOFError:
        choice = ""
    choice = choice.strip().lower()
    if choice.startswith("a"):
        return "allow"
    if choice.startswith("b"):
        return "block"
    return "sandbox"


def sandbox_command(target_cmd: list, no_net: bool) -> list:
    """Wrap *target_cmd* in a bubblewrap jail.

    Read-only / and home, secrets directories masked with empty tmpfs,
    writable /tmp and current directory untouched (also read-only).
    """
    home = str(Path.home())
    cmd = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--ro-bind", home, home,
        "--die-with-parent",
        "--new-session",
    ]
    for secret in (".ssh", ".gnupg", ".axon", ".mozilla",
                   ".config/google-chrome", ".config/chromium",
                   ".local/share/keyrings"):
        p = Path(home) / secret
        if p.exists():
            cmd += ["--tmpfs", str(p)]
    if no_net:
        cmd += ["--unshare-net"]
    return [*cmd, "--", *target_cmd]


def main(argv: list) -> int:
    no_net = False
    force_sandbox = False
    args = list(argv)
    while args and args[0] in ("--no-net", "--yes-sandbox"):
        flag = args.pop(0)
        no_net = no_net or flag == "--no-net"
        force_sandbox = force_sandbox or flag == "--yes-sandbox"
    if not args:
        print(__doc__)  # noqa: T201
        return 126

    target, target_args = args[0], args[1:]
    target_path = shutil.which(target) or target
    if not Path(target_path).exists():
        print(f"axon-shield: no such file: {target}")  # noqa: T201
        return 126

    text = read_target(target_path)
    if text is None:
        # Binary: no static patterns to match — treat as suspicious-by-default.
        findings = [{"line": 0, "severity": "medium",
                     "description": "Unverified native binary (no source to audit)",
                     "snippet": Path(target_path).name}]
        ai_verdict = ""
    else:
        findings = audit.analyze_script_compat(text)
        ai_verdict = ai_audit(text) if findings else ""

    risk = audit.risk_level_compat(findings)
    interpreter: list = []
    if text is not None and not os.access(target_path, os.X_OK):
        interpreter = ["bash"]
    target_cmd = [*interpreter, target_path, *target_args]

    if risk == "none" and not force_sandbox:
        return subprocess.call(target_cmd)

    decision = "sandbox" if force_sandbox else ask_user(
        target_path, findings, ai_verdict)
    if decision == "block":
        print("axon-shield: execution blocked.")  # noqa: T201
        return 125
    if decision == "allow":
        return subprocess.call(target_cmd)

    if not shutil.which("bwrap"):
        print("axon-shield: bubblewrap not installed; refusing unsandboxed run.")  # noqa: T201
        return 126
    print(json.dumps({"axon-shield": "sandboxed", "risk": risk,  # noqa: T201
                      "network": "blocked" if no_net else "allowed"}))
    return subprocess.call(sandbox_command(target_cmd, no_net))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
