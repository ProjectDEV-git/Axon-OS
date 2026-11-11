"""panel_context.py — Axon status section, desktop-context row and the cloud-send warning.

Mixed into ``AIPanelWindow`` (see ``panel.py``); methods rely on its widgets/state.
"""

from __future__ import annotations

import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Importing panel_compat first puts services/ and apps/shared on sys.path.
import panel_compat  # noqa: F401
from gi.repository import Adw, GLib, Gtk

# isort: split
from context_settings import ITEM_LABELS, SENSITIVE_ITEMS, sends_sensitive
from diagnostics import FAIL, INFO, OK, WARN
from i18n import _
from status_summary import build_status
from ui_logic import (
    origin_tooltip,
)

_STATUS_ICONS = {
    OK: "emblem-ok-symbolic",
    INFO: "dialog-information-symbolic",
    WARN: "dialog-warning-symbolic",
    FAIL: "dialog-error-symbolic",
}
# Text next to each icon, so status never relies on colour alone
_STATUS_WORDS = {OK: "OK", INFO: "Info", WARN: "Warning", FAIL: "Problem"}


class ContextMixin:
    """Axon status section, desktop-context row and the cloud-send warning."""

    def _refresh_status(self) -> None:
        """Re-run the services/AI diagnostics checks in a worker."""
        if self._status_loading:
            return
        self._status_loading = True
        threading.Thread(target=self._load_status_worker, daemon=True).start()

    def _show_status(self, results: list[Any], stats: Any, pull: Any) -> bool:
        self._status_loading = False
        summary = build_status(results, self.current_origin().label, stats, pull)
        self._status = summary
        self._status_icon.set_from_icon_name(_STATUS_ICONS[summary.status])
        self._status_headline.set_text(
            _("Axon status: {headline}").format(headline=summary.headline)
        )
        self._status_list.remove_all()
        for row in summary.rows:
            line = Adw.ActionRow(title=row.name, subtitle=row.text)
            line.set_use_markup(False)
            icon = Gtk.Image.new_from_icon_name(_STATUS_ICONS[row.status])
            icon.set_tooltip_text(_STATUS_WORDS[row.status])
            icon.update_property([Gtk.AccessibleProperty.LABEL], [_STATUS_WORDS[row.status]])
            line.add_prefix(icon)
            self._status_list.append(line)
        problem = summary.problem
        if problem is not None and problem.hint:
            self._status_fix.set_text(_("Fix: {hint}").format(hint=problem.hint))
            self._status_fix.set_visible(True)
        else:
            self._status_fix.set_visible(False)
        # Open the section by itself when something is wrong
        if summary.status == FAIL:
            self._status_expander.set_expanded(True)
        return False

    def _refresh_context(self) -> None:
        """Reload what context would be sent (D-Bus, so in a worker)."""
        if not hasattr(self._ctx_reader, "snapshot"):
            self._show_context(None, [], True)
            return
        threading.Thread(target=self._load_context_worker, daemon=True).start()

    def _load_context_worker(self) -> None:
        try:
            snapshot = self._ctx_reader.snapshot()
            items = self._ctx_reader.context_items(snapshot)
            enabled = self._ctx_reader.settings().share_context
        except Exception:
            snapshot, items, enabled = None, [], True
        GLib.idle_add(self._show_context, snapshot, items, enabled)

    def _show_context(
        self, snapshot: dict[str, Any] | None, items: list[tuple[str, str]], enabled: bool
    ) -> bool:
        self._ctx_snapshot = snapshot
        self._ctx_items = items
        self._ctx_enabled = enabled
        self._render_context()
        return False

    def _render_context(self) -> None:
        self._ctx_flow.remove_all()
        shown = [(k, d) for k, d in self._ctx_items if k not in self._ctx_excluded]
        if not self._ctx_enabled:
            self._ctx_title.set_text(_("Context: off"))
        elif not shown:
            self._ctx_title.set_text(_("Context: none"))
        else:
            self._ctx_title.set_text(_("Context:"))
        for key, desc in shown:
            chip = Gtk.Button()
            chip.add_css_class("context-chip")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            box.append(Gtk.Label(label=desc))
            box.append(Gtk.Image.new_from_icon_name("window-close-symbolic"))
            chip.set_child(box)
            what = _(ITEM_LABELS.get(key, key)).lower()
            chip.set_tooltip_text(_("Don't send {item} with this message").format(item=what))
            chip.update_property(
                [Gtk.AccessibleProperty.LABEL], [_("Don't send {item}").format(item=desc)]
            )
            chip.connect("clicked", lambda _b, k=key: self._exclude_context(k))
            self._ctx_flow.append(chip)

    def _exclude_context(self, key: str) -> None:
        self._ctx_excluded.add(key)
        self._render_context()

    def _needs_cloud_warning(self) -> bool:
        """Warn once per conversation before sensitive context leaves the computer."""
        return (
            self.current_origin().leaves_computer
            and self._cloud_ack_conv != self._conv_id
            and sends_sensitive(self._ctx_items, self._ctx_excluded)
        )

    def _confirm_cloud_send(self, text: str) -> None:
        origin = self.current_origin()
        labels = [
            _(ITEM_LABELS[k]).lower()
            for k, _d in self._ctx_items
            if k in SENSITIVE_ITEMS and k not in self._ctx_excluded
        ]
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Send desktop context to the cloud?"),
            body=(
                f"{origin_tooltip(origin)} This message would include your "
                f"{', '.join(labels)}. You won't be asked again in this conversation."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("without", _("Send Without It"))
        dialog.add_response("send", _("Send"))
        dialog.set_response_appearance("send", Adw.ResponseAppearance.DESTRUCTIVE)
        # Closing the dialog sends nothing
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_cloud_response, text)
        dialog.present()

    def _on_cloud_response(self, _dialog: Adw.MessageDialog, response: str, text: str) -> None:
        if response == "send":
            self._cloud_ack_conv = self._conv_id
            self._submit(text)
        elif response == "without":
            self._ctx_excluded |= SENSITIVE_ITEMS
            self._submit(text)
