# Axon OS — Master Debug Report

**Date:** 2026-07-02
**Agents:** 10 specialized debug agents, 30 sub-analyses
**Tokens consumed:** ~30M+
**Files analyzed:** 100+ source files across all components

---

## Executive Summary

The Axon OS codebase demonstrates ambitious, well-structured architecture with solid foundational patterns (D-Bus services, GTK4/libadwaita apps, kernel module, AI routing). However, the deep audit reveals **26 critical bugs**, **13 high-severity bugs**, and **60+ warnings** across all components. The kernel module (axon-winabi) is the highest-risk area with 9 critical bugs including use-after-free and buffer overflows. Security vulnerabilities in D-Bus policies and prompt injection chains create exploitable attack paths. Several core features are non-functional due to wiring bugs (global search, context-aware routing).

**Immediate priorities (P0):**
1. Fix kernel use-after-free and buffer overflow (security/crash risk)
2. Add safety instructions to AI system prompt (prompt injection defense)
3. Restrict D-Bus policies (prevents privilege escalation)
4. Fix broken global search wiring (one-line fix, feature completely broken)
5. Fix AI context transport (context-aware routing non-functional)

---

## Bug Counts by Severity

| Severity | Count | Components |
|----------|-------|------------|
| CRITICAL | 26 | Kernel (9), Security (5), Tests (4), Voice (3), AI Routing (3), Hardware (3), File System (2), Installer (3), D-Bus (1) |
| HIGH | 13 | GUI (10), Installer (5), Kernel (13 bugs), File System (3), Hardware (6 warnings) |
| MEDIUM | ~35 | Across all components |
| LOW/INFO | ~25 | Across all components |
| **TOTAL** | **~130** | |

---

## Top 20 Most Critical Findings

### 1. KERNEL: Use-after-free in `axon_get_task_state` (C1)
**File:** `kernel/axon-winabi/module-main.c`
**Impact:** Kernel crash or exploitable vulnerability. Hash table read without spinlock while concurrent free can occur.
**Fix:** Add RCU or spinlock around hash table access.

### 2. KERNEL: Buffer overflow in shell32 `SHGetFolderPathA` (C9)
**File:** `kernel/axon-winabi/dlls/shell32.c`
**Impact:** Stack corruption. `strcpy(pszPath, "/tmp")` with no size check.
**Fix:** Use `strscpy` with buffer size limit.

### 3. KERNEL: Memory protection always grants write (C5)
**File:** `kernel/axon-winabi/nt-syscalls.c`
**Impact:** W^X violation, security hole. All protection levels (READONLY, EXECUTE, NOACCESS) get `PROT_READ|PROT_WRITE`.
**Fix:** Implement complete Windows-to-Linux protection flag translation table.

### 4. SECURITY: All D-Bus policies fully permissive (H1)
**File:** All `services/*/org.axonos.*.conf`
**Impact:** Any session process can call any method on any service. Enables DoS, data theft, conversation deletion.
**Fix:** Add caller-specific restrictions (e.g., only specific apps can call Brain methods).

### 5. SECURITY: Indirect prompt injection via window titles (H3)
**File:** `services/axon-brain/brain_service.py:70-75`
**Impact:** `_sanitize_context()` only removes null bytes and truncates. Attacker names window with injection payload -> embedded in AI system prompt.
**Fix:** Strip prompt injection patterns, wrap in `<untrusted_context>` tags.

### 6. SECURITY: AI system prompt has zero safety instructions (H4)
**File:** `services/axon-brain/prompts.py:3-5`
**Impact:** No boundaries on harmful commands. Combined with prompt injection, AI can generate destructive `run_command` actions.
**Fix:** Add explicit safety rules against destructive commands.

### 7. FILE SYSTEM: Global search file results permanently broken (C1)
**File:** `services/axon-search/global_search_service.py:100`
**Impact:** Unified search never returns file results. D-Bus method name mismatch: calls `svc.Search(query)` but service only exposes `Query(query, limit)`.
**Fix:** Change `svc.Search(query)` to `svc.Query(query, 8)`. One-line fix.

### 8. AI ROUTING: Context never forwarded to Brain service (C1)
**File:** `apps/axon-ai-panel/ui/panel.py:666-669`, `apps/intent-bar/ollama_client.py:176`
**Impact:** Context-aware routing completely non-functional. AI panel builds context but never passes it through D-Bus call.
**Fix:** Wire `ctx_string` through `send_message_stream()` -> `brain.SendMessage()`.

### 9. D-BUS: Thread safety violations in signal emission (02-thread-safety.md)
**File:** All services (Brain, Voice, Search, Context, GUI Agent)
**Impact:** D-Bus signals emitted from Python threads without GLib main loop integration. Can cause crashes or lost signals.
**Fix:** Use `GLib.idle_add()` to marshal signal emissions to main loop.

### 10. VOICE: Recorder subprocess orphaned on shutdown (BUG-1)
**File:** `services/axon-voice/voice_service.py:450`
**Impact:** On SIGTERM/SIGINT, parecord/arecord subprocess never killed. Holds microphone exclusively, preventing future recordings.
**Fix:** Kill recorder subprocess in shutdown handler.

### 11. KERNEL: advapi32 wrong syscall numbers (C3)
**File:** `kernel/axon-winabi/dlls/advapi32.c`
**Impact:** All registry operations broken. `NR_NT_OPEN_KEY=0x0F4` should be `0x2C`.
**Fix:** Correct syscall number constants.

### 12. KERNEL: nt_pulse_event deadlock (C4)
**File:** `kernel/axon-winabi/nt-sync.c`
**Impact:** `wake_up_all` called inside spinlock, then `signaled=false`. Waiters see false and re-sleep. Pulse events never work.
**Fix:** Restructure wake/reset sequence.

### 13. HARDWARE: Low-RAM systems get impossible model recommendations (C2)
**File:** `services/axon-brain/hardware_profiler.py:164-180`
**Impact:** Systems with 2-4 GB RAM recommended 3B models (~2GB). Will OOM or thrash.
**Fix:** Add `ram < 4.0` branch recommending only 1B models.

### 14. HARDWARE: Intel Arc GPUs severely undersold (C3)
**File:** `services/axon-brain/hardware_profiler.py:90-96`
**Impact:** Arc A770 (16GB VRAM) detected as "Intel Integrated Graphics" with 2GB. User gets 3B models instead of 14B+.
**Fix:** Parse lspci model string for "Arc" keyword.

### 15. TESTS: 2,875+ lines of security-critical code have zero tests (C2)
**Files:** `audit_v2.py` (507 lines), `shield.py` (218 lines), `voice_service.py` (459 lines), `context_service.py` (486 lines)
**Impact:** Primary security boundary for script execution has no test coverage.
**Fix:** Write tests for `audit_v2.py` and `shield.py` immediately.

### 16. GUI: CSS providers accumulated on every window open (C1)
**Apps:** AIPanel, IntentBar, SandboxDialog, Welcome, Shortcuts
**Impact:** Memory leak + CSS specificity conflicts. Duplicate CSS providers registered globally each time.
**Fix:** Add `_css_loaded` guard flags.

### 17. SECURITY: `find` in ALLOWED_COMMANDS enables file enumeration (H5)
**File:** `services/service_utils.py:22`
**Impact:** Allows systematic search for private keys, configs, credentials without metacharacters.
**Fix:** Remove `find` from ALLOWED_COMMANDS or add path restrictions.

### 18. KERNEL: Thread creation uses wrong mm context (C2)
**File:** `kernel/axon-winabi/nt-thread.c`
**Impact:** `kthread_use_mm(current->mm)` called from parent instead of child. New threads may not have correct address space.
**Fix:** Defer mm setup to child trampoline.

### 19. AI ROUTING: GetEmbeddings falls back to wrong model (C3)
**File:** `services/axon-brain/brain_service.py:421`
**Impact:** Falls back to `speed_model` (language model like llama3.2:3b) instead of embedding model. Produces nonsensical results.
**Fix:** Change to `"nomic-embed-text"` or add `embedding_model` to config.

### 20. HARDWARE: spaces.json saves are NOT atomic (C1)
**File:** `apps/intent-bar/spaces_manager.py:93`
**Impact:** Power loss during write truncates file, destroying all space data.
**Fix:** Write to `.tmp` then `Path.replace()`.

---

## Component-Level Summary

### 1. D-Bus Services (Agent 01)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 1 | Signal emission from worker threads without GLib main loop |
| Warning | 7 | Missing lifecycle management, no backpressure, inconsistent patterns |
| **Verdict** | Solid foundation, thread safety needs systematic fix |

### 2. AI Routing (Agent 02)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 3 | Context never forwarded, classify_task ignores context, wrong embedding fallback |
| Warning | 8 | Code pattern thresholds too strict, single keywords false-positive, no model validation |
| **Verdict** | Core routing logic works but context-aware routing is non-functional |

### 3. Security (Agent 03)
| Severity | Count | Key Issues |
|----------|-------|------------|
| High | 5 | Permissive D-Bus policies, prompt injection, no safety instructions, file enumeration |
| Medium | 8 | Missing meta chars, xdg-open risk, settings bypass, sandbox gaps |
| Low | 4 | No audit trail, weak terminal safety, template variable trust |
| **Verdict** | Multiple exploitable attack chains identified |

### 4. Voice Pipeline (Agent 04)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 3 | Subprocess orphaned, TOCTOU race, no recording timeout |
| Warning | 5 | Temp file leak, whisper dir missing, Vosk model recreated, no TTS fallback |
| **Verdict** | Core pipeline works but shutdown and edge cases need fixes |

### 5. Kernel Module (Agent 05)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 9 | Use-after-free, wrong syscall numbers, buffer overflow, memory protection, deadlock |
| Bug | 13 | Thread lifecycle, handle management, missing validation, test gaps |
| Warning | 14 | Incomplete protection flags, global mapping list, DXVK race |
| **Verdict** | Highest-risk component. Architecture correct but implementation has critical safety bugs |

### 6. File System & Search (Agent 06)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 2 | Global search permanently broken, duplicate indexer systems |
| High | 3 | No D-Bus timeouts, missing WAL mode, no proxy reconnection |
| Medium | 9 | Excessive indexing, no CJK support, no schema migration |
| Low | 9 | Double stat, inconsistent watch dirs, no connection pooling |
| **Verdict** | One-line fix for broken search; consolidation needed for indexers |

### 7. GUI Applications (Agent 07)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 2 | CSS provider leak, no light theme support |
| High | 8 | Thread leaks, missing cleanup, deprecated APIs, no confirmation dialogs |
| Medium | 9 | Thread races, CSS conflicts, inconsistent tokens |
| Low | 7 | Timer leaks, no multi-monitor, popover per-click |
| **Verdict** | Needs shared design token system; 5 apps affected by CSS leak |

### 8. Installer & Build (Agent 08)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 3 | No BIOS support, unpinned packages, three inconsistent installer paths |
| High | 5 | No password minimum, theme drift, stale branding, no disk validation |
| Warning | 7 | Timestamp non-determinism, pip drift, fragile Ollama process |
| **Verdict** | Consolidate to single installer; pin packages for reproducibility |

### 9. Hardware Profiling (Agent 09)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 3 | Non-atomic saves, impossible low-RAM recommendations, Intel Arc misidentified |
| Warning | 6 | Config corruption, TOML escaping, multi-GPU, no thread locking |
| **Verdict** | GPU detection and low-RAM paths need immediate fixes |

### 10. Test Suite (Agent 10)
| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 4 | D-Bus dependency, zero tests for security code, incomplete CI, suppressed errors |
| Warning | 6 | sys.path pollution, flaky timing tests, no i18n, stub plugins |
| **Verdict** | Score 5.4/10. Solid foundation but major coverage and CI gaps |

---

## Recommended Fix Schedule

### Sprint 1 — Emergency (Week 1): Security & Crash Bugs
| # | Component | Fix | Effort |
|---|-----------|-----|--------|
| 1 | Kernel | Fix use-after-free in `axon_get_task_state` | 2h |
| 2 | Kernel | Fix buffer overflow in shell32 | 1h |
| 3 | Kernel | Fix memory protection mapping | 4h |
| 4 | Kernel | Fix advapi32 syscall numbers | 1h |
| 5 | Security | Add safety instructions to `CHAT_SYSTEM_PROMPT` | 1h |
| 6 | Security | Sanitize context before embedding in prompts | 2h |
| 7 | Security | Remove `find` from ALLOWED_COMMANDS | 30m |
| 8 | File System | Fix GlobalSearch method name (one-line) | 5m |
| 9 | AI Routing | Fix context transport wiring | 2h |
| 10 | Voice | Fix shutdown handler to kill recorder | 1h |

### Sprint 2 — Core Stability (Week 2): D-Bus & Kernel
| # | Component | Fix | Effort |
|---|-----------|-----|--------|
| 11 | D-Bus | Add GLib.idle_add for all signal emissions | 8h |
| 12 | Kernel | Fix nt_pulse_event deadlock | 2h |
| 13 | Kernel | Fix thread creation mm context | 4h |
| 14 | Kernel | Fix begin_new_exec ordering | 4h |
| 15 | Hardware | Make spaces.json saves atomic | 1h |
| 16 | Hardware | Fix low-RAM model recommendations | 2h |
| 17 | Hardware | Fix Intel Arc GPU detection | 2h |
| 18 | Voice | Fix TOCTOU race on toggle | 1h |
| 19 | Voice | Add recording timeout to AdvancedVoice | 1h |
| 20 | AI Routing | Fix GetEmbeddings fallback | 30m |

### Sprint 3 — Functionality (Week 3): Features & Polish
| # | Component | Fix | Effort |
|---|-----------|-----|--------|
| 21 | GUI | Create shared design token CSS system | 8h |
| 22 | GUI | Add `_css_loaded` guards to 5 apps | 2h |
| 23 | GUI | Add thread cancellation flags | 4h |
| 24 | File System | Consolidate duplicate indexers | 8h |
| 25 | File System | Add WAL mode to remaining databases | 2h |
| 26 | File System | Add proxy reconnection in GlobalSearch | 2h |
| 27 | Installer | Deprecate older wizard, consolidate | 8h |
| 28 | Installer | Pin package versions | 2h |
| 29 | Tests | Write tests for audit_v2.py and shield.py | 8h |
| 30 | Tests | Fix qa.sh to include full CI pipeline | 2h |

### Sprint 4 — Production Readiness (Week 4): Hardening
| # | Component | Fix | Effort |
|---|-----------|-----|--------|
| 31 | Security | Restrict D-Bus policies with caller restrictions | 8h |
| 32 | Security | Add command validation to ClassifyIntent | 4h |
| 33 | Kernel | Comprehensive test suite for axon-winabi | 16h |
| 34 | Kernel | Unify handle management systems | 8h |
| 35 | Tests | Expand coverage to 60%+ | 16h |
| 36 | GUI | Fix all deprecated GTK4 API usage | 4h |
| 37 | File System | Add schema migration system | 4h |
| 38 | Installer | Add BIOS boot support to newer wizard | 8h |

---

## Positive Observations

Despite the bugs found, the codebase demonstrates strong engineering in several areas:

1. **`safe_exec` uses list-based `subprocess.Popen`** — prevents shell injection even if metacharacter checks are bypassed
2. **Rate limiting on critical D-Bus methods** — `Generate`, `SendMessage`, `ClassifyIntent` all have rate limits
3. **Bubblewrap sandbox in shield.py** — proper Linux namespace sandboxing with secret directory masking
4. **Boot watchdog logic is correct** — counter mechanism and recovery entry work as designed
5. **Firstboot is idempotent** — guard file pattern prevents re-execution
6. **AI firstboot retry logic** — well-designed state machine with marker files
7. **PE header parsing is solid** — proper validation of MZ magic, PE signature, machine type
8. **Syscall dispatch is clean** — table-based dispatch with bounds checking
9. **Registry emulation** — in-memory tree with proper reference counting
10. **AST-based audit v2** — deep command structure analysis for script auditing

---

## Files Generated

```
debug-reports/
├── MASTER-DEBUG-REPORT.md          (this file)
├── agent-01-dbus/
│   ├── 01-connection-lifecycle.md
│   ├── 02-thread-safety.md
│   ├── 03-signals-streaming.md
│   └── SUMMARY.md
├── agent-02-ai-routing/
│   ├── 01-pattern-matching.md
│   ├── 02-model-fallback.md
│   ├── 03-context-routing.md
│   └── SUMMARY.md
├── agent-03-security/
│   ├── 01-shell-injection.md
│   ├── 02-sandbox-dbus.md
│   ├── 03-prompt-injection.md
│   └── SUMMARY.md
├── agent-04-voice/
│   ├── 01-recording-lifecycle.md
│   ├── 02-whisper-memory.md
│   ├── 03-intent-tts.md
│   └── SUMMARY.md
├── agent-05-kernel/
│   ├── 01-pe-parsing.md
│   ├── 02-dll-emulation.md
│   ├── 03-syscall-memory.md
│   └── SUMMARY.md
├── agent-06-files-search/
│   ├── 01-file-indexer.md
│   ├── 02-search-service.md
│   ├── 03-sqlite-management.md
│   └── SUMMARY.md
├── agent-07-gui/
│   ├── 01-widget-lifecycle.md
│   ├── 02-css-theming.md
│   ├── 03-event-handling.md
│   └── SUMMARY.md
├── agent-08-installer/
│   ├── 01-installer-edge-cases.md
│   ├── 02-build-reproducibility.md
│   ├── 03-grub-boot.md
│   └── SUMMARY.md
├── agent-09-hardware/
│   ├── 01-gpu-detection.md
│   ├── 02-config-persistence.md
│   ├── 03-model-recommendations.md
│   └── SUMMARY.md
└── agent-10-tests/
    ├── 01-test-isolation.md
    ├── 02-coverage-gaps.md
    ├── 03-integration-reliability.md
    └── SUMMARY.md
```

**Total: 31 reports + this master summary**
