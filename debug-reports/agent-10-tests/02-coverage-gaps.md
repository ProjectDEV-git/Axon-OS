# 02 — Coverage Gaps & Untested Edge Cases

Generated: 2026-07-02T11:30:00Z

## 1. Test-to-Source Coverage Matrix

### Services Layer (~7,600 LOC total)

| Source File | LOC | Test File(s) | Coverage Quality |
|------------|-----|--------------|-----------------|
| `axon-brain/brain_service.py` | 593 | test_brain_service.py, test_services_enhanced.py | ⚠️ Partial — only validates signatures, sanitization. `SendMessage` logic, `GetStatus`, `ListModels`, `GetEmbeddings` not unit-tested |
| `axon-brain/ai_router.py` | 211 | test_ai_router.py, test_phase4.py | ✅ Good — classification, model selection, helpers all tested |
| `axon-brain/conversation_store.py` | 211 | test_services_enhanced.py, test_conversation_store.py | ✅ Good — CRUD, search, thread safety, connection cleanup |
| `axon-brain/hardware_profiler.py` | 190 | test_hardware_profiler.py, test_hardware_profiler_extended.py | ✅ Good — RAM parsing, GPU detection (NVIDIA/AMD/Intel/CPU), recommendations |
| `axon-brain/model_marketplace.py` | 447 | test_model_marketplace.py, test_phase4.py | ⚠️ Partial — catalog validation and HTTP helpers tested, but `pull_model`, `list_installed_models`, `delete_model` not tested |
| `axon-brain/prompts.py` | 6 | None | ❌ No tests (only 6 lines, likely just prompt strings) |
| `axon-search/search_service.py` | 451 | test_search_service.py, test_innovations.py | ⚠️ Partial — DB setup, keyword query, delete, reindex, vector query tested. `RescanIndex`, `Search`, D-Bus methods not tested |
| `axon-search/indexer.py` | 172 | test_innovations.py | ✅ Good — chunk_text, should_index, read_text all tested |
| `axon-search/global_search_service.py` | 219 | test_phase4.py | ❌ Only import test — no method tests |
| `axon-voice/advanced_voice_service.py` | 478 | test_phase4.py | ❌ Only import test + constants check — no method tests |
| `axon-voice/intent_router.py` | 49 | test_innovations.py | ✅ Good — clean_transcript, parse_intent_response tested |
| `axon-voice/voice_service.py` | 459 | None | ❌ **NO TESTS** — 459 lines completely untested |
| `axon-voice/overlay.py` | 127 | None | ❌ **NO TESTS** |
| `axon-voice/vad_helper.py` | 81 | test_voice_and_terminal.py | ✅ Good — speech detection, silence rejection |
| `axon-sandbox/audit.py` | 150 | test_innovations.py | ✅ Good — clean scripts, SSH theft, curl pipe, rm rf, reverse shell, comments, persistence |
| `axon-sandbox/audit_v2.py` | 507 | None | ❌ **NO TESTS** — 507 lines completely untested |
| `axon-sandbox/sandbox_manager.py` | 296 | test_sandbox.py, test_sandbox_manager_extended.py | ✅ Good — fail-closed, fallback analysis, truncation, Brain response parsing |
| `axon-sandbox/shield.py` | 218 | None | ❌ **NO TESTS** — 218 lines completely untested |
| `axon-gui-agent/gui_agent_service.py` | 195 | test_gui_agent_validation.py | ⚠️ Partial — only `_validate_app_name` tested. `ExecutePlan`, `GetStatus` D-Bus methods not tested |
| `axon-gui-agent/plan.py` | 124 | test_innovations.py | ✅ Good — validation, disallowed schemas, shell injection, markdown fence, gvariant |
| `axon-context/context_service.py` | 486 | test_services_enhanced.py | ❌ Only tests init + JSON structure — 486 lines, no method tests |
| `axon-context/clipboard_store.py` | 180 | None | ❌ **NO TESTS** — 180 lines completely untested |
| `axon-context/file_indexer.py` | 196 | None | ❌ **NO TESTS** — 196 lines completely untested |
| `constants.py` | 51 | test_services_enhanced.py | ✅ Import and value tests |
| `i18n.py` | 90 | test_i18n.py | ⚠️ Minimal — only tests get_translator returns callable and translate returns string |
| `_log_helper.py` | 52 | None | ❌ **NO TESTS** |
| `service_base.py` | 148 | test_plugin_system.py | ✅ Good — init, health tracking, uptime, duplicate name exit |
| `service_utils.py` | 276 | test_service_utils.py, test_services_enhanced.py | ✅ Good — TTLCache, RateLimiter, safe_exec, error_response, decorators |
| `plugin_registry.py` | 472 | test_plugin_registry.py, test_plugin_system.py | ✅ Good — manifest parsing, discovery, validation, topo sort |
| `plugin_deploy.py` | 216 | test_plugin_system.py | ✅ Good — systemd unit, D-Bus service, D-Bus policy generation |
| `telemetry.py` | 254 | test_telemetry.py, test_phase4.py | ✅ Good — opt-in/out, events, crashes, summary, singleton |

### Apps Layer (partial)

| Source File | LOC | Test File(s) | Coverage Quality |
|------------|-----|--------------|-----------------|
| `apps/axon-settings/settings_executor.py` | varies | test_settings_executor.py | ✅ Good — validation, routing, display, power, input |
| `apps/axon-installer/install_engine.py` | varies | test_installer.py, test_innovations.py | ✅ Good — config validation, part_node, fstab_lines |
| `apps/axon-terminal/safety.py` | varies | test_voice_and_terminal.py | ✅ Good — command assessment, findings formatting |
| `apps/axon-terminal/ai_helper.py` | varies | None | ❌ No tests |
| `apps/axon-terminal/terminal_widget.py` | varies | None | ❌ No tests (GTK UI — may need display) |
| `apps/axon-files/` (all) | varies | None | ❌ No tests |
| `apps/axon-ai-panel/` (all) | varies | None | ❌ No tests |
| `apps/axon-welcome/` (all) | varies | None | ❌ No tests |
| `apps/axon-shortcuts/` (all) | varies | None | ❌ No tests |
| `apps/intent-bar/` (all) | varies | None | ❌ No tests |
| `apps/axon-logger.py` | varies | None | ❌ No tests |

### Installer Layer

| Source File | LOC | Test File(s) | Coverage Quality |
|------------|-----|--------------|-----------------|
| `installer/partitioner.py` | varies | test_partitioner.py | ⚠️ Dry-run only — no real partition tests |
| `installer/branding/axon/generate_branding.py` | varies | None | ❌ No tests |

### Kernel Module

| Source Files | LOC | Tests | Coverage Quality |
|-------------|-----|-------|-----------------|
| `kernel/axon-winabi/*.c` (14 files) | ~3,900 | `tests/hello.c`, `tests/hello_nt.c`, `axon-winabi-test.c` | ❌ **NO automated tests** — only manual test programs, not compiled/tested in CI |

## 2. Completely Untested Files (High Priority)

These files have **zero test coverage** and are non-trivial:

| File | LOC | Risk Level | Why It Matters |
|------|-----|------------|----------------|
| `services/axon-sandbox/audit_v2.py` | 507 | **HIGH** | Security-critical script auditing — successor to audit.py. A bug here means dangerous scripts could pass inspection |
| `services/axon-voice/voice_service.py` | 459 | **HIGH** | Core voice pipeline — speech-to-text, recording, processing. Bugs here break voice commands |
| `services/axon-context/context_service.py` | 486 | **MEDIUM** | Context tracking — window tracking, clipboard, clipboard history. Security-relevant (clipboard can contain passwords) |
| `services/axon-search/global_search_service.py` | 219 | **MEDIUM** | Cross-service search coordination |
| `services/axon-voice/advanced_voice_service.py` | 478 | **MEDIUM** | Multi-engine voice support (whisper, vosk, wake words) |
| `services/axon-sandbox/shield.py` | 218 | **HIGH** | Shield/protection layer for sandbox — security boundary |
| `services/axon-context/clipboard_store.py` | 180 | **MEDIUM** | Clipboard history persistence |
| `services/axon-context/file_indexer.py` | 196 | **LOW** | File indexing for context |
| `services/_log_helper.py` | 52 | **LOW** | Logging helper |

**Total untested production code: ~2,875 lines in services alone**

## 3. Error Path Coverage Gaps

### brain_service.py
- ❌ `SendMessage` with Ollama connection failure (no mock test for Ollama being down)
- ❌ `SendMessage` with invalid model name (validation tested but not the actual send path)
- ❌ `GetStatus` when Ollama is unreachable
- ❌ `GetEmbeddings` error handling

### search_service.py
- ❌ `RescanIndex` with filesystem permission errors
- ❌ `_embed` failure during reindex
- ❌ SQLite database corruption recovery
- ❌ FTS5 query with special characters (SQL injection via FTS)

### context_service.py
- ❌ Clipboard monitoring failure (e.g., xclip not installed)
- ❌ Active window detection failure (no xdotool)
- ❌ Database write failure during context save

### voice_service.py
- ❌ Recording timeout handling
- ❌ Audio device not found
- ❌ Whisper model not downloaded
- ❌ Vosk model not found

### audit_v2.py
- ❌ Malformed script content (binary, encoding errors)
- ❌ Very large script content (memory/buffer overflow)
- ❌ Nested/encoded payloads (base64-encoded malicious commands)

## 4. Edge Case Coverage Gaps

### ConversationStore
- ✅ Thread safety tested
- ❌ Database file locked by another process
- ❌ Database disk full
- ❌ Very large message content (>10MB)
- ❌ Unicode/emoji in messages
- ❌ SQL injection via search terms

### TTLCache
- ✅ Max entries eviction tested
- ❌ Zero TTL edge case (already tested but could document behavior)
- ❌ Very large values (memory pressure)
- ❌ Concurrent eviction (multiple threads triggering eviction simultaneously)

### RateLimiter
- ✅ Window reset tested
- ❌ Rate=0 edge case (should deny everything)
- ❌ Negative rate (should deny everything)
- ❌ Very large window_seconds (overflow behavior)

### Plugin System
- ✅ Topological sort tested
- ❌ Circular dependencies in plugins
- ❌ Plugin that takes >30 seconds to load
- ❌ Plugin with invalid TOML
- ❌ Plugin that crashes during load

### SettingsExecutor
- ✅ Good validation coverage
- ❌ Concurrent command execution
- ❌ Brain service crashing mid-execution

## 5. i18n Completeness

**The `.po` file only has English translations** (en_US). The `i18n.py` module:
- Uses `gettext` with `fallback=True`, so it always returns the original string
- The `_FALLBACK_TRANSLATIONS` dict in `i18n.py` has 17 entries
- The `.po` file has 25 entries (includes shell-specific strings)
- **No non-English locale files exist** — the i18n system is infrastructure-only with no actual translations
- **Test coverage is minimal**: `test_i18n.py` only checks that `get_translator()` returns a callable and `translate()` returns a string. It does not test:
  - Whether all translatable strings in the codebase are extracted
  - Whether locale switching works
  - Whether the fallback translations are actually used
  - Whether .po file entries match the code strings

## 6. Kernel Module Test Coverage

The kernel module (`kernel/axon-winabi/`) contains:
- 14 C source files (~3,900 lines)
- 1 header file
- 3 test files (`tests/hello.c`, `tests/hello_nt.c`, `axon-winabi-test.c`)
- **None of these tests are automated** — they must be compiled and run manually on a target system
- The `Makefile` exists but the module is noted as "NOT compiled/tested yet" in AGENTS.md
- **No build verification in CI** — the QA script does not attempt to compile or lint C code

## 7. Summary Statistics

| Category | Count |
|----------|-------|
| Total test files | 24 |
| Total source files in services/ | 31 |
| Source files with tests | 18 (58%) |
| Source files with NO tests | 13 (42%) |
| Lines of untested production code (services) | ~2,875 |
| Lines of untested production code (apps) | ~1,500+ (estimate) |
| Kernel module lines without automated tests | ~3,900 |
| Error paths tested vs. estimated total | ~15% |
| Edge cases tested vs. estimated total | ~20% |
