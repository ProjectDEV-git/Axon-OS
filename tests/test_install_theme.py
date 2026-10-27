"""install.sh must not force the Axon GTK theme on the whole system by default."""

import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "install.sh").read_text()


def test_system_theme_is_only_set_when_asked():
    for m in re.finditer(
        r"gsettings set org\.gnome\.desktop\.interface (gtk-theme|color-scheme)", SCRIPT
    ):
        before = SCRIPT[: m.start()]
        # the nearest `if` above the command must be the opt-in check
        last_if = before.rfind("\nif ")
        assert 'AXON_APPLY_SYSTEM_THEME:-0}" == "1"' in before[last_if : last_if + 80], m.group(0)


def test_libadwaita_accent_is_installed_and_user_css_is_kept():
    assert 'cp "${SCRIPT_DIR}/theme/axon-gtk/libadwaita.css" "${ADW_CSS}"' in SCRIPT
    assert 'cp -n "${ADW_CSS}" "${ADW_CSS}.before-axon"' in SCRIPT
    assert "gsettings reset org.gnome.desktop.interface gtk-theme" in SCRIPT, (
        "undoes the old behaviour"
    )


def test_first_login_script_applies_the_theme_only_on_axon_os():
    text = (Path(__file__).resolve().parent.parent / "build/config/firstboot.sh").read_text()
    guard = text.index("ID=axonos")
    assert guard < text.index("gtk-theme axon-gtk") < text.index("\nfi", guard)


def test_uninstall_removes_the_autostart_before_resetting_settings():
    text = (Path(__file__).resolve().parent.parent / "uninstall.sh").read_text()
    # the autostart re-applies the theme at every login, so it must go first
    assert text.index("axon-firstboot.desktop") < text.index("gsettings reset")
    assert "--dry-run" in text and "EUID} -eq 0" in text
