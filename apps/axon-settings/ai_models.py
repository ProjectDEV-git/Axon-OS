"""
ai_models.py — "AI Models" preferences for Axon Settings.

Lets the user pick any model (local Ollama or a cloud provider) for each
routing tier, add API keys, sign in with OAuth where the provider allows it,
and add custom OpenAI-compatible, Anthropic or Gemini endpoints (for example
a company gateway). Everything goes through the org.axonos.Brain D-Bus
service; keys and tokens stay in the system keyring and never pass back here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

TIERS = (
    ("general", "Everyday model", "Chat, questions and writing"),
    ("speed", "Fast model", "Quick commands and window sorting"),
    ("deep", "Deep model", "Code and long reasoning"),
)
KIND_LABELS = (
    ("openai", "OpenAI-compatible"),
    ("anthropic", "Anthropic"),
    ("gemini", "Google Gemini"),
)
AUTH_LABELS = (
    ("bearer", "API key (Authorization: Bearer)"),
    ("api-key", "API key (api-key header, Azure)"),
    ("none", "No key (local server)"),
)
FLOW_LABELS = (
    ("", "No sign-in"),
    ("pkce", "Browser sign-in"),
    ("device", "Device code sign-in"),
)


class BrainClient:
    """Thin JSON wrapper over the Brain service's provider methods."""

    def __init__(self) -> None:
        self._proxy: Any = None

    def _brain(self) -> Any:
        if self._proxy is None:
            import dbus

            self._proxy = dbus.SessionBus().get_object("org.axonos.Brain", "/org/axonos/Brain")
        return self._proxy

    def call(self, method: str, *args: Any) -> Any:
        result = getattr(self._brain(), method)(*args, dbus_interface="org.axonos.Brain")
        if isinstance(result, bool):
            return result
        return json.loads(str(result))


def run_async(fn: Callable[[], Any], done: Callable[[Any, Exception | None], None]) -> None:
    """Run *fn* off the UI thread and hand its result to *done* on the UI thread."""

    def worker() -> None:
        try:
            result, error = fn(), None
        except Exception as e:  # D-Bus errors, service down
            result, error = None, e

        def deliver() -> bool:
            done(result, error)
            return False

        GLib.idle_add(deliver)

    threading.Thread(target=worker, daemon=True).start()


def open_uri(parent: Gtk.Window, uri: str) -> None:
    if hasattr(Gtk, "UriLauncher"):
        Gtk.UriLauncher.new(uri).launch(parent, None, None, None)
    else:
        Gio.AppInfo.launch_default_for_uri(uri, None)


def _combo(options: tuple[tuple[str, str], ...], selected: str) -> Adw.ComboRow:
    row = Adw.ComboRow()
    row.set_model(Gtk.StringList.new([label for _key, label in options]))
    keys = [key for key, _label in options]
    row.set_selected(keys.index(selected) if selected in keys else 0)
    return row


def _combo_value(row: Adw.ComboRow, options: tuple[tuple[str, str], ...]) -> str:
    return options[row.get_selected()][0]


class ProviderDialog(Adw.Window):
    """Add a custom provider, or edit a provider's URL, models and sign-in."""

    def __init__(
        self,
        parent: Gtk.Window,
        provider: dict[str, Any] | None,
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_default_size(480, 640)
        self._provider = provider or {}
        self._on_save = on_save
        is_new = provider is None
        builtin = bool(self._provider.get("builtin"))
        self.set_title("Add AI provider" if is_new else f"Edit {self._provider.get('label')}")

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save_clicked)
        header.pack_end(save)

        page = Adw.PreferencesPage()
        toolbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar.append(header)
        toolbar.append(page)
        page.set_vexpand(True)
        self.set_content(toolbar)

        basics = Adw.PreferencesGroup(title="Provider")
        page.add(basics)
        self._label = Adw.EntryRow(title="Name")
        self._label.set_text(self._provider.get("label", ""))
        basics.add(self._label)
        self._id = Adw.EntryRow(title="Short ID (used in model names, e.g. work)")
        self._id.set_text(self._provider.get("id", ""))
        self._id.set_sensitive(is_new)
        basics.add(self._id)
        self._kind = _combo(KIND_LABELS, self._provider.get("kind", "openai"))
        self._kind.set_title("API type")
        self._kind.set_sensitive(is_new)
        basics.add(self._kind)
        self._base_url = Adw.EntryRow(title="Base URL (e.g. https://api.example.com/v1)")
        self._base_url.set_text(self._provider.get("base_url", ""))
        basics.add(self._base_url)
        self._auth = _combo(AUTH_LABELS, self._provider.get("auth_header", "bearer"))
        self._auth.set_title("Key style")
        basics.add(self._auth)
        self._models = Adw.EntryRow(title="Extra model names, comma-separated (optional)")
        self._models.set_text(", ".join(self._provider.get("models", [])))
        basics.add(self._models)

        oauth = self._provider.get("oauth") or {}
        sign_in = Adw.PreferencesGroup(
            title="Company or provider sign-in",
            description=(
                "For identity providers that allow it (Microsoft Entra ID, Google, Okta…). "
                "Your IT team or the provider gives you the issuer and client ID."
            ),
        )
        sign_in.set_visible(oauth.get("flow") != "openrouter")
        page.add(sign_in)
        self._flow = _combo(FLOW_LABELS, oauth.get("flow", ""))
        self._flow.set_title("Sign-in method")
        self._flow.set_sensitive(not builtin)
        sign_in.add(self._flow)
        self._issuer = Adw.EntryRow(
            title="Issuer URL (e.g. https://login.microsoftonline.com/TENANT/v2.0)"
        )
        self._issuer.set_text(oauth.get("issuer", ""))
        sign_in.add(self._issuer)
        self._client_id = Adw.EntryRow(title="Client ID")
        self._client_id.set_text(oauth.get("client_id", ""))
        sign_in.add(self._client_id)
        self._client_secret = Adw.PasswordEntryRow(
            title="Client secret (only for desktop-app clients that issue one)"
        )
        sign_in.add(self._client_secret)
        self._scopes = Adw.EntryRow(title="Scopes, space-separated")
        self._scopes.set_text(" ".join(oauth.get("scopes", [])))
        sign_in.add(self._scopes)

        self._kind.connect("notify::selected", lambda *_a: self._sync_visibility())
        self._flow.connect("notify::selected", lambda *_a: self._sync_visibility())
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        self._auth.set_visible(_combo_value(self._kind, KIND_LABELS) == "openai")
        has_flow = _combo_value(self._flow, FLOW_LABELS) != "" or bool(
            (self._provider.get("oauth") or {}).get("flow")
        )
        for row in (self._issuer, self._client_id, self._client_secret, self._scopes):
            row.set_visible(has_flow)

    def _on_save_clicked(self, _button: Gtk.Button) -> None:
        data: dict[str, Any] = {
            "id": self._id.get_text().strip().lower(),
            "label": self._label.get_text().strip(),
            "kind": _combo_value(self._kind, KIND_LABELS),
            "base_url": self._base_url.get_text().strip(),
            "models": [m.strip() for m in self._models.get_text().split(",") if m.strip()],
        }
        if data["kind"] == "openai":
            data["auth_header"] = _combo_value(self._auth, AUTH_LABELS)
        old_oauth = self._provider.get("oauth") or {}
        flow = old_oauth.get("flow", "") if self._provider.get("builtin") else ""
        flow = flow or _combo_value(self._flow, FLOW_LABELS)
        if flow == "openrouter":
            data["oauth"] = old_oauth
        elif flow:
            oauth = {**old_oauth, "flow": flow}
            oauth["issuer"] = self._issuer.get_text().strip()
            oauth["client_id"] = self._client_id.get_text().strip()
            oauth["scopes"] = self._scopes.get_text().split()
            secret = self._client_secret.get_text().strip()
            if secret:
                oauth["client_secret"] = secret
            data["oauth"] = oauth
        else:
            data["oauth"] = {}
        self._on_save(data)
        self.close()


class AIModelsWindow(Adw.PreferencesWindow):
    """Pick models and set up AI providers."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_title("AI Models")
        self.set_default_size(640, 720)
        self._client = BrainClient()
        self._poll_ids: dict[str, int] = {}
        self._code_dialogs: dict[str, Adw.MessageDialog] = {}
        self._models: list[dict[str, Any]] = []
        self._model_settings: dict[str, str] = {}
        self.connect("close-request", self._on_close)

        self._models_page = Adw.PreferencesPage(title="Models", icon_name="starred-symbolic")
        self._providers_page = Adw.PreferencesPage(
            title="Providers", icon_name="network-server-symbolic"
        )
        self.add(self._models_page)
        self.add(self._providers_page)
        self._models_groups: list[Adw.PreferencesGroup] = []
        self._provider_groups: list[Adw.PreferencesGroup] = []
        self.refresh()

    # -- data --------------------------------------------------------

    def refresh(self, refresh_models: bool = False) -> None:
        def load() -> tuple[Any, Any, Any]:
            return (
                self._client.call("ListProviders"),
                self._client.call("ListAllModels", refresh_models),
                self._client.call("GetModelSettings"),
            )

        run_async(load, self._on_loaded)

    def _on_loaded(self, result: Any, error: Exception | None) -> None:
        if error is not None:
            self._show_offline(error)
            return
        providers, models, tiers = result
        self._models = models.get("models", [])
        self._model_settings = tiers
        self._build_models_page(models.get("errors", {}), providers.get("providers", []))
        self._build_providers_page(providers)

    def _clear(self, page: Adw.PreferencesPage, groups: list[Adw.PreferencesGroup]) -> None:
        for group in groups:
            page.remove(group)
        groups.clear()

    def _add_group(
        self, page: Adw.PreferencesPage, groups: list[Adw.PreferencesGroup], **kw: Any
    ) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(**kw)
        page.add(group)
        groups.append(group)
        return group

    def _show_offline(self, error: Exception) -> None:
        self._clear(self._models_page, self._models_groups)
        group = self._add_group(
            self._models_page,
            self._models_groups,
            title="AI service not running",
            description=f"Axon Brain did not answer ({error}). Start it with "
            "'systemctl --user start axon-brain' and reopen this window.",
        )
        retry = Gtk.Button(label="Try again", valign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _b: self.refresh())
        group.set_header_suffix(retry)

    # -- models page -----------------------------------------------------

    def _build_models_page(self, errors: dict[str, str], providers: list[dict[str, Any]]) -> None:
        self._clear(self._models_page, self._models_groups)
        group = self._add_group(
            self._models_page,
            self._models_groups,
            title="Which model Axon uses",
            description="Pick any local or cloud model. Cloud models appear once a "
            "provider is set up on the Providers page.",
        )
        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER)
        reload_btn.set_tooltip_text("Reload model lists")
        reload_btn.connect("clicked", lambda _b: self.refresh(refresh_models=True))
        group.set_header_suffix(reload_btn)

        ids = [m["id"] for m in self._models]
        labels = [f"{m['name']}  ·  {m['provider_label']}" for m in self._models]
        for tier, title, subtitle in TIERS:
            current = self._model_settings.get(f"{tier}_model", "")
            tier_ids, tier_labels = list(ids), list(labels)
            if current and current not in tier_ids:
                tier_ids.insert(0, current)
                tier_labels.insert(0, f"{current}  ·  not available right now")
            row = Adw.ComboRow(title=title, subtitle=subtitle)
            row.set_model(Gtk.StringList.new(tier_labels or ["No models found"]))
            row.set_enable_search(True)
            if current in tier_ids:
                row.set_selected(tier_ids.index(current))
            row.set_sensitive(bool(tier_ids))
            row.connect("notify::selected", self._on_tier_selected, tier, tier_ids)
            group.add(row)

        labels_by_id = {p["id"]: p["label"] for p in providers}
        if errors:
            problems = self._add_group(
                self._models_page, self._models_groups, title="Providers with problems"
            )
            for pid, message in errors.items():
                row = Adw.ActionRow(
                    title=labels_by_id.get(pid, pid), subtitle=message, use_markup=False
                )
                row.set_subtitle_lines(3)
                row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
                problems.add(row)

    def _on_tier_selected(self, row: Adw.ComboRow, _pspec: Any, tier: str, ids: list[str]) -> None:
        index = row.get_selected()
        if index >= len(ids) or ids[index] == self._model_settings.get(f"{tier}_model"):
            return
        model = ids[index]

        def done(result: Any, error: Exception | None) -> None:
            if error is not None or not result.get("ok"):
                self._toast(f"Could not switch model: {error or result.get('error')}")
                return
            self._model_settings = result["models"]
            self._toast(f"Now using {model}")

        run_async(lambda: self._client.call("SetModel", tier, model), done)

    # -- providers page --------------------------------------------------

    def _build_providers_page(self, data: dict[str, Any]) -> None:
        self._clear(self._providers_page, self._provider_groups)
        if not data.get("keyring_available", True):
            self._add_group(
                self._providers_page,
                self._provider_groups,
                title="No keyring available",
                description="Axon keeps keys and sign-ins in the system keyring, which is "
                "not running. Keys can still come from environment variables.",
            )
        group = self._add_group(
            self._providers_page,
            self._provider_groups,
            title="AI providers",
            description="Keys and sign-ins are stored in your keyring, never in a file.",
        )
        add_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_btn.set_tooltip_text("Add a provider or company endpoint")
        add_btn.connect("clicked", lambda _b: self._edit_provider(None))
        group.set_header_suffix(add_btn)
        for provider in data.get("providers", []):
            group.add(self._provider_row(provider))

    def _provider_row(self, p: dict[str, Any]) -> Adw.ExpanderRow:
        # provider names, URLs and errors are user/server text, not Pango markup
        row = Adw.ExpanderRow(title=p["label"], subtitle=self._provider_state(p), use_markup=False)
        if p["kind"] == "ollama":
            info = Adw.ActionRow(title="Address", subtitle=p["base_url"], use_markup=False)
            row.add_row(info)
            return row

        if p.get("auth_header") != "none":
            key_row = Adw.PasswordEntryRow(
                title="API key (saved)" if p.get("has_api_key") else "API key"
            )
            key_row.set_show_apply_button(True)
            key_row.connect("apply", self._on_key_apply, p)
            row.add_row(key_row)

        if p.get("oauth_ready") or p.get("signed_in"):
            sign_row = Adw.ActionRow(
                title="Signed in" if p.get("signed_in") else "Sign in instead of a key",
                subtitle=self._flow_hint(p.get("oauth_flow", "")),
            )
            if p.get("signed_in"):
                btn = Gtk.Button(label="Sign out", valign=Gtk.Align.CENTER)
                btn.connect(
                    "clicked", lambda _b: self._simple("SignOut", p["id"], toast="Signed out")
                )
            else:
                btn = Gtk.Button(label="Sign in", valign=Gtk.Align.CENTER)
                btn.add_css_class("suggested-action")
                btn.connect("clicked", lambda _b: self._start_sign_in(p))
            sign_row.add_suffix(btn)
            row.add_row(sign_row)
        elif p.get("oauth_flow") and not p.get("oauth_ready"):
            hint = Adw.ActionRow(
                title="Sign-in available",
                subtitle="Add an OAuth client ID with Edit to enable sign-in.",
            )
            row.add_row(hint)

        actions = Adw.ActionRow(title=p["base_url"], use_markup=False)
        actions.set_title_lines(1)
        edit = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
        edit.connect("clicked", lambda _b: self._edit_provider(p))
        actions.add_suffix(edit)
        remove = Gtk.Button(label="Reset" if p.get("builtin") else "Remove")
        remove.set_valign(Gtk.Align.CENTER)
        remove.add_css_class("destructive-action")
        remove.connect("clicked", lambda _b: self._confirm_remove(p))
        actions.add_suffix(remove)
        row.add_row(actions)
        return row

    @staticmethod
    def _provider_state(p: dict[str, Any]) -> str:
        if p["kind"] == "ollama":
            return "Runs on this computer"
        if not p.get("enabled", True):
            return "Turned off"
        if p.get("signed_in"):
            return "Signed in"
        if p.get("has_api_key"):
            return "API key saved"
        if p.get("auth_header") == "none":
            return "No key needed"
        return "Not set up"

    @staticmethod
    def _flow_hint(flow: str) -> str:
        return {
            "openrouter": "Opens OpenRouter in your browser and creates a key for Axon",
            "pkce": "Opens the provider's sign-in page in your browser",
            "device": "Shows a code to enter on the sign-in page",
        }.get(flow, "")

    def _on_key_apply(self, entry: Adw.PasswordEntryRow, p: dict[str, Any]) -> None:
        key = entry.get_text()
        entry.set_text("")
        message = "API key saved" if key.strip() else "API key removed"
        self._simple("SetApiKey", p["id"], key, toast=message)

    def _simple(self, method: str, *args: Any, toast: str = "") -> None:
        def done(result: Any, error: Exception | None) -> None:
            if error is not None or not result.get("ok"):
                self._toast(str(error or result.get("error")))
            elif toast:
                self._toast(toast)
            self.refresh(refresh_models=True)

        run_async(lambda: self._client.call(method, *args), done)

    def _edit_provider(self, p: dict[str, Any] | None) -> None:
        def save(data: dict[str, Any]) -> None:
            self._simple("SaveProvider", json.dumps(data), toast="Provider saved")

        ProviderDialog(self, p, save).present()

    def _confirm_remove(self, p: dict[str, Any]) -> None:
        verb = "Reset" if p.get("builtin") else "Remove"
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"{verb} {p['label']}?",
            body="Its saved key and sign-in are deleted from the keyring.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", verb)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response == "ok":
                self._simple("RemoveProvider", p["id"], toast=f"{p['label']} {verb.lower()} done")

        dialog.connect("response", on_response)
        dialog.present()

    # -- sign-in -----------------------------------------------------------

    def _start_sign_in(self, p: dict[str, Any]) -> None:
        pid = p["id"]

        def started(result: Any, error: Exception | None) -> None:
            if error is not None or not result.get("ok"):
                self._toast(f"Sign-in could not start: {error or result.get('error')}")
                return
            self._toast(f"Signing in to {p['label']}…")
            self._stop_poll(pid)
            self._poll_ids[pid] = GLib.timeout_add(1000, self._poll_sign_in, p, set())

        run_async(lambda: self._client.call("StartSignIn", pid), started)

    def _poll_sign_in(self, p: dict[str, Any], seen: set[str]) -> bool:
        pid = p["id"]

        def done(status: Any, error: Exception | None) -> None:
            if pid not in self._poll_ids:
                return  # finished or window closed while this call was in flight
            if error is not None:
                self._finish_sign_in(p, f"Sign-in failed: {error}")
                return
            state = status.get("state")
            if state == "awaiting_user" and "prompted" not in seen:
                seen.add("prompted")
                self._prompt_user(p, status)
            elif state == "done":
                self._finish_sign_in(p, f"Signed in to {p['label']}")
            elif state == "error":
                self._finish_sign_in(p, f"Sign-in failed: {status.get('error')}")

        run_async(lambda: self._client.call("GetSignInStatus", pid), done)
        return True

    def _prompt_user(self, p: dict[str, Any], status: dict[str, Any]) -> None:
        if status.get("auth_url"):
            open_uri(self, status["auth_url"])
            return
        uri = status.get("verification_uri_complete") or status.get("verification_uri", "")
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Sign in to {p['label']}",
            body=f"Open {status.get('verification_uri', '')} and enter this code:\n\n"
            f"{status.get('user_code', '')}",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("open", "Open sign-in page")
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response == "open" and uri:
                open_uri(self, uri)
            elif response == "cancel":
                run_async(lambda: self._client.call("CancelSignIn", p["id"]), lambda *_a: None)

        dialog.connect("response", on_response)
        self._code_dialogs[p["id"]] = dialog
        dialog.present()

    def _finish_sign_in(self, p: dict[str, Any], message: str) -> None:
        self._stop_poll(p["id"])
        dialog = self._code_dialogs.pop(p["id"], None)
        if dialog is not None:
            dialog.close()
        self._toast(message)
        self.refresh(refresh_models=True)

    def _stop_poll(self, pid: str) -> None:
        source = self._poll_ids.pop(pid, None)
        if source is not None:
            GLib.source_remove(source)

    def _toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def _on_close(self, _window: Gtk.Window) -> bool:
        for pid in list(self._poll_ids):
            self._stop_poll(pid)
        return False
