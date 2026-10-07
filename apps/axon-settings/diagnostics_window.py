"""Diagnostics window for Axon Settings.

Runs the checks in ``services/diagnostics.py`` (the same ones behind the
``axon-diagnose`` command), shows each result with its fix, and lets the user
copy the report or save it to a file for a bug report.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

# Repo root in a checkout, /usr/lib/axon when installed: both hold services/.
_AXON_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _AXON_ROOT not in sys.path:
    sys.path.insert(0, _AXON_ROOT)
from services.diagnostics import FAIL, INFO, OK, WARN, CheckResult, Report, run_diagnostics

_STATUS_ICONS = {
    OK: "emblem-ok-symbolic",
    INFO: "dialog-information-symbolic",
    WARN: "dialog-warning-symbolic",
    FAIL: "dialog-error-symbolic",
}
_STATUS_CLASSES = {OK: "success", INFO: "dim-label", WARN: "warning", FAIL: "error"}


class DiagnosticsWindow(Adw.ApplicationWindow):
    """Shows a diagnostics report and lets the user copy or save it."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Diagnostics")
        self.set_default_size(620, 680)
        self._report: Report | None = None

        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        header = Adw.HeaderBar()
        self._run_btn = Gtk.Button(label="Run again")
        self._run_btn.connect("clicked", lambda _b: self.run())
        header.pack_start(self._run_btn)
        self._save_btn = Gtk.Button(icon_name="document-save-symbolic")
        self._save_btn.set_tooltip_text("Save report to a file")
        self._save_btn.connect("clicked", self._on_save)
        header.pack_end(self._save_btn)
        self._copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self._copy_btn.set_tooltip_text("Copy report")
        self._copy_btn.connect("clicked", self._on_copy)
        header.pack_end(self._copy_btn)
        toolbar.add_top_bar(header)

        self._page = Adw.PreferencesPage()
        toolbar.set_content(self._page)
        self._groups: list[Adw.PreferencesGroup] = []

        self.run()

    # -- running -----------------------------------------------------------
    def run(self) -> None:
        """Run every check in the background and show the results."""
        self._set_busy(True)
        self._replace_groups([self._status_group("Running checks…", None)])

        def worker() -> None:
            report = run_diagnostics()
            GLib.idle_add(self._show_report, report)

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._run_btn.set_sensitive(not busy)
        self._copy_btn.set_sensitive(not busy)
        self._save_btn.set_sensitive(not busy)

    def _show_report(self, report: Report) -> bool:
        self._report = report
        self._set_busy(False)
        counts = report.counts()
        if report.status == FAIL:
            title = f"{counts[FAIL]} problem(s) found"
        elif report.status == WARN:
            title = f"{counts[WARN]} warning(s)"
        else:
            title = "Everything looks good"
        groups = [self._status_group(title, report)]

        by_category: dict[str, list[CheckResult]] = {}
        for result in report.results:
            by_category.setdefault(result.category, []).append(result)
        for category, results in by_category.items():
            group = Adw.PreferencesGroup(title=category)
            for result in results:
                group.add(self._result_row(result))
            groups.append(group)
        self._replace_groups(groups)
        return False

    # -- widgets -----------------------------------------------------------
    def _replace_groups(self, groups: list[Adw.PreferencesGroup]) -> None:
        for group in self._groups:
            self._page.remove(group)
        self._groups = groups
        for group in groups:
            self._page.add(group)

    @staticmethod
    def _status_group(title: str, report: Report | None) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=title)
        if report is None:
            spinner = Gtk.Spinner()
            spinner.start()
            row.add_prefix(spinner)
        else:
            row.set_subtitle(
                " · ".join(f"{k}: {v}" for k, v in report.system.items()) + f"\n{report.created}"
            )
            icon = Gtk.Image.new_from_icon_name(_STATUS_ICONS[report.status])
            icon.add_css_class(_STATUS_CLASSES[report.status])
            row.add_prefix(icon)
        group.add(row)
        return group

    @staticmethod
    def _result_row(result: CheckResult) -> Gtk.Widget:
        icon = Gtk.Image.new_from_icon_name(_STATUS_ICONS.get(result.status, _STATUS_ICONS[INFO]))
        icon.add_css_class(_STATUS_CLASSES.get(result.status, "dim-label"))
        lines = list(result.details)
        if result.hint and result.status in (WARN, FAIL):
            lines.append(f"Fix: {result.hint}")

        if not lines:
            row = Adw.ActionRow(title=result.name, subtitle=result.summary)
            row.add_prefix(icon)
            return row

        expander = Adw.ExpanderRow(title=result.name, subtitle=result.summary)
        expander.add_prefix(icon)
        expander.set_expanded(result.status in (WARN, FAIL))
        for line in lines:
            label = Gtk.Label(label=line, xalign=0, wrap=True, selectable=True)
            label.set_margin_start(12)
            label.set_margin_end(12)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            expander.add_row(label)
        return expander

    # -- export ------------------------------------------------------------
    def _on_copy(self, _btn: Gtk.Button) -> None:
        if self._report is None:
            return
        Gdk.Display.get_default().get_clipboard().set(self._report.to_text())
        self._toasts.add_toast(Adw.Toast(title="Report copied"))

    def _on_save(self, _btn: Gtk.Button) -> None:
        if self._report is None:
            return
        dialog = Gtk.FileDialog(title="Save diagnostics report")
        dialog.set_initial_name(
            f"axon-diagnostics-{GLib.DateTime.new_now_local().format('%Y%m%d-%H%M')}.txt"
        )
        dialog.save(self, None, self._on_save_done)

    def _on_save_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return  # cancelled
        if file is None or self._report is None:
            return
        path = file.get_path()
        text = self._report.to_json() if path and path.endswith(".json") else self._report.to_text()
        try:
            Path(path).write_text(text)
        except (OSError, TypeError) as exc:
            self._toasts.add_toast(Adw.Toast(title=f"Could not save: {exc}"))
            return
        self._toasts.add_toast(Adw.Toast(title=f"Saved to {file.get_basename()}"))
