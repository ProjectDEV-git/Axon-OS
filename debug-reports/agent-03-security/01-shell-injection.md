# Security Audit: Shell Injection & Command Validation Gaps

**Agent:** Debug Agent 3 — Security & Input Validation
**Date:** 2026-07-02
**Scope:** `services/service_utils.py`, `apps/axon-terminal/safety.py`, `apps/axon-settings/settings_executor.py`, `apps/intent-bar/ui/window.py`

---

## Summary

The `safe_exec()` function in `service_utils.py` implements a whitelist-based command gate with metacharacter blocking. While it prevents obvious shell injection via semicolons, pipes, and backticks, several bypass vectors and defense-in-depth gaps exist. The `ALLOWED_COMMANDS` set includes commands that can be abused for information disclosure. The `settings_executor.py` bypasses `safe_exec` entirely, running hardcoded subprocess calls.

---

## FINDING 1: `find` in ALLOWED_COMMANDS enables file enumeration [HIGH]

**File:** `services/service_utils.py:22`
**Severity:** HIGH

`find` is included in `ALLOWED_COMMANDS`. The `find` command can be used for systematic file enumeration without any shell metacharacters:

```bash
# All of these pass safe_exec checks — no metacharacters, binary is in allowlist
find /home -type f -name "*.pem"
find /home -type f -name "*.conf"
find / -name "id_rsa" -o -name "id_ed25519"
find /home -name "credentials*"
find /etc -type f -name "*.key"
```

**Attack path:** User types "find all my private keys" in Intent Bar → AI generates `{"action": "run_command", "command": "find / -name \"id_rsa\""}` → passes `safe_exec` → enumerates SSH private keys.

**Recommendation:** Remove `find` from `ALLOWED_COMMANDS`, or add argument-level filtering that blocks `-exec`, `-ok`, `-name` patterns matching sensitive files (`.pem`, `.key`, `.ssh`, etc.).

---

## FINDING 2: Missing newline/null-byte/tab in `_SHELL_META_CHARS` [MEDIUM]

**File:** `services/service_utils.py:64`
**Severity:** MEDIUM

```python
_SHELL_META_CHARS = frozenset("|;&$`\\(){}[]<>*?~!#")
```

**Missing characters:** `\n` (newline), `\r` (carriage return), `\t` (tab), `\x00` (null byte)

Currently, `shlex.split()` normalizes newlines/tabs to whitespace, preventing command chaining. However:

1. **Defense-in-depth gap:** If `safe_exec` is ever refactored to use `shell=True` or `os.system()`, the newline bypass immediately enables command chaining: `ls\nrm -rf /`
2. **Null byte truncation:** In some C-backed Python implementations, null bytes can truncate strings at the OS boundary
3. **Tab-based bypasses:** Tabs can sometimes be used to break argument parsing in edge cases

**Recommendation:** Add `\n`, `\r`, `\t`, `\x00` to `_SHELL_META_CHARS`:

```python
_SHELL_META_CHARS = frozenset("|;&$`\\(){}[]<>*?~!#\n\r\t\x00")
```

---

## FINDING 3: `xdg-open` and `gio` in ALLOWED_COMMANDS enable URI-based attacks [MEDIUM]

**File:** `services/service_utils.py:57-59`
**Severity:** MEDIUM

`xdg-open`, `gio`, and `gtk-launch` are in the allowlist. While list-based `Popen` prevents shell expansion in arguments, these commands can open arbitrary URIs:

```bash
# These pass safe_exec — no shell metacharacters needed
xdg-open http://evil.com/phishing
gio open http://evil.com
gio call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
```

`gio call` is particularly dangerous as it can make arbitrary D-Bus method calls, potentially invoking privileged services or reading sensitive data from other D-Bus services.

**Recommendation:** Remove `gio` from the allowlist entirely. For `xdg-open`, consider adding URI scheme allowlisting (only `file://`, `https://`, `mailto:`).

---

## FINDING 4: `settings_executor.py` bypasses `safe_exec` entirely [MEDIUM]

**File:** `apps/axon-settings/settings_executor.py`
**Severity:** MEDIUM (mitigated by hardcoded commands)

`SettingsExecutor` constructs subprocess calls directly without going through `safe_exec`. It trusts AI-generated JSON for the `value` and `setting` fields:

```python
# AI-generated `value` flows directly into subprocess args
subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{vol}%"], check=True)
```

**Why this is currently safe:** All subprocess calls use list format (not `shell=True`), and values are either type-cast (`int(value)`, `float(value)`) or use hardcoded strings.

**Risk:** If any new method in `SettingsExecutor` uses string interpolation into a shell command, the AI-generated `value` or `setting` fields would be directly injectable. The `_validate_value()` method is only applied to the `action` field (line 123), not to `setting` or `value` fields.

**Recommendation:** Apply `_validate_value()` to `setting` parameter as well. Add a comment warning against using `shell=True` in future methods.

---

## FINDING 5: `safe_exec` uses `Popen` without `stdout`/`stderr` capture for `run_command` [LOW]

**File:** `services/service_utils.py:102-104`
**Severity:** LOW

`safe_exec` defaults to `stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL`. When called from `intent-bar` for `run_command`, the user gets no output or error feedback. This is a UX issue but also means:
- Error-based command injection (e.g., commands that produce error messages leaking info) is invisible
- Users can't verify what the command actually did

**Recommendation:** Allow callers to opt into capturing output, or log command results for audit trail.

---

## FINDING 6: `safety.py` heuristic is too weak [LOW]

**File:** `apps/axon-terminal/safety.py:34-52`
**Severity:** LOW

The `DANGEROUS_HINTS` heuristic uses simple substring matching:
- `"curl "` will match `echo "do not curl this"` (false positive)
- Missing patterns: `nc -l`, `python -c`, `perl -e`, `ruby -e`, `node -e`
- No detection of encoding tricks like `$(printf '\x63\x75...')` for `curl`

The fallback is `risk = "medium"` with a generic description. This is acceptable for a UI hint layer but insufficient as a security gate.

**Recommendation:** Add missing dangerous patterns and use regex word-boundary matching to reduce false positives.

---

## FINDING 7: `find` arguments can use `-exec` without metacharacters in some edge cases [LOW]

**File:** `services/service_utils.py:22`
**Severity:** LOW

While `-exec` contains a semicolon (blocked by `_SHELL_META_CHARS`), `find` supports `-exec ... {} +` which uses `+` instead of `;`. However, `-exec` still contains `;`, so the semicolon check catches this. This is a note for completeness.

**Mitigation already in place:** The `;` in `_SHELL_META_CHARS` blocks `-exec`.

---

## Files Reviewed

| File | Lines |
|------|-------|
| `services/service_utils.py` | 1-276 |
| `apps/axon-terminal/safety.py` | 1-89 |
| `apps/axon-settings/settings_executor.py` | 1-494 |
| `apps/intent-bar/ui/window.py` | 180-210, 515-545, 620-645 |
| `services/axon-voice/voice_service.py` | 40-120, 200-350 |
