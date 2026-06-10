"""Axon OS Welcome App — WelcomeWindow (4-page onboarding wizard)."""

import os
import sys
import shutil
import subprocess
import threading
import json
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import dbus
from dbus.mainloop.glib import DBusGMainLoop

# Insert axon-brain path so we can run hardware profiler
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "axon-brain"))
try:
    import hardware_profiler
except ImportError:
    hardware_profiler = None

# ---------------------------------------------------------------------------
# Embedded CSS
# ---------------------------------------------------------------------------

_CSS = b"""
.welcome-window {
    background-color: #09090f;
}
.hero-logo {
    font-size: 52px;
    color: #8b5cf6;
}
.hero-title {
    font-size: 32px;
    font-weight: bold;
    color: #e8e8f4;
}
.hero-subtitle {
    font-size: 16px;
    color: #9090b8;
}
.chip {
    background-color: rgba(139, 92, 246, 0.18);
    color: #8b5cf6;
    border-radius: 9999px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: bold;
    border: 1px solid rgba(139, 92, 246, 0.35);
}
.hardware-badge {
    background-color: rgba(34, 211, 238, 0.1);
    color: #22d3ee;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    border: 1px solid rgba(34, 211, 238, 0.25);
    margin-bottom: 12px;
}
.model-row {
    background-color: #111119;
    border-radius: 12px;
    border: 1px solid #2a2a42;
    padding: 12px 16px;
}
.model-name {
    font-size: 14px;
    font-weight: bold;
    color: #e8e8f4;
}
.model-desc {
    font-size: 12px;
    color: #9090b8;
}
.model-size {
    font-size: 11px;
    color: #50507a;
}
.page-title {
    font-size: 24px;
    font-weight: bold;
    color: #e8e8f4;
}
.page-subtitle {
    font-size: 14px;
    color: #9090b8;
}
.nav-btn-next {
    background-color: #8b5cf6;
    color: white;
    border-radius: 9999px;
    border: none;
    padding: 10px 28px;
    font-size: 15px;
}
.nav-btn-next:hover {
    background-color: #7c3aed;
}
.nav-btn-back {
    background-color: transparent;
    color: #9090b8;
    border: 1px solid #3a3a58;
    border-radius: 9999px;
    padding: 10px 22px;
}
.page-indicator-dot {
    font-size: 8px;
}
.check-icon {
    font-size: 64px;
    color: #10b981;
}
.status-online {
    color: #10b981;
    font-size: 12px;
}
.status-offline {
    color: #ef4444;
    font-size: 12px;
}
"""

def _add_class(widget: Gtk.Widget, css_class: str) -> None:
    widget.get_style_context().add_class(css_class)

def _remove_class(widget: Gtk.Widget, css_class: str) -> None:
    widget.get_style_context().remove_class(css_class)


class WelcomeWindow(Adw.Window):
    _PAGE_NAMES = ["welcome", "setup", "features", "ready"]

    def __init__(self, app: Adw.Application):
        super().__init__(application=app)

        # Initialize DBus GLib loop integration early
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.brain = None
        self._connect_brain()

        # Track model downloading state
        self._downloading_model = None

        # Run hardware profiling
        self._profile = {}
        self._models = []
        self._run_profiling()

        # Window properties
        self.set_title("Welcome to Axon OS")
        self.set_default_size(640, 600)
        self.set_decorated(True)

        # Load CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Root layout
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        _add_class(root_box, "welcome-window")

        clamp = Adw.Clamp()
        clamp.set_maximum_size(560)
        clamp.set_vexpand(True)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self._stack.set_vexpand(True)

        # Page indicator
        self._dot_labels: list[Gtk.Label] = []
        indicator_row = self._build_indicator()

        # Build pages
        self._stack.add_named(self._build_page_welcome(), "welcome")
        self._stack.add_named(self._build_page_setup(), "setup")
        self._stack.add_named(self._build_page_features(), "features")
        self._stack.add_named(self._build_page_ready(), "ready")

        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner_box.append(self._stack)
        inner_box.append(indicator_row)

        clamp.set_child(inner_box)
        root_box.append(clamp)
        self.set_content(root_box)

        # Track current page index
        self._current_page = 0
        self._update_indicator()

        # Register D-Bus signal listener for model pulls
        self.bus.add_signal_receiver(
            self._on_pull_progress,
            signal_name="PullProgress",
            dbus_interface="org.axonos.Brain"
        )

    def _connect_brain(self) -> None:
        try:
            self.brain = self.bus.get_object('org.axonos.Brain', '/org/axonos/Brain')
        except Exception:
            self.brain = None

    def _run_profiling(self) -> None:
        if hardware_profiler:
            try:
                self._profile = hardware_profiler.profile_hardware()
                hw = self._profile.get("hardware", {})
                recs = self._profile.get("recommendations", {})
                self._hw_info_str = f"Hardware profile: {hw.get('ram_gb')}GB RAM | GPU: {hw.get('gpu_vendor')} ({hw.get('gpu_model')})"
                
                self._models = [
                    ("Speed Model", recs.get("speed", {}).get("model"), recs.get("speed", {}).get("description")),
                    ("General Model", recs.get("general", {}).get("model"), recs.get("general", {}).get("description")),
                    ("Deep Task Model", recs.get("deep", {}).get("model"), recs.get("deep", {}).get("description"))
                ]
                return
            except Exception:
                pass
        
        # Fallbacks if module is missing or fails
        self._hw_info_str = "Hardware profile: Generic System CPU"
        self._models = [
            ("Speed Model", "llama3.2:1b", "Llama 3.2 1B — fast command processing."),
            ("General Model", "llama3.2:3b", "Llama 3.2 3B — balanced conversation."),
            ("Deep Task Model", "llama3:8b", "Llama 3 8B — high reasoning, running on CPU RAM.")
        ]

    # ------------------------------------------------------------------
    # Page indicator
    # ------------------------------------------------------------------

    def _build_indicator(self) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_bottom(16)
        row.set_margin_top(6)

        for _ in self._PAGE_NAMES:
            dot = Gtk.Label(label="●")
            _add_class(dot, "page-indicator-dot")
            dot.set_opacity(0.3)
            self._dot_labels.append(dot)
            row.append(dot)

        return row

    def _update_indicator(self) -> None:
        for i, dot in enumerate(self._dot_labels):
            if i == self._current_page:
                dot.set_opacity(1.0)
                dot.set_markup('<span color="#8b5cf6">●</span>')
            else:
                dot.set_opacity(0.3)
                dot.set_markup('<span color="#50507a">●</span>')

    def _go_to(self, page_name: str) -> None:
        idx = self._PAGE_NAMES.index(page_name)
        current_idx = self._current_page

        if idx > current_idx:
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        else:
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)

        self._stack.set_visible_child_name(page_name)
        self._current_page = idx
        self._update_indicator()

    # ------------------------------------------------------------------
    # PAGE 1 — Welcome
    # ------------------------------------------------------------------

    def _build_page_welcome(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_halign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.FILL)
        page.set_margin_top(40)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)

        # Hero logo
        logo = Gtk.Label(label="⬡")
        _add_class(logo, "hero-logo")
        logo.set_margin_bottom(8)
        page.append(logo)

        title = Gtk.Label(label="Welcome to Axon OS")
        _add_class(title, "hero-title")
        title.set_wrap(True)
        title.set_justify(Gtk.Justification.CENTER)
        page.append(title)

        subtitle = Gtk.Label(label="Your AI-native desktop. Fully private. Entirely local.")
        _add_class(subtitle, "hero-subtitle")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        page.append(subtitle)

        # Chips
        chips_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chips_row.set_halign(Gtk.Align.CENTER)
        chips_row.set_margin_top(20)
        for chip_text in ["100% Local AI", "Zero Cloud", "GNOME Native"]:
            chip = Gtk.Label(label=chip_text)
            _add_class(chip, "chip")
            chips_row.append(chip)
        page.append(chips_row)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        # Get Started button
        btn = Gtk.Button(label="Get Started")
        _add_class(btn, "nav-btn-next")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _: self._go_to("setup"))
        page.append(btn)

        return page

    # ------------------------------------------------------------------
    # PAGE 2 — Setup (AI Model Downloader)
    # ------------------------------------------------------------------

    def _build_page_setup(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(32)
        page.set_margin_end(32)

        # Title
        title = Gtk.Label(label="Set Up Your AI")
        _add_class(title, "page-title")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(label="Axon recommends local models customized for your hardware.")
        _add_class(subtitle, "page-subtitle")
        subtitle.set_halign(Gtk.Align.START)
        subtitle.set_wrap(True)
        page.append(subtitle)

        # Hardware Info Badge
        hw_lbl = Gtk.Label(label=self._hw_info_str)
        _add_class(hw_lbl, "hardware-badge")
        hw_lbl.set_halign(Gtk.Align.START)
        page.append(hw_lbl)

        # Model checkboxes group
        self._model_checks = []
        self._selected_model = self._models[1][1] # Default to General Model
        first_check = None

        for tier, model_id, model_desc in self._models:
            outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            _add_class(outer, "model-row")

            check = Gtk.CheckButton()
            if first_check is None:
                first_check = check
            else:
                check.set_group(first_check)

            if model_id == self._selected_model:
                check.set_active(True)

            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            inner.set_hexpand(True)

            tier_lbl = Gtk.Label()
            tier_lbl.set_markup(f"<b>{tier}</b>: <span color='#8b5cf6'>{model_id}</span>")
            _add_class(tier_lbl, "model-name")
            tier_lbl.set_halign(Gtk.Align.START)
            inner.append(tier_lbl)

            desc_lbl = Gtk.Label(label=model_desc)
            _add_class(desc_lbl, "model-desc")
            desc_lbl.set_halign(Gtk.Align.START)
            desc_lbl.set_wrap(True)
            inner.append(desc_lbl)

            check.set_child(inner)
            captured_id = model_id

            def _on_toggled(btn, mid=captured_id):
                if btn.get_active():
                    self._selected_model = mid

            check.connect("toggled", _on_toggled)
            self._model_checks.append(check)
            outer.append(check)
            page.append(outer)

        # Pull button + progress area
        self._pull_btn = Gtk.Button(label="Download Recommended Model")
        _add_class(self._pull_btn, "nav-btn-next")
        self._pull_btn.set_halign(Gtk.Align.CENTER)
        self._pull_btn.set_margin_top(8)
        self._pull_btn.connect("clicked", self._on_pull_clicked)
        page.append(self._pull_btn)

        self._progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._progress_box.set_visible(False)
        self._progress_box.set_margin_top(6)

        self._pull_progress = Gtk.ProgressBar()
        self._progress_lbl = Gtk.Label(label="Downloading: 0%")
        _add_class(self._progress_lbl, "model-desc")
        self._progress_lbl.set_halign(Gtk.Align.CENTER)

        self._progress_box.append(self._pull_progress)
        self._progress_box.append(self._progress_lbl)
        page.append(self._progress_box)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        # Navigation row
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_halign(Gtk.Align.CENTER)

        back_btn = Gtk.Button(label="← Back")
        _add_class(back_btn, "nav-btn-back")
        back_btn.connect("clicked", lambda _: self._go_to("welcome"))
        nav.append(back_btn)

        skip_btn = Gtk.Button(label="Skip")
        _add_class(skip_btn, "nav-btn-back")
        skip_btn.connect("clicked", lambda _: self._go_to("features"))
        nav.append(skip_btn)

        continue_btn = Gtk.Button(label="Continue →")
        _add_class(continue_btn, "nav-btn-next")
        continue_btn.connect("clicked", lambda _: self._go_to("features"))
        nav.append(continue_btn)

        page.append(nav)

        return page

    # ------------------------------------------------------------------
    # D-Bus Signal Handler
    # ------------------------------------------------------------------

    def _on_pull_progress(self, model_name, completed_bytes, total_bytes, status):
        # Filter signals for our download
        if not self._downloading_model or model_name != self._downloading_model:
            return

        GLib.idle_add(self._update_pull_progress, completed_bytes, total_bytes, status)

    def _update_pull_progress(self, completed, total, status):
        if total > 0:
            fraction = float(completed) / float(total)
            self._pull_progress.set_fraction(fraction)
            percentage = int(fraction * 100)
            completed_gb = completed / (1024 * 1024 * 1024)
            total_gb = total / (1024 * 1024 * 1024)
            self._progress_lbl.set_text(f"Downloading: {percentage}% ({completed_gb:.2f} / {total_gb:.2f} GB)")
        else:
            self._pull_progress.pulse()
            self._progress_lbl.set_text(f"Status: {status}")
            
        if status == "success" or "verify" in status.lower():
            self._progress_box.set_visible(False)
            self._pull_btn.set_sensitive(True)
            self._pull_btn.set_label("✓ Model Ready")
            self._downloading_model = None
        elif "error" in status.lower():
            self._progress_box.set_visible(False)
            self._pull_btn.set_sensitive(True)
            self._pull_btn.set_label("Download Failed — Retry")
            self._downloading_model = None

    # ------------------------------------------------------------------
    # Pull model action
    # ------------------------------------------------------------------

    def _on_pull_clicked(self, _btn: Gtk.Button) -> None:
        model = self._selected_model
        self._downloading_model = model
        self._pull_btn.set_sensitive(False)
        self._progress_box.set_visible(True)
        self._pull_progress.set_fraction(0.0)
        self._progress_lbl.set_text("Connecting to Axon Brain...")

        # Fire D-Bus pull request
        def _trigger_dbus_pull():
            if self.brain is None:
                try:
                    self._connect_brain()
                except Exception:
                    pass
                    
            if self.brain is not None:
                try:
                    self.brain.PullModel(model)
                except Exception as e:
                    GLib.idle_add(self._update_pull_progress, 0, 0, f"Error: {e}")
            else:
                # Local command fallback if service is missing
                GLib.idle_add(self._update_pull_progress, 0, 0, "Brain service not available. Retrying via CLI...")
                try:
                    subprocess.run(
                        ["ollama", "pull", model],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True
                    )
                    GLib.idle_add(self._update_pull_progress, 1, 1, "success")
                except Exception:
                    GLib.idle_add(self._update_pull_progress, 0, 0, "error")

        threading.Thread(target=_trigger_dbus_pull, daemon=True).start()

    # ------------------------------------------------------------------
    # PAGE 3 — Features
    # ------------------------------------------------------------------

    def _build_page_features(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(32)
        page.set_margin_end(32)

        title = Gtk.Label(label="What Axon Can Do")
        _add_class(title, "page-title")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        grid.set_margin_top(8)

        feature_data = [
            ("⬡", "Intent Bar", "Press Super+Space. Ask anything in natural language."),
            ("🤖", "AI Panel", "Press Super+A. Your persistent AI assistant."),
            ("🏠", "Spaces", "Super+1-9. Named workspaces for each project."),
            ("🔒", "Private", "All AI runs locally. Zero data leaves your machine."),
        ]

        positions = [(0, 0), (1, 0), (0, 1), (1, 1)]

        for (icon, feat_title, feat_desc), (col, row) in zip(feature_data, positions):
            card = self._build_feature_card(icon, feat_title, feat_desc)
            grid.attach(card, col, row, 1, 1)

        page.append(grid)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_halign(Gtk.Align.CENTER)

        back_btn = Gtk.Button(label="← Back")
        _add_class(back_btn, "nav-btn-back")
        back_btn.connect("clicked", lambda _: self._go_to("setup"))
        nav.append(back_btn)

        next_btn = Gtk.Button(label="Next →")
        _add_class(next_btn, "nav-btn-next")
        next_btn.connect("clicked", lambda _: self._go_to("ready"))
        nav.append(next_btn)

        page.append(nav)

        return page

    def _build_feature_card(self, icon: str, feat_title: str, feat_desc: str) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.set_hexpand(True)
        _add_class(card, "feature-card")

        icon_lbl = Gtk.Label(label=icon)
        _add_class(icon_lbl, "feature-icon")
        icon_lbl.set_halign(Gtk.Align.START)
        card.append(icon_lbl)

        title_lbl = Gtk.Label(label=feat_title)
        _add_class(title_lbl, "feature-title")
        title_lbl.set_halign(Gtk.Align.START)
        card.append(title_lbl)

        desc_lbl = Gtk.Label(label=feat_desc)
        _add_class(desc_lbl, "feature-desc")
        desc_lbl.set_halign(Gtk.Align.START)
        desc_lbl.set_wrap(True)
        desc_lbl.set_xalign(0.0)
        card.append(desc_lbl)

        return card

    # ------------------------------------------------------------------
    # PAGE 4 — Ready
    # ------------------------------------------------------------------

    def _build_page_ready(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_halign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.FILL)
        page.set_margin_top(40)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)

        check_lbl = Gtk.Label(label="✓")
        _add_class(check_lbl, "check-icon")
        check_lbl.set_margin_bottom(4)
        page.append(check_lbl)

        title = Gtk.Label(label="You're All Set!")
        _add_class(title, "page-title")
        title.set_justify(Gtk.Justification.CENTER)
        page.append(title)

        subtitle = Gtk.Label(label="Try Super+Space to open the Intent Bar.")
        _add_class(subtitle, "page-subtitle")
        subtitle.set_justify(Gtk.Justification.CENTER)
        subtitle.set_wrap(True)
        page.append(subtitle)

        # Show on startup toggle
        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toggle_row.set_halign(Gtk.Align.CENTER)
        toggle_row.set_margin_top(16)

        toggle_lbl = Gtk.Label(label="Show on startup:")
        _add_class(toggle_lbl, "page-subtitle")
        toggle_row.append(toggle_lbl)

        switch = Gtk.Switch()
        switch.set_active(True)
        switch.connect("state-set", self._on_startup_toggle)
        toggle_row.append(switch)

        page.append(toggle_row)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_halign(Gtk.Align.CENTER)

        back_btn = Gtk.Button(label="← Back")
        _add_class(back_btn, "nav-btn-back")
        back_btn.connect("clicked", lambda _: self._go_to("features"))
        nav.append(back_btn)

        start_btn = Gtk.Button(label="Start Using Axon")
        _add_class(start_btn, "nav-btn-next")
        start_btn.connect("clicked", lambda _: self.close())
        nav.append(start_btn)

        page.append(nav)

        return page

    # ------------------------------------------------------------------
    # Startup toggle
    # ------------------------------------------------------------------

    def _on_startup_toggle(self, switch: Gtk.Switch, state: bool) -> bool:
        config_dir = os.path.expanduser("~/.config/axon-os")
        marker = os.path.join(config_dir, ".firstboot-done")
        if state:
            if os.path.exists(marker):
                try:
                    os.remove(marker)
                except OSError:
                    pass
        else:
            os.makedirs(config_dir, exist_ok=True)
            try:
                with open(marker, "w") as f:
                    f.write("")
            except OSError:
                pass
        return False
