# Security Audit Summary — Axon OS

**Agent:** Debug Agent 3 — Security & Input Validation
**Date:** 2026-07-02
**Project:** `/home/hxshin/projects/Axon-OS`
**Reports:** `01-shell-injection.md`, `02-sandbox-dbus.md`, `03-prompt-injection.md`

---

## Executive Summary

Audited 15+ files across the Axon OS codebase for security vulnerabilities in shell injection, D-Bus authorization, sandbox enforcement, and AI prompt injection. Found **16 vulnerabilities**: 5 HIGH, 7 MEDIUM, 4 LOW. The most critical issues are: fully permissive D-Bus policies allowing any session process to call any service method, indirect prompt injection via unsanitized window titles embedded in AI system prompts, and the absence of safety instructions in the chat system prompt.

---

## Vulnerabilities by Severity

### CRITICAL
_(None found — the attack surface is limited to the user session context)_

---

### HIGH (5 findings)

| # | Vulnerability | File | Report |
|---|--------------|------|--------|
| H1 | **All D-Bus policies fully permissive** — any session process can call any method on any service (Brain, Voice, Sandbox, etc.) | All `services/*/org.axonos.*.conf` | 02-sandbox-dbus.md |
| H2 | **SandboxManager is only a UI prompt** — does not enforce sandboxing; callers must implement bubblewrap wrapping themselves | `services/axon-sandbox/sandbox_manager.py` | 02-sandbox-dbus.md |
| H3 | **Indirect prompt injection via unsanitized window titles** — `_sanitize_context()` only removes null bytes and truncates; doesn't strip injection markers | `services/axon-brain/brain_service.py:70-75` | 03-prompt-injection.md |
| H4 | **`CHAT_SYSTEM_PROMPT` has zero safety instructions** — no boundaries on harmful commands, no protection against context manipulation | `services/axon-brain/prompts.py:3-5` | 03-prompt-injection.md |
| H5 | **`find` in ALLOWED_COMMANDS enables file enumeration** — allows systematic search for private keys, configs, credentials without metacharacters | `services/service_utils.py:22` | 01-shell-injection.md |

#### Attack Chains for HIGH Findings:

**Chain 1 (H1 + H5):** Malicious app → D-Bus call to `Brain.ClassifyIntent("find my SSH keys")` → AI returns `{"action": "run_command", "command": "find /home -name id_rsa"}` → `safe_exec` allows it → file paths leaked

**Chain 2 (H3 + H4):** Attacker names a window with prompt injection → context embedded in AI system prompt → AI follows injected instructions → generates destructive `run_command` → `safe_exec` blocks most, but `find`/`cat`/`ls` pass

**Chain 3 (H1 + H2):** Malicious app → calls `Brain.PullModel("huge-model")` to fill disk (DoS) → calls `Brain.DeleteConversation()` to destroy user data → calls `Brain.GetMessages()` to read all conversations (privacy)

---

### MEDIUM (7 findings)

| # | Vulnerability | File | Report |
|---|--------------|------|--------|
| M1 | **Missing newline/null-byte/tab in `_SHELL_META_CHARS`** — defense-in-depth gap | `services/service_utils.py:64` | 01-shell-injection.md |
| M2 | **`xdg-open`/`gio` in ALLOWED_COMMANDS** — can open arbitrary URIs and make D-Bus calls | `services/service_utils.py:57-59` | 01-shell-injection.md |
| M3 | **`settings_executor.py` bypasses `safe_exec`** — direct subprocess calls, currently safe but fragile | `apps/axon-settings/settings_executor.py` | 01-shell-injection.md |
| M4 | **Bubblewrap sandbox doesn't mask `/proc`/`/sys`** — sandboxed process can read environment variables | `services/axon-sandbox/shield.py:109-149` | 02-sandbox-dbus.md |
| M5 | **polkit install-engine allows `yes` for active sessions** — no auth required | `data/polkit/org.axonos.install-engine.policy:14` | 02-sandbox-dbus.md |
| M6 | **SandboxManager trusts arbitrary file paths** — no path restriction on `AuditAndPrompt` | `services/axon-sandbox/sandbox_manager.py:195` | 02-sandbox-dbus.md |
| M7 | **ClassifyIntent returns unvalidated AI output** — no command filtering at service level | `services/axon-brain/brain_service.py:357-414` | 03-prompt-injection.md |
| M8 | **`_sanitize_context` doesn't strip prompt-injection patterns** | `services/axon-brain/brain_service.py:70-75` | 03-prompt-injection.md |

---

### LOW (4 findings)

| # | Vulnerability | File | Report |
|---|--------------|------|--------|
| L1 | **`safe_exec` defaults to DEVNULL** — no output capture or audit trail | `services/service_utils.py:102-104` | 01-shell-injection.md |
| L2 | **`safety.py` heuristic is weak** — substring matching, missing patterns | `apps/axon-terminal/safety.py:34-52` | 01-shell-injection.md |
| L3 | **D-Bus `${user}` template variable** — substitution depends on correct installer behavior | All `.conf` files | 02-sandbox-dbus.md |
| L4 | **Rate limiter uses fallback `"default"` sender** — shared bucket disables per-caller limits | `services/service_utils.py:268` | 03-prompt-injection.md |

---

## Positive Security Observations

The codebase does have several good security practices:

1. **`safe_exec` uses list-based `subprocess.Popen`** (not `shell=True`) — prevents shell injection even if metacharacter checks are bypassed
2. **`_validate_app_name` in both `intent-bar` and `voice_service`** — regex + length check on AI-generated app names
3. **`_validate_value` in `settings_executor`** — validates AI-generated action strings
4. **Bubblewrap sandbox in `shield.py`** — proper Linux namespace sandboxing with secret directory masking
5. **Rate limiting on critical D-Bus methods** — `Generate`, `SendMessage`, `ClassifyIntent` all have rate limits
6. **Eavesdrop protection** in all D-Bus configs — `<deny eavesdrop="true"/>`
7. **AST-based audit v2** — `audit_v2.py` provides deep command structure analysis for script auditing
8. **Model name validation** — `_validate_model_name` prevents injection in model tag strings
9. **Output sanitization** — `_sanitize_output` strips ANSI escapes and null bytes from AI responses

---

## Top 5 Recommended Fixes (Priority Order)

1. **Add safety instructions to `CHAT_SYSTEM_PROMPT`** — lowest effort, highest impact. Add explicit rules against destructive commands and mark context as untrusted.

2. **Validate/sanitize context before embedding in prompts** — strip prompt injection patterns, wrap context in `<untrusted_context>` tags, and instruct the model to treat it as data only.

3. **Add command validation to `ClassifyIntent`** — filter out dangerous commands at the service level before returning to consumers.

4. **Remove `find` and `gio` from `ALLOWED_COMMANDS`** — eliminate the most exploitable commands from the whitelist.

5. **Restrict D-Bus policies** — add caller-specific restrictions instead of allowing all session processes.

---

## Files Audited

| Category | Files |
|----------|-------|
| Shell injection | `services/service_utils.py`, `apps/axon-terminal/safety.py`, `apps/axon-settings/settings_executor.py`, `apps/intent-bar/ui/window.py` |
| Sandbox/D-Bus | `services/axon-sandbox/sandbox_manager.py`, `shield.py`, `audit.py`, `audit_v2.py`, 6 `.conf` files, 2 `.policy` files |
| Prompt injection | `services/axon-brain/brain_service.py`, `services/axon-brain/prompts.py`, `services/axon-voice/intent_router.py`, `voice_service.py`, `apps/intent-bar/ollama_client.py`, `apps/intent-bar/ui/window.py` |
