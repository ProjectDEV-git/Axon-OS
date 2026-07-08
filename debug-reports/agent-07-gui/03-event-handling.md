# Sub-Task 3: Event Handling & Signal Connection Issues

**Agent 7 — GUI Applications Deep Debug**
**Date: 2026-07-02**

---

## 1. High: Ctrl+Shift+W (Close Tab) Not Implemented in Terminal

The shortcuts overlay (`axon-shortcuts/main.css` SHORTCUTS data) lists:
```
("Ctrl + Shift + W", "Close tab")
```

But the terminal's key handler (`axon-terminal/main.py:98-118`) only handles:
- `Ctrl+Shift+A` → toggle NL command bar
- `Ctrl+Shift+T` → new tab

**Ctrl+Shift+W is NOT implemented.** Users following the shortcuts overlay will press Ctrl+Shift+W and nothing will happen. Tab close is only available via the TabBar close button.

**Severity:** High (feature gap documented in shortcuts but missing)
**Fix:** Add Ctrl+Shift+W handler:
```python
if keyval in (Gdk.KEY_w, Gdk.KEY_W):
    page = self._terminal_widget._tab_view.get_selected_page()
    if page is not None:
        self._terminal_widget._tab_view.close_page(page)
    return True
```

---

## 2. High: Deprecated Gtk.Dialog Usage in Terminal Safety Prompt

In `terminal_widget.py:544`:
```python
dialog = Gtk.Dialog(
    title="Suspicious command detected", transient_for=transient, modal=True
)
dialog.add_button("Block", Gtk.ResponseType.CANCEL)
dialog.add_button("Allow Once", Gtk.ResponseType.YES)
dialog.add_button("Run Sandboxed", Gtk.ResponseType.OK)
```

**Bug:** `Gtk.Dialog` is deprecated in GTK4. While it still works, it may be removed in future GTK versions. The recommended replacement is `Adw.AlertDialog` or `Adw.MessageDialog`.

Additionally, `Gtk.Dialog` doesn't follow the libadwaita visual style, so this dialog will look visually inconsistent with the rest of the Axon Terminal UI.

**Severity:** High
**Fix:** Replace with `Adw.AlertDialog`:
```python
dialog = Adw.AlertDialog(
    heading="Suspicious command detected",
    body=format_findings(findings),
)
dialog.add_response("cancel", "Block")
dialog.add_response("yes", "Allow Once")
dialog.add_response("ok", "Run Sandboxed")
dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
```

---

## 3. High: Intent Bar — No Input Validation on `run_command` Action

In `intent-bar/ui/window.py:532-535`:
```python
elif action_type == "run_command":
    command: str = action.get("command", "")
    if command:
        safe_exec(command)
```

The `open_app` action is protected by `_validate_app_name()` with a strict regex, but `run_command` passes the AI-generated command directly to `safe_exec()`. The security of this depends entirely on what `safe_exec()` does. If `safe_exec()` only does basic sanitization, a compromised or hallucinating model could generate dangerous commands.

**Recommendation:** Add explicit command validation or at minimum a user confirmation dialog before executing AI-generated shell commands, similar to the terminal's `assess_command()` + dialog pattern.

**Severity:** High (security concern)

---

## 4. High: Terminal Tab Close Doesn't Kill Child Process

In `terminal_widget.py:329-337`:
```python
def _on_close_page(self, tab_view, page):
    self._tabs = [t for t in self._tabs if t.page is not page]
    tab_view.close_page_finish(page, True)
    if not self._tabs:
        self.new_tab()
    return True
```

**Bug:** When a tab is closed, the VTE terminal widget is removed from the TabView, but the child process spawned by VTE is not explicitly killed. VTE will send SIGHUP to the child process group when the terminal widget is destroyed, but `close_page_finish` only removes the page from the view — it doesn't necessarily destroy the VTE widget immediately.

If the VTE widget is still referenced elsewhere (e.g., by the tab tracking list before the list comprehension), the child process may continue running as an orphan.

**Mitigation:** The list comprehension `self._tabs = [t for t in self._tabs if t.page is not page]` removes the tab reference, which should allow GC to destroy the VTE widget and send SIGHUP. However, Python's GC timing is non-deterministic.

**Recommendation:** Explicitly terminate the child process:
```python
def _on_close_page(self, tab_view, page):
    tab = next((t for t in self._tabs if t.page is page), None)
    if tab and tab.pid > 0:
        try:
            os.kill(tab.pid, signal.SIGHUP)
        except (ProcessLookupError, OSError):
            pass
    self._tabs = [t for t in self._tabs if t.page is not page]
    tab_view.close_page_finish(page, True)
```

**Severity:** High

---

## 5. Medium: Settings Executor — Thread Race on Rapid Button Clicks

In `axon-settings/main.py:189-200`:
```python
def _run_command(self, query):
    self._feedback_text.set_text("Applying settings change...")
    self._feedback_card.add_css_class("loading")
    def worker():
        res = self._executor.execute_command(query)
        GLib.idle_add(self._on_command_completed, res)
    threading.Thread(target=worker, daemon=True).start()
```

**Bug:** If the user clicks a quick-action button multiple times rapidly, multiple background threads are spawned. Each thread calls `self._executor.execute_command(query)` which may re-initialize the D-Bus connection (`_connect()`) while another thread is using it. The `SettingsExecutor._connect()` method modifies `self._bus` and `self._brain` without any lock:
```python
def _connect(self):
    self._bus = dbus.SessionBus()  # Not thread-safe
    brain_obj = self._bus.get_object(...)
    self._brain = dbus.Interface(brain_obj, ...)
```

Additionally, multiple `_on_command_completed` callbacks could fire simultaneously, causing the UI to flicker between loading/success/error states.

**Severity:** Medium
**Fix:** Disable the entry/buttons while a command is executing, or use a lock to serialize access to the executor.

---

## 6. Medium: Intent Bar — Streaming Thread Not Cancellable

In `intent-bar/ui/window.py:467-472`:
```python
thread = threading.Thread(
    target=self._do_query,
    args=(query,),
    daemon=True,
)
thread.start()
```

If the user presses Escape to close the intent bar while streaming is in progress, the background thread continues running. When it calls `GLib.idle_add(self._append_token, token)`, the callback tries to update `self._response_label` on a window that's been closed but not destroyed.

**Impact:** Potential GTK critical warnings. Wasted network/processing resources.

**Severity:** Medium
**Fix:** Store a reference to the thread and set a cancellation flag. Check the flag in the streaming loop:
```python
self._stream_cancelled = threading.Event()
# In _do_query:
for token in ...
    if self._stream_cancelled.is_set():
        break
# On close:
self._stream_cancelled.set()
```

---

## 7. Medium: AI Panel — Streaming State Not Reset on Visibility Toggle

In `panel.py:696-701`:
```python
def toggle(self):
    if self.get_visible():
        self.set_visible(False)
    else:
        self.present()
```

When the panel is hidden via toggle, the streaming state (`self._streaming`, `self._stream_bubble`) is NOT reset. If the user toggles the panel off during streaming and then toggles it back on, the old stream bubble is still there, and the new stream would try to append to a stale reference.

**Severity:** Medium

---

## 8. Medium: Key Handling — Super+Space Intent Bar Activation

The intent bar itself does NOT register the Super+Space keyboard shortcut. This is handled externally (likely via GNOME Shell keybindings or a custom key daemon like `axon-keybinds`).

**Potential Issues:**
1. If the external key daemon is not running, Super+Space does nothing
2. If the intent bar is already visible and the user presses Super+Space again, it depends on the external daemon's toggle logic
3. No fallback mechanism exists within the intent bar code itself

**Severity:** Medium (architectural — depends on external component)

---

## 9. Low: Files App — Popover Created Per-Right-Click

In `axon-files/ui.py:699-701`:
```python
def show_context_menu(self, row, x, y):
    popover = Gtk.Popover()
    ...
```

**Bug:** A new `Gtk.Popover` is created every time the user right-clicks. While GTK4 will clean up the popover when it's closed, creating and destroying widgets on every interaction is wasteful.

**Fix:** Create the popover once in `__init__` and reuse it by updating its content.

**Severity:** Low

---

## 10. Low: Files App — Keyboard Handler Doesn't Filter Modifier Keys

In `axon-files/ui.py:660-689`:
```python
def on_key_pressed(self, controller, keyval, keycode, state):
    keyname = Gdk.keyval_name(keyval)
    is_ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0
    if is_ctrl:
        if keyname == "c":
            self.copy_file(...)
            return True
```

**Bug:** The handler checks for `is_ctrl` but doesn't verify that ONLY Ctrl is pressed. So `Ctrl+Shift+C` (which is typically copy in terminals) would also trigger file copy. This is actually correct behavior for a file manager, but it means the handler could interfere with GTK's own keyboard shortcuts if there are conflicts.

**Severity:** Low (works correctly for the intended use case)

---

## 11. Low: Welcome App — D-Bus Signal Handler Runs on Main Thread

In `welcome.py:491-496`:
```python
def _on_pull_progress(self, model_name, completed_bytes, total_bytes, status):
    if not self._downloading_model or model_name != self._downloading_model:
        return
    GLib.idle_add(self._update_pull_progress, completed_bytes, total_bytes, status)
```

The D-Bus signal handler is called on the GLib main loop thread (which is the GTK main thread). It then calls `GLib.idle_add()` which schedules ANOTHER callback on the main thread. This is a double-hop: signal fires → idle_add → callback. The `GLib.idle_add` is unnecessary here since `_on_pull_progress` is already on the main thread.

**Fix:** Call `_update_pull_progress` directly instead of via `GLib.idle_add`.

**Severity:** Low (functional but inefficient)

---

## 12. Low: Terminal — Ctrl+Shift+C/V Not Explicitly Handled

The shortcuts overlay lists:
```
("Ctrl + Shift + C", "Copy selection")
("Ctrl + Shift + V", "Paste")
```

The terminal's key handler (`main.py:98-118`) does NOT handle these. However, VTE terminals natively handle Ctrl+Shift+C/V for copy/paste, so these shortcuts should work by default. **No bug here** — VTE provides this behavior.

**Severity:** None (working correctly via VTE native handling)

---

## 13. Informational: Spaces Manager Thread Safety

`SpacesManager` (spaces_manager.py) reads/writes `spaces.json` using synchronous file I/O without any locking. If multiple processes (intent-bar, AI panel, etc.) access the same file simultaneously, data corruption could occur.

In practice, the SpacesManager is instantiated per-process and file writes are fast (small JSON), so the race window is very small. But it's architecturally fragile.

**Severity:** Informational

---

## Summary Table

| Issue | Severity | App |
|-------|----------|-----|
| Ctrl+Shift+W not implemented | High | Terminal |
| Deprecated Gtk.Dialog usage | High | Terminal |
| No user confirmation for AI commands | High | Intent Bar |
| Tab close doesn't kill child process | High | Terminal |
| Thread race on rapid settings clicks | Medium | Settings |
| Streaming not cancellable | Medium | Intent Bar |
| Stream state not reset on toggle | Medium | AI Panel |
| Super+Space depends on external daemon | Medium | Intent Bar |
| Popover created per right-click | Low | Files |
| Keyboard handler modifier filtering | Low | Files |
| Double-hop D-Bus signal handler | Low | Welcome |
| SpacesManager file locking | Info | Cross-app |
