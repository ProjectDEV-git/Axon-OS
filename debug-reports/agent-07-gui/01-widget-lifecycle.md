# Sub-Task 1: GTK4/Adw Widget Lifecycle & Memory Management

**Agent 7 — GUI Applications Deep Debug**
**Date: 2026-07-02**

---

## 1. Critical: CSS Providers Loaded Per-Instance (No Deduplication)

### AIPanelWindow (panel.py:366-372)
```python
css_provider = Gtk.CssProvider()
css_provider.load_from_data(self._CSS)
Gtk.StyleContext.add_provider_for_display(
    self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
)
```
**Bug:** Every new `AIPanelWindow` instance creates a new `CssProvider` and registers it globally via `add_provider_for_display()`. No guard flag exists. Each panel open accumulates a duplicate CSS provider on the display, leaking memory and potentially causing CSS specificity conflicts.

**Severity:** Critical
**Fix:** Add a class-level `_css_loaded = False` guard, similar to `VoiceOverlay`.

### IntentBarWindow (intent-bar/ui/window.py:233-240)
```python
def _apply_css(self) -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(
        self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
```
**Bug:** Same issue. `_apply_css()` is called from `__init__()` with no deduplication guard. Every open/close of the intent bar registers a new provider.

**Severity:** Critical

### SandboxPromptDialog (sandbox_manager.py:41-89)
**Bug:** Each `SandboxPromptDialog.__init__()` creates a new CSS provider with the sandbox dialog styles and registers it globally. Multiple concurrent sandbox prompts accumulate providers.

**Severity:** High

### WelcomeWindow (welcome.py:163-169)
**Bug:** Each `WelcomeWindow.__init__()` creates a new `CssProvider` from the `_CSS` bytes and registers it. No guard.

**Severity:** Medium (welcome screen is typically opened once per session)

### ShortcutsWindow (axon-shortcuts/main.py:89-97)
**Bug:** No CSS loading guard at all. CSS provider created and registered on every instantiation.

**Severity:** Medium

### Apps with Proper Guards (Good Patterns)
- `axon-files/ui.py`: Module-level `_css_loaded` flag
- `axon-settings/main.py`: Class-level `AxonSettingsWindow._css_loaded`
- `axon-terminal/main.py`: Class-level `AxonTerminalWindow._css_loaded`
- `axon-voice-overlay/main.py`: Class-level `VoiceOverlay._css_loaded`

---

## 2. High: No Thread-Safe Cleanup for Streaming Background Threads

### AI Panel (panel.py:666-677)
```python
def _stream_response(self, text, ctx, model):
    ...
    for chunk in self._client.send_message_stream(...):
        GLib.idle_add(self._on_chunk, chunk)
    GLib.idle_add(self._on_stream_done, accumulated)
```
**Bug:** If the panel window is hidden via `toggle()` (set_visible(False)) while streaming is active, background thread callbacks continue firing via `GLib.idle_add`. The `_on_chunk` method doesn't check window visibility. The `_on_stream_done` callback also doesn't verify the window state before updating the spinner. This wastes resources and could cause visual artifacts when the panel is re-shown.

**Severity:** High
**Fix:** Check `self.get_visible()` in `_on_chunk` and `_on_stream_done`, and cancel the streaming thread when the window is hidden.

### Intent Bar (intent-bar/ui/window.py:467-488)
**Bug:** Same pattern — background streaming thread fires `GLib.idle_add` callbacks after the window is closed. `_finish_stream` tries to set text on widgets that may be in a destroyed state.

**Severity:** High

### Settings Executor (axon-settings/main.py:189-200)
```python
def worker():
    res = self._executor.execute_command(query)
    GLib.idle_add(self._on_command_completed, res)
threading.Thread(target=worker, daemon=True).start()
```
**Mitigated:** `_on_command_completed` checks `self.get_realized()` (line 203), which prevents crashes. However, the D-Bus call still completes even if the window is gone.

**Severity:** Low

---

## 3. Medium: VoiceOverlay Timer Handler Race Condition

### VoiceOverlay (voice-overlay/main.py:48-51)
```python
self._timer_id = GLib.timeout_add(16, self.on_tick)
self.connect(
    "destroy", lambda _: GLib.source_remove(self._timer_id) if self._timer_id else None
)
self.present()  # Line 54 — called AFTER handler connection
```
**Bug (minor):** The `destroy` signal handler is connected at line 49-51, but `self.present()` is called at line 54. If the window is somehow destroyed between these calls (extremely unlikely but possible with compositor-level events), the timer source would leak. The `self.present()` should ideally come before timer setup, or at minimum the handler should be connected first (which it is).

**Actual risk:** Very low. The ordering is acceptable.

### Timer ID Not Cleared After Removal
```python
lambda _: GLib.source_remove(self._timer_id) if self._timer_id else None
```
`self._timer_id` is never set to `None` after `source_remove()`. If somehow `destroy` fires twice (unlikely in GTK4), the second call would try to remove an already-removed source.

**Severity:** Very Low

---

## 4. Medium: No Cleanup of Signal Connections on Widget Destruction

### FilesWindow (axon-files/ui.py)
**No `destroy` handler exists.** The `file_list` has signal connections to `row-selected` and `row-activated`. The `sidebar_list` connects `row-selected`. While GTK4 cleans up controllers when a widget is destroyed, signal connections from the widget TO the listbox are not automatically disconnected if the callback holds a reference to the window.

**Severity:** Medium — the window is typically destroyed only once, but background threads (sync_thread) could fire callbacks after destruction. The `update_sync_progress` and `sync_completed` methods do check `self.get_realized()`, which mitigates this.

### AIPanelWindow (panel.py)
**No `destroy` handler.** When the panel is destroyed while a streaming thread is active, the thread continues running and attempts to call `GLib.idle_add(self._on_chunk, ...)` on a destroyed widget.

**Severity:** High — can cause GTK critical warnings or crashes.

### IntentBarWindow
**No `destroy` handler.** Same issue as AIPanelWindow with background streaming threads.

**Severity:** High

### WelcomeWindow (welcome.py:212-229)
**Properly handles cleanup.** Connects `destroy` signal and removes D-Bus signal receivers:
```python
def _on_destroy(self, _widget):
    try:
        self.bus.remove_signal_receiver(...)
    except Exception:
        pass
```
**Good pattern.** All other apps should follow this.

---

## 5. Medium: Deprecated API Usage — `get_style_context()`

Multiple apps use `widget.get_style_context().add_class()` which is deprecated since GTK 4.10:
- `axon-files/ui.py`: Lines 44, 63, 110, 117, 129, 135, 141, 149, etc. (extensive use)
- `axon-welcome/welcome.py`: The `_add_class()` helper wraps `get_style_context().add_class()`

**Should use:** `widget.add_css_class()` directly, which is the modern GTK4 API.

**Severity:** Medium — deprecated but still functional. Will eventually be removed.

---

## 6. Low: Multi-Monitor Handling Not Implemented

**None of the applications handle display/monitor configuration changes:**
- No app connects to `Gdk.Display::connect` / `Gdk.Display::disconnect` signals
- No app handles `Gdk.Monitor` geometry changes
- Window positioning is static

**Impact:**
- `IntentBarWindow` sets `set_default_size(660, -1)` but doesn't center itself on the current monitor
- `VoiceOverlay` is positioned statically without considering monitor bounds
- `AIPanelWindow` has fixed size but doesn't adapt to monitor changes

**Severity:** Low for most apps (GNOME Shell handles repositioning), but could be an issue on Wayland.

---

## 7. Low: Circular Reference Analysis

**No circular references found.** Key observations:
- `_TerminalTab` uses `__slots__` to minimize memory footprint (good)
- Widget ownership in GTK4 follows a tree model — parent owns children
- `SpacesManager` has no back-references to UI widgets
- `SettingsExecutor` holds D-Bus references that are cleaned up on GC

---

## 8. Informational: VoiceOverlay Cairo Drawing Surface

The `on_draw` method (line 63-96) receives a valid Cairo context from GTK4. GTK4 manages the surface lifecycle, so the draw function is safe. The animation loop properly uses `queue_draw()` to trigger redraws.

**No issue here** — GTK4 handles surface destruction/recreation automatically.

---

## Summary Table

| Issue | Severity | Apps Affected |
|-------|----------|---------------|
| CSS providers loaded per-instance | Critical | AIPanel, IntentBar, Sandbox, Welcome, Shortcuts |
| No streaming thread cancellation on close | High | AIPanel, IntentBar |
| No destroy handler for cleanup | High | AIPanel, IntentBar |
| Deprecated `get_style_context()` API | Medium | Files, Welcome |
| No multi-monitor handling | Low | All |
| Timer ID not cleared after removal | Very Low | VoiceOverlay |
| No circular references | N/A | All (clean) |
