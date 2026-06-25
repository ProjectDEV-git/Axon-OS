"""Axon OS Installer — InstallerWindow.

A full welcome + installation wizard shown on first boot of the live ISO:
Welcome → Network → Identity → Disk → AI Setup → Summary → Install → Done.

The UI runs unprivileged; the actual installation is performed by
install_engine.py launched as root (sudo -n on the live session, pkexec
otherwise) and reporting progress over stdout.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

ENGINE_PATH = Path(__file__).resolve().parents[1] / "install_engine.py"
ENGINE_WRAPPER = "/usr/local/bin/axon-install-engine"

USERNAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")


def _os_version() -> str:
    """Version + codename string from /etc/axon-release, with a safe default."""
    version, codename = "0.3.0", "Pulse"
    try:
        for line in Path("/etc/axon-release").read_text().splitlines():
            key, _, value = line.partition("=")
            if key == "AXON_VERSION" and value.strip():
                version = value.strip()
            elif key == "AXON_CODENAME" and value.strip():
                codename = value.strip()
    except OSError:
        pass
    return f'{version} "{codename}"'


OLLAMA_MODELS = [
    ("llama3.2:1b", "Llama 3.2 1B", "1.3 GB — fastest, great for the Intent Bar"),
    ("llama3.2:3b", "Llama 3.2 3B", "2.0 GB — recommended balance (default)"),
    ("llama3:8b", "Llama 3 8B", "4.7 GB — deeper reasoning, needs 8 GB+ RAM"),
    ("mistral:7b", "Mistral 7B", "4.1 GB — strong general assistant"),
    ("phi3:mini", "Phi-3 Mini", "2.2 GB — compact Microsoft model"),
    ("qwen2.5:7b", "Qwen 2.5 7B", "4.7 GB — excellent coding ability"),
]

CLOUD_PROVIDERS = [
    ("anthropic", "Anthropic Claude", "Claude Sonnet and Opus models"),
    ("openai", "OpenAI", "GPT-4o family"),
    ("google", "Google Gemini", "Gemini 2.0 models"),
    ("openrouter", "OpenRouter", "One key, every major model"),
]

CLOUD_PROVIDERS_BY_ID = [(pid, name) for pid, name, _desc in CLOUD_PROVIDERS]

INSTALL_TIPS = [
    "Press Super+Space anywhere to open the Intent Bar — just type what you want done.",
    "Press Super+A to slide out the AI Panel, your always-available assistant.",
    "Your local AI runs entirely on this machine. No prompt ever leaves it unless you add a cloud provider.",
    "Super+1 through Super+9 jump between named Spaces: Code, Web, Chat, Files and more.",
    "Axon Brain picks the right model per task — fast ones for commands, big ones for reasoning.",
    "You can add or change AI providers any time in Axon Settings.",
    "The Context service understands what you're working on, so the AI answers with awareness.",
]

_CSS = b"""
.installer-window { background-color: #0a0a11; }
.sidebar {
    background-color: #0d0d15;
    border-right: 1px solid #1d1d2e;
}
.sidebar-logo { font-size: 40px; color: #8b5cf6; }
.sidebar-product { font-size: 18px; font-weight: bold; color: #e8e8f4; }
.sidebar-version { font-size: 11px; color: #50507a; }
.step-row { padding: 8px 14px; border-radius: 10px; }
.step-row-current { background-color: rgba(139, 92, 246, 0.14); }
.step-label { font-size: 13px; color: #70709a; }
.step-label-current { color: #e8e8f4; font-weight: bold; }
.step-label-done { color: #8b5cf6; }
.step-dot { font-size: 10px; color: #34344e; }
.step-dot-current { color: #8b5cf6; }
.step-dot-done { color: #10b981; }
.hero-logo { font-size: 64px; color: #8b5cf6; }
.hero-title { font-size: 34px; font-weight: bold; color: #e8e8f4; }
.hero-subtitle { font-size: 15px; color: #9090b8; }
.page-title { font-size: 26px; font-weight: bold; color: #e8e8f4; }
.page-subtitle { font-size: 14px; color: #9090b8; }
.chip {
    background-color: rgba(139, 92, 246, 0.18);
    color: #a78bfa;
    border-radius: 9999px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: bold;
    border: 1px solid rgba(139, 92, 246, 0.35);
}
.ai-hero {
    background: linear-gradient(135deg, rgba(139,92,246,0.16), rgba(34,211,238,0.10));
    border: 1px solid rgba(139, 92, 246, 0.30);
    border-radius: 14px;
    padding: 14px 18px;
}
.ai-hero-title { font-size: 15px; font-weight: bold; color: #e8e8f4; }
.ai-hero-sub { font-size: 12px; color: #9090b8; }
.nav-btn-next {
    background-color: #8b5cf6;
    color: white;
    border-radius: 9999px;
    border: none;
    padding: 10px 30px;
    font-size: 15px;
    font-weight: bold;
}
.nav-btn-next:hover { background-color: #7c3aed; }
.nav-btn-next:disabled { background-color: #3a3060; color: #8a8aa8; }
.nav-btn-back {
    background-color: transparent;
    color: #9090b8;
    border: 1px solid #3a3a58;
    border-radius: 9999px;
    padding: 10px 24px;
}
.nav-btn-danger {
    background-color: #dc2626;
    color: white;
    border-radius: 9999px;
    border: none;
    padding: 10px 30px;
    font-size: 15px;
    font-weight: bold;
}
.status-online { color: #10b981; font-size: 13px; font-weight: bold; }
.status-offline { color: #f59e0b; font-size: 13px; font-weight: bold; }
.warn-text { color: #f59e0b; font-size: 12px; }
.error-text { color: #ef4444; font-size: 13px; }
.muted { color: #70709a; font-size: 12px; }
.summary-value { color: #a78bfa; font-weight: bold; }
.install-percent { font-size: 44px; font-weight: bold; color: #8b5cf6; }
.install-step { font-size: 15px; color: #e8e8f4; }
.install-tip { font-size: 13px; color: #9090b8; font-style: italic; }
.check-icon { font-size: 72px; color: #10b981; }
progressbar > trough { background-color: #1d1d2e; border-radius: 9999px; min-height: 10px; }
progressbar > trough > progress { background-color: #8b5cf6; border-radius: 9999px; min-height: 10px; }
"""

_STEPS = [
    ("welcome", "Welcome"),
    ("network", "Internet"),
    ("identity", "About You"),
    ("disk", "Disk Setup"),
    ("ai", "AI Setup"),
    ("summary", "Summary"),
    ("install", "Installing"),
    ("done", "Finished"),
]


def _cls(widget, *names):
    for name in names:
        widget.get_style_context().add_class(name)
    return widget


def _label(text, *classes, halign=Gtk.Align.START, wrap=False):
    lbl = Gtk.Label(label=text)
    lbl.set_halign(halign)
    if wrap:
        lbl.set_wrap(True)
        lbl.set_xalign(0.0 if halign == Gtk.Align.START else 0.5)
    _cls(lbl, *classes)
    return lbl


def _human_size(num_bytes: int) -> str:
    gib = num_bytes / (1024**3)
    if gib >= 1000:
        return f"{gib / 1024:.1f} TiB"
    return f"{gib:.0f} GiB"


def is_live_session() -> bool:
    try:
        with open("/proc/cmdline") as f:
            return "boot=casper" in f.read()
    except OSError:
        return False


class InstallerWindow(Adw.Window):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("Install Axon OS")
        self.set_default_size(1060, 720)

        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._live = is_live_session()
        self._engine_proc = None
        self._install_failed = False
        self._poll_running = False

        # Wizard state collected across pages
        self.state = {
            "install_mode": "erase",
            "target_disk": None,
            "full_name": "",
            "username": "",
            "password": "",
            "hostname": "axon",
            "install_ollama": True,
            "ollama_model": "llama3.2:3b",
            "providers": {},  # id -> api_key
        }

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        _cls(root, "installer-window")
        root.append(self._build_sidebar())

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._stack.add_named(self._page_welcome(), "welcome")
        self._stack.add_named(self._page_network(), "network")
        self._stack.add_named(self._page_identity(), "identity")
        self._stack.add_named(self._page_disk(), "disk")
        self._stack.add_named(self._page_ai(), "ai")
        self._stack.add_named(self._page_summary(), "summary")
        self._stack.add_named(self._page_install(), "install")
        self._stack.add_named(self._page_done(), "done")
        root.append(self._stack)

        self.set_content(root)
        self._current = 0
        self._update_sidebar()
        GLib.timeout_add_seconds(4, self._poll_connectivity)

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> Gtk.Box:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        _cls(side, "sidebar")
        side.set_size_request(250, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head.set_margin_top(36)
        head.set_margin_bottom(28)
        head.append(_label("⬡", "sidebar-logo", halign=Gtk.Align.CENTER))
        head.append(_label("Axon OS", "sidebar-product", halign=Gtk.Align.CENTER))
        head.append(
            _label(
                f"{_os_version()} — AI-native desktop", "sidebar-version", halign=Gtk.Align.CENTER
            )
        )
        side.append(head)

        self._step_rows = []
        self._step_dots = []
        self._step_labels = []
        for _, title in _STEPS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            _cls(row, "step-row")
            row.set_margin_start(18)
            row.set_margin_end(18)
            dot = _label("●", "step-dot")
            lbl = _label(title, "step-label")
            row.append(dot)
            row.append(lbl)
            side.append(row)
            self._step_rows.append(row)
            self._step_dots.append(dot)
            self._step_labels.append(lbl)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        side.append(spacer)

        foot = _label(
            "Local-first AI · Your data stays yours", "muted", halign=Gtk.Align.CENTER, wrap=True
        )
        foot.set_margin_bottom(20)
        foot.set_margin_start(16)
        foot.set_margin_end(16)
        side.append(foot)
        return side

    def _update_sidebar(self) -> None:
        for i, (row, dot, lbl) in enumerate(
            zip(self._step_rows, self._step_dots, self._step_labels, strict=False)
        ):
            for widget, classes in (
                (row, ["step-row-current"]),
                (dot, ["step-dot-current", "step-dot-done"]),
                (lbl, ["step-label-current", "step-label-done"]),
            ):
                for c in classes:
                    widget.get_style_context().remove_class(c)
            if i < self._current:
                dot.set_label("✓")
                _cls(dot, "step-dot-done")
                _cls(lbl, "step-label-done")
            elif i == self._current:
                dot.set_label("●")
                _cls(row, "step-row-current")
                _cls(dot, "step-dot-current")
                _cls(lbl, "step-label-current")
            else:
                dot.set_label("●")

    def _go(self, name: str) -> None:
        idx = [s[0] for s in _STEPS].index(name)
        self._stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT
            if idx > self._current
            else Gtk.StackTransitionType.SLIDE_RIGHT
        )
        self._stack.set_visible_child_name(name)
        self._current = idx
        self._update_sidebar()
        if name == "network":
            self._refresh_wifi()
        elif name == "disk":
            self._refresh_disks()
        elif name == "summary":
            self._build_summary_rows()
        elif name == "install":
            self._start_install()

    # ------------------------------------------------------------------
    # Page scaffold helpers
    # ------------------------------------------------------------------

    def _page_box(self, title: str, subtitle: str):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(620)
        clamp.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_margin_top(44)
        page.set_margin_bottom(28)
        page.set_margin_start(36)
        page.set_margin_end(36)
        page.append(_label(title, "page-title"))
        sub = _label(subtitle, "page-subtitle", wrap=True)
        sub.set_margin_bottom(10)
        page.append(sub)

        clamp.set_child(page)
        outer.append(clamp)
        return outer, page

    def _nav_row(self, back_to=None, next_to=None, next_label="Continue", next_danger=False):
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        nav.set_halign(Gtk.Align.END)
        nav.set_margin_top(14)
        if back_to:
            back = Gtk.Button(label="Back")
            _cls(back, "nav-btn-back")
            back.connect("clicked", lambda _b: self._go(back_to))
            nav.append(back)
        next_btn = None
        if next_to:
            next_btn = Gtk.Button(label=next_label)
            _cls(next_btn, "nav-btn-danger" if next_danger else "nav-btn-next")
            next_btn.connect("clicked", lambda _b: self._go(next_to))
            nav.append(next_btn)
        return nav, next_btn

    # ------------------------------------------------------------------
    # PAGE — Welcome
    # ------------------------------------------------------------------

    def _page_welcome(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_valign(Gtk.Align.CENTER)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_start(48)
        outer.set_margin_end(48)

        outer.append(_label("⬡", "hero-logo", halign=Gtk.Align.CENTER))
        outer.append(_label("Welcome to Axon OS", "hero-title", halign=Gtk.Align.CENTER))
        outer.append(
            _label(
                "The AI-native operating system. Your assistant lives in the OS itself —\n"
                "local-first, private, and one keystroke away.",
                "hero-subtitle",
                halign=Gtk.Align.CENTER,
                wrap=True,
            )
        )

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chips.set_halign(Gtk.Align.CENTER)
        chips.set_margin_top(18)
        for text in ("⬡ AI-Centered", "🔒 100% Local Option", "☁ Any Provider", "🐧 GNOME Native"):
            chips.append(_cls(Gtk.Label(label=text), "chip"))
        outer.append(chips)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btns.set_halign(Gtk.Align.CENTER)
        btns.set_margin_top(34)

        try_btn = Gtk.Button(label="Try Axon OS" if self._live else "Quit")
        _cls(try_btn, "nav-btn-back")
        try_btn.connect("clicked", lambda _b: self.close())
        btns.append(try_btn)

        install_btn = Gtk.Button(label="Install Axon OS  →")
        _cls(install_btn, "nav-btn-next")
        install_btn.connect("clicked", lambda _b: self._go("network"))
        btns.append(install_btn)
        outer.append(btns)

        if self._live:
            note = _label(
                "Trying keeps everything in memory — nothing touches your disk.",
                "muted",
                halign=Gtk.Align.CENTER,
            )
            note.set_margin_top(12)
            outer.append(note)
        return outer

    # ------------------------------------------------------------------
    # PAGE — Network
    # ------------------------------------------------------------------

    def _page_network(self):
        outer, page = self._page_box(
            "Connect to the Internet",
            "A connection lets the installer set up Ollama and download your AI model "
            "automatically. You can also stay offline — AI setup will finish on first boot.",
        )

        self._net_status = _label("Checking connection…", "status-offline")
        page.append(self._net_status)

        group = Adw.PreferencesGroup()
        group.set_title("Wi-Fi networks")
        group.set_margin_top(8)

        self._wifi_list = Gtk.ListBox()
        self._wifi_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._wifi_list.add_css_class("boxed-list")
        group.add(self._wifi_list)
        page.append(group)

        refresh = Gtk.Button(label="↻  Rescan networks")
        _cls(refresh, "nav-btn-back")
        refresh.set_halign(Gtk.Align.START)
        refresh.set_margin_top(8)
        refresh.connect("clicked", lambda _b: self._refresh_wifi())
        page.append(refresh)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav, _ = self._nav_row(back_to="welcome", next_to="identity")
        page.append(nav)
        return outer

    def _poll_connectivity(self) -> bool:
        if self._poll_running:
            return True
        self._poll_running = True

        def check():
            try:
                out = subprocess.run(
                    ["nmcli", "-t", "-f", "CONNECTIVITY", "general"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
            except Exception:
                out = "unknown"
            GLib.idle_add(self._set_net_status, out)
            self._poll_running = False

        threading.Thread(target=check, daemon=True).start()
        return True  # keep the timeout alive

    def _set_net_status(self, state: str) -> None:
        ctx = self._net_status.get_style_context()
        ctx.remove_class("status-online")
        ctx.remove_class("status-offline")
        if state == "full":
            self._net_status.set_label("●  Connected to the internet")
            ctx.add_class("status-online")
        else:
            self._net_status.set_label("●  Offline — AI downloads will run on first boot")
            ctx.add_class("status-offline")

    def _refresh_wifi(self) -> None:
        def scan():
            rows = []
            try:
                out = subprocess.run(
                    [
                        "nmcli",
                        "-t",
                        "-f",
                        "IN-USE,SSID,SIGNAL,SECURITY",
                        "dev",
                        "wifi",
                        "list",
                        "--rescan",
                        "yes",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                ).stdout
                seen = set()
                for line in out.splitlines():
                    parts = line.split(":")
                    if len(parts) < 4:
                        continue
                    in_use = parts[0].strip() == "*"
                    signal = parts[-2]
                    security = parts[-1]
                    ssid = ":".join(parts[1:-2]).replace("\\:", ":").strip()
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    rows.append((ssid, in_use, signal, security))
            except Exception:
                pass
            GLib.idle_add(self._populate_wifi, rows)

        threading.Thread(target=scan, daemon=True).start()

    def _populate_wifi(self, rows) -> None:
        while child := self._wifi_list.get_first_child():
            self._wifi_list.remove(child)
        if not rows:
            row = Adw.ActionRow(title="No Wi-Fi networks found")
            row.set_subtitle("Plug in an ethernet cable, or continue offline")
            self._wifi_list.append(row)
            return
        for ssid, in_use, signal, security in rows[:12]:
            secured = bool(security and security != "--")
            row = Adw.ActionRow(title=ssid)
            bits = [f"signal {signal}%"]
            bits.append("secured" if secured else "open")
            if in_use:
                bits.append("connected ✓")
            row.set_subtitle(" · ".join(bits))
            row.set_activatable(not in_use)
            icon = Gtk.Label(label="🔒" if secured else "📶")
            row.add_prefix(icon)
            row.connect("activated", self._on_wifi_row, ssid, secured)
            self._wifi_list.append(row)

    def _on_wifi_row(self, _row, ssid: str, secured: bool) -> None:
        if not secured:
            self._wifi_connect(ssid, None)
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Connect to “{ssid}”",
            body="Enter the network password.",
        )
        entry = Gtk.PasswordEntry()
        entry.set_show_peek_icon(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")

        def on_response(_d, response):
            if response == "connect":
                self._wifi_connect(ssid, entry.get_text())

        dialog.connect("response", on_response)
        dialog.present()

    def _wifi_connect(self, ssid: str, password) -> None:
        self._net_status.set_label(f"●  Connecting to {ssid}…")

        def connect():
            cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            except Exception:
                pass
            GLib.idle_add(self._refresh_wifi)
            self._poll_connectivity()

        threading.Thread(target=connect, daemon=True).start()

    # ------------------------------------------------------------------
    # PAGE — Identity
    # ------------------------------------------------------------------

    def _page_identity(self):
        outer, page = self._page_box(
            "Tell Us About You", "This creates your account on the installed system."
        )

        group = Adw.PreferencesGroup()

        self._fullname_row = Adw.EntryRow(title="Full name")
        self._fullname_row.connect("changed", self._on_identity_changed)
        group.add(self._fullname_row)

        self._username_row = Adw.EntryRow(title="Username")
        self._username_row.connect("changed", self._on_username_edited)
        group.add(self._username_row)

        self._password_row = Adw.PasswordEntryRow(title="Password")
        self._password_row.connect("changed", self._on_identity_changed)
        group.add(self._password_row)

        self._password2_row = Adw.PasswordEntryRow(title="Confirm password")
        self._password2_row.connect("changed", self._on_identity_changed)
        group.add(self._password2_row)

        self._hostname_row = Adw.EntryRow(title="Computer name")
        self._hostname_row.set_text("axon")
        self._hostname_row.connect("changed", self._on_identity_changed)
        group.add(self._hostname_row)

        page.append(group)

        self._identity_hint = _label("", "error-text", wrap=True)
        self._identity_hint.set_margin_top(8)
        page.append(self._identity_hint)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav, self._identity_next = self._nav_row(back_to="network", next_to="disk")
        self._identity_next.set_sensitive(False)
        page.append(nav)
        self._username_touched = False
        return outer

    def _on_username_edited(self, _row) -> None:
        self._username_touched = bool(self._username_row.get_text())
        self._on_identity_changed(None)

    def _on_identity_changed(self, _row) -> None:
        full_name = self._fullname_row.get_text().strip()
        if full_name and not self._username_touched:
            suggestion = re.sub(r"[^a-z0-9]", "", full_name.split()[0].lower())[:32]
            if suggestion and suggestion != self._username_row.get_text():
                self._username_row.handler_block_by_func(self._on_username_edited)
                self._username_row.set_text(suggestion)
                self._username_row.handler_unblock_by_func(self._on_username_edited)

        username = self._username_row.get_text().strip()
        pw1 = self._password_row.get_text()
        pw2 = self._password2_row.get_text()
        hostname = self._hostname_row.get_text().strip()

        problem = ""
        if username and not USERNAME_RE.match(username):
            problem = "Username must start with a lowercase letter (a–z, 0–9, dashes)."
        elif pw1 and len(pw1) < 4:
            problem = "Password must be at least 4 characters."
        elif pw2 and pw1 != pw2:
            problem = "Passwords do not match."
        elif hostname and not HOSTNAME_RE.match(hostname):
            problem = "Computer name may only contain letters, digits and dashes."
        self._identity_hint.set_label(problem)

        valid = (
            bool(full_name)
            and USERNAME_RE.match(username) is not None
            and len(pw1) >= 4
            and pw1 == pw2
            and HOSTNAME_RE.match(hostname) is not None
        )
        self._identity_next.set_sensitive(bool(valid))
        if valid:
            self.state.update(
                full_name=full_name, username=username, password=pw1, hostname=hostname
            )

    # ------------------------------------------------------------------
    # PAGE — Disk
    # ------------------------------------------------------------------

    def _page_disk(self):
        outer, page = self._page_box(
            "How Do You Want to Install?",
            "Pick an installation style, then choose the target disk.",
        )

        mode_group = Adw.PreferencesGroup()

        self._mode_erase = Gtk.CheckButton()
        self._mode_erase.set_active(True)
        erase_row = Adw.ActionRow(
            title="Erase disk and install Axon OS",
            subtitle="Deletes everything on the selected disk — simplest and cleanest",
        )
        erase_row.add_prefix(self._mode_erase)
        erase_row.set_activatable_widget(self._mode_erase)
        mode_group.add(erase_row)

        self._mode_alongside = Gtk.CheckButton(group=self._mode_erase)
        dual_row = Adw.ActionRow(
            title="Install alongside another OS (dual boot)",
            subtitle="Uses the largest unallocated space (≥ 16 GB) on the selected disk. "
            "Shrink a partition in GNOME Disks first if there is none. "
            "The GRUB menu will list your other systems at boot.",
        )
        dual_row.add_prefix(self._mode_alongside)
        dual_row.set_activatable_widget(self._mode_alongside)
        mode_group.add(dual_row)
        page.append(mode_group)

        disk_group = Adw.PreferencesGroup()
        disk_group.set_title("Target disk")
        disk_group.set_margin_top(10)
        self._disk_list = Gtk.ListBox()
        self._disk_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._disk_list.add_css_class("boxed-list")
        disk_group.add(self._disk_list)
        page.append(disk_group)

        warn = _label(
            "⚠ “Erase disk” permanently destroys all data on the chosen disk.",
            "warn-text",
            wrap=True,
        )
        warn.set_margin_top(8)
        page.append(warn)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav, self._disk_next = self._nav_row(back_to="identity", next_to="ai")
        self._disk_next.set_sensitive(False)
        page.append(nav)
        return outer

    def _refresh_disks(self) -> None:
        def scan():
            disks = []
            medium = self._live_medium_disk()
            try:
                out = subprocess.run(
                    ["lsblk", "-J", "-b", "-d", "-o", "PATH,SIZE,MODEL,TYPE,RO"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout
                for dev in json.loads(out).get("blockdevices", []):
                    if dev.get("type") != "disk" or dev.get("ro"):
                        continue
                    path = dev["path"]
                    if path.startswith(("/dev/loop", "/dev/sr", "/dev/zram")):
                        continue
                    if medium and os.path.realpath(path) == os.path.realpath(medium):
                        continue
                    disks.append(
                        (path, int(dev.get("size") or 0), (dev.get("model") or "Disk").strip())
                    )
            except Exception:
                pass
            GLib.idle_add(self._populate_disks, disks)

        threading.Thread(target=scan, daemon=True).start()

    @staticmethod
    def _live_medium_disk() -> str:
        for mount in ("/cdrom", "/run/live/medium"):
            try:
                source = subprocess.run(
                    ["findmnt", "-n", "-o", "SOURCE", mount],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
                if not source:
                    continue
                pk = (
                    subprocess.run(
                        ["lsblk", "-no", "PKNAME", source],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    .stdout.strip()
                    .splitlines()
                )
                return f"/dev/{pk[0]}" if pk and pk[0] else source
            except Exception:
                continue
        return ""

    def _populate_disks(self, disks) -> None:
        while child := self._disk_list.get_first_child():
            self._disk_list.remove(child)
        self._disk_checks = []
        if not disks:
            row = Adw.ActionRow(title="No usable disks found")
            row.set_subtitle("Attach a disk of at least 20 GB and rescan")
            self._disk_list.append(row)
            self._disk_next.set_sensitive(False)
            return
        first = None
        for path, size, model in disks:
            check = Gtk.CheckButton()
            if first is None:
                first = check
            else:
                check.set_group(first)
            row = Adw.ActionRow(title=f"{model}  ({_human_size(size)})")
            row.set_subtitle(path)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            check.connect("toggled", self._on_disk_toggled, path)
            self._disk_list.append(row)
            self._disk_checks.append(check)
        first.set_active(True)

    def _on_disk_toggled(self, check: Gtk.CheckButton, path: str) -> None:
        if check.get_active():
            self.state["target_disk"] = path
            self._disk_next.set_sensitive(True)

    # ------------------------------------------------------------------
    # PAGE — AI Setup (the heart of an AI-centered OS)
    # ------------------------------------------------------------------

    def _page_ai(self):
        outer, page = self._page_box(
            "Set Up Your AI",
            "Axon OS is built around its AI. Choose a local model, connect cloud "
            "providers, or both — you can change everything later in Settings.",
        )

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        _cls(hero, "ai-hero")
        hero.append(_label("⬡  Local AI with Ollama — recommended", "ai-hero-title"))
        hero.append(
            _label(
                "Runs entirely on this machine. Powers the Intent Bar, AI Panel and "
                "system-wide assistance with zero data leaving your computer.",
                "ai-hero-sub",
                wrap=True,
            )
        )
        page.append(hero)

        local_group = Adw.PreferencesGroup()
        local_group.set_margin_top(10)

        self._ollama_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._ollama_switch.set_active(True)
        ollama_row = Adw.ActionRow(
            title="Install Ollama",
            subtitle="Local model runtime — installed automatically (online now, or on first boot)",
        )
        ollama_row.add_suffix(self._ollama_switch)
        ollama_row.set_activatable_widget(self._ollama_switch)
        local_group.add(ollama_row)

        self._model_combo = Adw.ComboRow(title="Default local model")
        model_names = Gtk.StringList()
        for _mid, name, desc in OLLAMA_MODELS:
            model_names.append(f"{name} — {desc}")
        self._model_combo.set_model(model_names)
        self._model_combo.set_selected(1)  # llama3.2:3b
        local_group.add(self._model_combo)

        self._ollama_switch.connect(
            "notify::active", lambda sw, _p: self._model_combo.set_sensitive(sw.get_active())
        )
        page.append(local_group)

        cloud_group = Adw.PreferencesGroup()
        cloud_group.set_title("Cloud AI providers (optional)")
        cloud_group.set_description("Add an API key to use hosted models alongside local AI")
        cloud_group.set_margin_top(10)

        self._provider_rows = {}
        for pid, name, desc in CLOUD_PROVIDERS:
            expander = Adw.ExpanderRow(title=name, subtitle=desc)
            expander.set_show_enable_switch(True)
            expander.set_enable_expansion(False)
            key_row = Adw.PasswordEntryRow(title="API key")
            expander.add_row(key_row)
            cloud_group.add(expander)
            self._provider_rows[pid] = (expander, key_row)
        page.append(cloud_group)

        note = _label(
            "Skipping is fine too — Axon works without AI configured, "
            "and the Welcome app can set it up later.",
            "muted",
            wrap=True,
        )
        note.set_margin_top(8)
        page.append(note)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav, _ = self._nav_row(back_to="disk", next_to="summary")
        page.append(nav)
        return outer

    def _collect_ai_state(self) -> None:
        self.state["install_ollama"] = self._ollama_switch.get_active()
        self.state["ollama_model"] = OLLAMA_MODELS[self._model_combo.get_selected()][0]
        providers = {}
        for pid, (expander, key_row) in self._provider_rows.items():
            if expander.get_enable_expansion() and key_row.get_text().strip():
                providers[pid] = key_row.get_text().strip()
        self.state["providers"] = providers

    # ------------------------------------------------------------------
    # PAGE — Summary
    # ------------------------------------------------------------------

    def _page_summary(self):
        outer, page = self._page_box(
            "Ready to Install",
            "Review your choices. Nothing is written to disk until you press Install.",
        )
        self._summary_group = Adw.PreferencesGroup()
        self._summary_rows = []
        page.append(self._summary_group)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav, _ = self._nav_row(
            back_to="ai", next_to="install", next_label="Install Now", next_danger=True
        )
        page.append(nav)
        return outer

    def _build_summary_rows(self) -> None:
        self._collect_ai_state()
        group = self._summary_group
        for row in self._summary_rows:
            group.remove(row)
        self._summary_rows.clear()

        s = self.state
        mode = (
            "Erase entire disk"
            if self._mode_erase.get_active()
            else "Install alongside another OS (dual boot)"
        )
        s["install_mode"] = "erase" if self._mode_erase.get_active() else "alongside"

        ai_bits = []
        if s["install_ollama"]:
            ai_bits.append(f"Ollama + {s['ollama_model']}")
        ai_bits += [dict(CLOUD_PROVIDERS_BY_ID)[pid] for pid in s["providers"]]
        rows = [
            ("Disk", f"{s['target_disk']} — {mode}"),
            ("User", f"{s['full_name']}  (@{s['username']})"),
            ("Computer name", s["hostname"]),
            ("AI setup", " · ".join(ai_bits) if ai_bits else "Skipped — configure later"),
        ]
        for title, value in rows:
            row = Adw.ActionRow(title=title)
            row.add_suffix(_label(value, "summary-value", wrap=True, halign=Gtk.Align.END))
            group.add(row)
            self._summary_rows.append(row)

    # ------------------------------------------------------------------
    # PAGE — Install (progress)
    # ------------------------------------------------------------------

    def _page_install(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_valign(Gtk.Align.CENTER)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_start(64)
        outer.set_margin_end(64)

        outer.append(_label("⬡", "hero-logo", halign=Gtk.Align.CENTER))
        self._install_pct = _label("0%", "install-percent", halign=Gtk.Align.CENTER)
        outer.append(self._install_pct)

        self._install_bar = Gtk.ProgressBar()
        self._install_bar.set_size_request(460, -1)
        outer.append(self._install_bar)

        self._install_step = _label(
            "Starting installer…", "install-step", halign=Gtk.Align.CENTER, wrap=True
        )
        outer.append(self._install_step)

        self._install_tip = _label(
            INSTALL_TIPS[0], "install-tip", halign=Gtk.Align.CENTER, wrap=True
        )
        self._install_tip.set_margin_top(22)
        self._install_tip.set_size_request(480, -1)
        outer.append(self._install_tip)

        self._install_error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._install_error_box.set_visible(False)
        self._install_error = _label("", "error-text", halign=Gtk.Align.CENTER, wrap=True)
        self._install_error_box.append(self._install_error)
        retry = Gtk.Button(label="Back to Summary")
        _cls(retry, "nav-btn-back")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda _b: self._go("summary"))
        self._install_error_box.append(retry)
        outer.append(self._install_error_box)

        self._tip_index = 0
        GLib.timeout_add_seconds(7, self._rotate_tip)
        return outer

    def _rotate_tip(self) -> bool:
        self._tip_index = (self._tip_index + 1) % len(INSTALL_TIPS)
        self._install_tip.set_label(INSTALL_TIPS[self._tip_index])
        return True

    def _engine_config(self) -> dict:
        s = self.state
        providers = [{"id": pid, "api_key": key} for pid, key in s["providers"].items()]
        return {
            "target_disk": s["target_disk"],
            "install_mode": s["install_mode"],
            "user": {
                "full_name": s["full_name"],
                "username": s["username"],
                "password": s["password"],
                "hostname": s["hostname"],
            },
            "ai": {
                "install_ollama": s["install_ollama"],
                "ollama_model": s["ollama_model"],
                "providers": providers,
            },
        }

    def _engine_command(self, config_path: str):
        if os.geteuid() == 0:
            return [sys.executable, str(ENGINE_PATH), config_path]
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
            return ["sudo", "-n", sys.executable, str(ENGINE_PATH), config_path]
        if os.path.exists(ENGINE_WRAPPER):
            return ["pkexec", ENGINE_WRAPPER, config_path]
        return ["pkexec", sys.executable, str(ENGINE_PATH), config_path]

    def _start_install(self) -> None:
        if self._engine_proc is not None:
            return
        self._install_failed = False
        self._install_error_box.set_visible(False)
        self.set_deletable(False)

        fd, config_path = tempfile.mkstemp(prefix="axon-install-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(self._engine_config(), f)
        os.chmod(config_path, 0o600)

        cmd = self._engine_command(config_path)

        def runner():
            try:
                self._engine_proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                for line in self._engine_proc.stdout:
                    line = line.strip()
                    if line.startswith("AXON-PROGRESS:"):
                        _, pct, msg = line.split(":", 2)
                        GLib.idle_add(self._on_progress, int(pct), msg)
                    elif line.startswith("AXON-ERROR:"):
                        GLib.idle_add(self._on_install_error, line.split(":", 1)[1])
                    elif line == "AXON-DONE":
                        GLib.idle_add(self._on_install_done)
                self._engine_proc.wait()
                if self._engine_proc.returncode != 0 and not self._install_failed:
                    GLib.idle_add(
                        self._on_install_error,
                        f"installer exited with code {self._engine_proc.returncode}",
                    )
            except Exception as exc:
                GLib.idle_add(self._on_install_error, str(exc))
            finally:
                try:
                    os.remove(config_path)
                except OSError:
                    pass
                self._engine_proc = None

        threading.Thread(target=runner, daemon=True).start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self._install_pct.set_label(f"{pct}%")
        self._install_bar.set_fraction(pct / 100.0)
        self._install_step.set_label(msg)

    def _on_install_error(self, message: str) -> None:
        self._install_failed = True
        self.set_deletable(True)
        self._install_step.set_label("Installation failed")
        self._install_error.set_label(message.strip())
        self._install_error_box.set_visible(True)

    def _on_install_done(self) -> None:
        self.set_deletable(True)
        self._go("done")

    # ------------------------------------------------------------------
    # PAGE — Done
    # ------------------------------------------------------------------

    def _page_done(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_valign(Gtk.Align.CENTER)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_start(64)
        outer.set_margin_end(64)

        outer.append(_label("✓", "check-icon", halign=Gtk.Align.CENTER))
        outer.append(_label("Axon OS Is Installed!", "hero-title", halign=Gtk.Align.CENTER))
        outer.append(
            _label(
                "Remove the installation media, then restart to boot into your new "
                "AI-native desktop. Your AI finishes setting itself up on first boot.",
                "hero-subtitle",
                halign=Gtk.Align.CENTER,
                wrap=True,
            )
        )

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btns.set_halign(Gtk.Align.CENTER)
        btns.set_margin_top(28)

        stay = Gtk.Button(label="Keep Exploring Live")
        _cls(stay, "nav-btn-back")
        stay.connect("clicked", lambda _b: self.close())
        btns.append(stay)

        reboot = Gtk.Button(label="Restart Now  ⟳")
        _cls(reboot, "nav-btn-next")
        reboot.connect("clicked", self._on_reboot)
        btns.append(reboot)
        outer.append(btns)
        return outer

    def _on_reboot(self, _btn) -> None:
        for cmd in (["systemctl", "reboot"], ["sudo", "-n", "reboot"]):
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                return
        # Both commands failed — inform the user
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Reboot failed",
            body="Could not reboot automatically. Please reboot manually.",
        )
        dialog.add_response("ok", "OK")
        dialog.present()
