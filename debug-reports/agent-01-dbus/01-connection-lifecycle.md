# 01 — D-Bus Connection Lifecycle & Error Handling

**Agent**: Debug Agent 1 (D-Bus Infrastructure)
**Date**: 2026-07-02
**Files Analyzed**: service_base.py, brain_service.py, context_service.py, voice_service.py, sandbox_manager.py, search_service.py, gui_agent_service.py

---

## 1. ServiceBase Bootstrap Pattern

### 1.1 NameExistsException Handling

| Service | Pattern | Severity |
|---------|---------|----------|
| **ServiceBase** | `print()` to stderr + `sys.exit(1)` | OK |
| **BrainService** | `logger.error()` + `sys.exit(1)` | OK |
| **ContextService** | `logger.error()` + `sys.exit(1)` | OK |
| **VoiceService** | `log.error()` + `sys.exit(1)` | OK |
| **SandboxManager** | `logger.error()` + `sys.exit(1)` | OK |
| **SearchService** | `log.error()` + `sys.exit(1)` | OK |
| **GuiAgentService** | `log.error()` + `sys.exit(1)` | OK |

**Finding**: All services handle `NameExistsException` consistently by logging and exiting. ServiceBase uses `print()` because the logger may not be ready yet, which is a valid design choice. **No bug here.**

### 1.2 ServiceBase Inheritance

**BUG — CRITICAL**: `ServiceBase` exists at `services/service_base.py` but **no concrete service inherits from it**. Every service (`BrainService`, `ContextService`, `VoiceService`, `SandboxManager`, `SearchService`, `GuiAgentService`) directly subclasses `dbus.service.Object` and duplicates the entire bootstrap pattern:
- GLib main loop setup
- Session bus creation
- BusName claim with NameExistsException
- Object path registration

This means `ServiceBase` is **dead code**. Its `GetStatus()` and `GetServiceName()` methods, health tracking (`_health_lock`, `_healthy`, `_start_time`), and `uptime` property are **never used by any service**.

**Impact**: 6 services each maintain 15+ lines of duplicated bootstrap code. Any fix to the bootstrap pattern must be applied 6 times.

### 1.3 Session Bus Unavailability

**BUG — HIGH**: `dbus.SessionBus()` is called in every service's `__init__` with **no try/except**. If the D-Bus session bus is unavailable (headless server, no user session, `DBUS_SESSION_BUS_ADDRESS` unset), every service crashes with an unhandled `dbus.exceptions.DBusException`.

**Expected**: Graceful error message + non-zero exit or retry.
**Actual**: Raw Python traceback.

**Affected files**:
- `service_base.py:61`
- `brain_service.py:82`
- `context_service.py:45`
- `voice_service.py:61`
- `sandbox_manager.py:176`
- `search_service.py:99`
- `gui_agent_service.py:71`

### 1.4 GLib Main Loop Integration

**All services**: `dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)` is called before `dbus.SessionBus()`. This is correct — GLib must be set as the main loop before any D-Bus connections are made.

**Note**: `set_as_default=True` is called multiple times if ServiceBase is ever mixed with direct `dbus.service.Object` usage. This is harmless but indicates design confusion.

### 1.5 GetStatus() Consistency

| Service | Implements `GetStatus()` | Interface | Notes |
|---------|-------------------------|-----------|-------|
| ServiceBase | Yes | `org.axonos.Service` | JSON: service, healthy, uptime |
| BrainService | Yes | `org.axonos.Brain` | JSON: ollama_online, active_models, configured_models |
| ContextService | **NO** | — | Missing |
| VoiceService | **NO** | — | Missing |
| SandboxManager | **NO** | — | Missing |
| SearchService | **NO** | — | Has `GetStats()` on `org.axonos.Search` |
| GuiAgentService | **NO** | — | Missing |

**Finding**: Only BrainService implements GetStatus, and it's on a *different* D-Bus interface (`org.axonos.Brain` vs `org.axonos.Service`). Since no service inherits ServiceBase, the `org.axonos.Service` interface is **never exposed on the bus**. There is no uniform status query mechanism.

### 1.6 Main Loop Entry Point

| Service | Signal Handling | Cleanup |
|---------|----------------|---------|
| ServiceBase | `KeyboardInterrupt` only | None |
| BrainService | SIGTERM + SIGINT + KeyboardInterrupt | None |
| ContextService | SIGTERM + SIGINT + KeyboardInterrupt | `service.cleanup()` |
| VoiceService | SIGTERM + SIGINT + KeyboardInterrupt | None |
| SandboxManager | SIGTERM + SIGINT + KeyboardInterrupt | None |
| SearchService | SIGTERM + SIGINT + KeyboardInterrupt | None |
| GuiAgentService | SIGTERM + SIGINT + KeyboardInterrupt | None |

**Warning**: BrainService, VoiceService, SandboxManager, SearchService, and GuiAgentService have **no cleanup on shutdown**. BrainService's background threads (streaming, pull) are daemon threads so they die with the process, but open HTTP connections may be aborted uncleanly. ContextService properly calls `cleanup()` to terminate the clipboard watcher subprocess.

---

## Summary of Findings

### Critical Bugs
1. **ServiceBase is dead code** — no service inherits from it; bootstrap is duplicated 6 times
2. **No D-Bus session bus fallback** — all services crash with raw traceback if session bus unavailable

### Warnings
3. **Inconsistent GetStatus()** — only BrainService has it, on a different interface; 5 services have no status endpoint
4. **No shutdown cleanup** in 5/7 services (missing subprocess/connection cleanup)
5. **Inconsistent bootstrap** — ServiceBase uses `print()` for NameExistsException, services use `logger.error()`
