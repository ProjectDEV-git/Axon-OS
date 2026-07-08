# Security Fix Report — Axon OS

**Agent:** Debug Agent 3 — Security & Input Validation
**Date:** 2026-07-02
**Project:** Axon OS (`services/`)
**Audit Reference:** `debug-reports/agent-03-security/SUMMARY.md`

---

## Summary of Changes

Applied 5 security fixes targeting the highest-priority findings from the audit
(SUMMARY.md, findings H1, H3, H4, H5, M1, M2).

---

## FIX 1: Add safety instructions to CHAT_SYSTEM_PROMPT

**File:** `services/axon-brain/prompts.py`
**Audit Finding:** H4 — `CHAT_SYSTEM_PROMPT` has zero safety instructions

### What changed

Added 5 mandatory safety rules to the system prompt:

1. **Destructive commands blocked:** rm -rf, dd, mkfs, chmod 777, wget|sh, curl|sh, fork bombs
2. **Credential extraction blocked:** /etc/shadow, /etc/passwd
3. **Network servers blocked:** No listeners or servers without explicit user consent
4. **System modification blocked:** Bootloader, kernel, and partition changes
5. **Untrusted context isolation:** `<untrusted_context>` content treated as data only, never followed

### Risk assessment

Low risk. System prompt additions constrain model behavior without affecting
legitimate use cases. The prompt is shorter and clearer than the previous version.

---

## FIX 2: Enhance `_sanitize_context()` with injection pattern stripping

**File:** `services/axon-brain/brain_service.py` (lines 60-90)
**Audit Findings:** H3 — Indirect prompt injection via unsanitized window titles; M8 — `_sanitize_context` doesn't strip injection patterns

### What changed

1. **Reduced max context length** from 2000 to 500 chars (more conservative truncation)
2. **Added `_INJECTION_PATTERNS` regex** matching common prompt injection phrases:
   - `ignore previous`, `ignore all previous`
   - `you are now`, `system:`, `assistant:`, `IMPORTANT:`
   - `disregard.*instructions`, `new instructions`
   - `override.*system`, `forget everything`
3. **Pattern stripping** — matched injection patterns are replaced with empty string
4. **Tag wrapping** — output wrapped in `<untrusted_context>...</untrusted_context>` tags
5. **Updated docstring** to document all sanitization steps

### Risk assessment

Medium-low. The 500-char limit is aggressive but sufficient for desktop context
(window titles, app names). The regex is case-insensitive. Patterns are stripped
silently (no error), which is the correct behavior since stripped content would
only be injection attempts.

### Known limitations

- Pattern set is not exhaustive; novel injection patterns will not be caught
- Application-level caller verification (see FIX 5) provides additional defense-in-depth

---

## FIX 3: Remove `find` and `gio` from ALLOWED_COMMANDS

**File:** `services/service_utils.py` (lines 18-64)
**Audit Findings:** H5 — `find` enables systematic file enumeration; M2 — `gio` can make arbitrary D-Bus calls

### What changed

1. Removed `find` from `ALLOWED_COMMANDS` — prevents AI-generated `find /home -name id_rsa` style commands from passing `safe_exec`
2. Removed `gio` from `ALLOWED_COMMANDS` — prevents arbitrary D-Bus call proxying
3. Added inline comments documenting why each was removed with audit reference

### Risk assessment

Low. `find` and `gio` are low-utility commands for the AI assistant's primary
use cases (opening apps, checking system status). Users who need `find` can use
`ls` + `grep` or the file manager directly.

### Attack chain mitigated

- **Chain 1 (H1+H5):** Malicious app -> `Brain.ClassifyIntent("find my SSH keys")` -> AI returns `find /home -name id_rsa` -> **now blocked by safe_exec allowlist**

---

## FIX 4: Add missing meta chars to `_SHELL_META_CHARS`

**File:** `services/service_utils.py` (line 66)
**Audit Finding:** M1 — Missing newline/null-byte/tab in `_SHELL_META_CHARS`

### What changed

Added three characters to the `_SHELL_META_CHARS` frozenset:
- `\n` (newline) — could bypass command parsing boundaries
- `\t` (tab) — could be used for command concatenation/obfuscation
- `\x00` (null byte) — could truncate command strings at the OS level

### Risk assessment

Very low. Defense-in-depth addition. These characters in a command string are
almost never legitimate and would typically indicate injection attempts.

---

## FIX 5: D-Bus policy restrictions

**Files:** All 6 `services/*/org.axonos.*.conf` files
**Audit Finding:** H1 — All D-Bus policies fully permissive

### What changed

Replaced the blanket `<allow send_destination="..."/>` in each service's default
policy with **method-level allow/deny rules**:

| Service | Methods open to all | Methods restricted |
|---------|-------------------|-------------------|
| **Brain** | GetStatus, ListModels, ListConversations, GetMessages, GetEmbeddings | SendMessage, ClassifyIntent, Generate, ClassifyWindow, PullModel, CreateConversation, AddMessage, UpdateTitle, DeleteConversation |
| **Voice** | GetStatus | StartListening, StopListening, SendVoiceCommand, SetWakeWord, PullTTSModel |
| **Search** | Query, GetStatus | IndexDirectory, ReindexAll, DeleteIndex |
| **Sandbox** | GetStatus | AuditAndPrompt, SandboxRun, ShieldStatus, EnableShield, DisableShield |
| **GuiAgent** | GetStatus | Click, Type, Screenshot, GetActiveWindow, ListWindows |
| **Context** | GetSnapshot, GetStatus | UpdateSnapshot, ClearCache, SetWatchPaths |

The session user (`${user}`) retains full access to all methods.

### Design note

D-Bus session bus policies cannot match by sender bus name or object path. The
intended caller restrictions (Brain methods limited to org.axonos.IntentBar,
org.axonos.VoiceService, org.axonos.AIPanel, org.axonos.GuiAgent) are documented
in the conf files but cannot be enforced at the D-Bus policy level alone.
**Application-level caller verification** (e.g., checking the caller's unique
bus name via `dbus.sender`) is recommended as defense-in-depth.

### Risk assessment

Low. The `${user}` policy still allows all methods for session processes, so
existing functionality is preserved. Non-session processes (system services,
etc.) are now restricted to read-only methods. This establishes the principle
of least privilege and provides a foundation for future caller-name verification.

---

## Files Modified

| File | Fix # | Lines changed |
|------|-------|--------------|
| `services/axon-brain/prompts.py` | 1 | 14 lines (full file rewrite) |
| `services/axon-brain/brain_service.py` | 2 | ~30 lines (module-level + function) |
| `services/service_utils.py` | 3, 4 | ~6 lines (ALLOWED_COMMANDS + _SHELL_META_CHARS) |
| `services/axon-brain/org.axonos.Brain.conf` | 5 | 39 lines (full file rewrite) |
| `services/axon-voice/org.axonos.Voice.conf` | 5 | 26 lines (full file rewrite) |
| `services/axon-search/org.axonos.Search.conf` | 5 | 24 lines (full file rewrite) |
| `services/axon-sandbox/org.axonos.Sandbox.conf` | 5 | 26 lines (full file rewrite) |
| `services/axon-gui-agent/org.axonos.GuiAgent.conf` | 5 | 26 lines (full file rewrite) |
| `services/axon-context/org.axonos.Context.conf` | 5 | 25 lines (full file rewrite) |

**Total: 9 files modified across 5 fixes**

---

## Validation

- All Python files maintain valid syntax (no import changes, no structural changes)
- D-Bus conf files follow freedesktop DTD structure
- No modifications outside the listed constraint files
- Follows existing code style (Google docstrings, ruff-compatible formatting)

## Remaining Recommendations

These items were not in scope for this fix batch but are recommended:

1. **Add caller-name verification in BrainService** — check `dbus.sender` against a known allowlist at the Python level
2. **Add `ClassifyIntent` output filtering** — validate/filter the AI-generated command at the service level (finding M7)
3. **Audit `settings_executor.py`** — direct subprocess calls bypass `safe_exec` (finding M3)
