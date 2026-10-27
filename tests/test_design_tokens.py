"""Checks on the single design-token source, theme/tokens/axon-tokens.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOKENS = json.loads((ROOT / "theme" / "tokens" / "axon-tokens.json").read_text())
VARIANTS = TOKENS["variants"]


def _rgb(value: str, background: tuple[float, float, float] | None = None) -> tuple[float, ...]:
    """Parse #rrggbb or rgba(); blend alpha over *background* (0-255 floats)."""
    if value.startswith("#"):
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))
    m = re.match(r"rgba?\(([^)]*)\)", value)
    assert m, value
    parts = [float(p) for p in m.group(1).split(",")]
    r, g, b = parts[:3]
    a = parts[3] if len(parts) > 3 else 1.0
    if background is None or a == 1.0:
        return (r, g, b)
    br, bg_, bb = background
    return (r * a + br * (1 - a), g * a + bg_ * (1 - a), b * a + bb * (1 - a))


def _luminance(rgb: tuple[float, ...]) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    back = _rgb(bg)
    a, b = _luminance(_rgb(fg, back)), _luminance(back)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_variants_define_the_same_tokens() -> None:
    keys = {name: set(v) for name, v in VARIANTS.items()}
    assert set(keys) == {"dark", "light", "high-contrast"}
    assert keys["dark"] == keys["light"] == keys["high-contrast"]


@pytest.mark.parametrize("variant", sorted(VARIANTS))
@pytest.mark.parametrize("surface", ["bg", "surface", "surface_raised"])
def test_text_contrast_meets_wcag_aa(variant: str, surface: str) -> None:
    colours = VARIANTS[variant]
    minimum = 7.0 if variant == "high-contrast" else 4.5
    for text in ("fg", "fg_secondary", "fg_muted", "accent_text", "error", "success", "warning"):
        ratio = contrast(colours[text], colours[surface])
        assert ratio >= minimum, f"{variant}: {text} on {surface} is {ratio:.2f}:1"


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_text_on_accent_and_focus_ring(variant: str) -> None:
    colours = VARIANTS[variant]
    assert contrast(colours["accent_fg"], colours["accent"]) >= 4.5
    # Non-text contrast (WCAG 1.4.11): focus ring vs the window background
    assert contrast(colours["focus_ring"], colours["bg"]) >= 3.0


def test_scales_are_px_or_ms() -> None:
    for group in ("space", "radius"):
        assert all(re.fullmatch(r"\d+px", v) for v in TOKENS[group].values()), group
    # pt, not px: px font sizes don't follow GNOME's Large Text setting
    assert all(re.fullmatch(r"\d+(\.\d+)?pt", v) for v in TOKENS["font_size"].values())
    assert all(re.fullmatch(r"\d+ms", v) for v in TOKENS["duration"].values())


def test_type_floor_and_tracking() -> None:
    # Nothing under 11px (8.25pt); large type tracks tighter, small type looser
    sizes = {k: float(v[:-2]) for k, v in TOKENS["font_size"].items()}
    assert min(sizes.values()) >= 8.25
    track = {k: float(v[:-2]) for k, v in TOKENS["letter_spacing"].items()}
    assert track["display"] < track["title"] < track["body"] <= track["small"]


def test_motion_tokens_follow_the_rules() -> None:
    durations = {k: int(v[:-2]) for k, v in TOKENS["duration"].items()}
    assert durations["instant"] <= 60  # surfaces opened from the keyboard
    assert max(durations.values()) <= 300  # UI motion stays under 300 ms
    for curve in TOKENS["easing"].values():
        assert curve.startswith("cubic-bezier(")
    assert "in" not in TOKENS["easing"], "no ease-in curve for UI"


def test_greys_are_neutral() -> None:
    # True neutral darks and lights: no navy, slate or violet tint in the greys
    for variant in ("dark", "light"):
        for name in ("bg", "surface", "surface_raised", "fg", "fg_secondary", "fg_muted"):
            r, g, b = _rgb(VARIANTS[variant][name])
            assert max(r, g, b) - min(r, g, b) <= 2, f"{variant} {name} is tinted"


def test_accent_is_not_violet_or_indigo() -> None:
    import colorsys

    for variant in VARIANTS.values():
        r, g, b = _rgb(variant["accent"])
        hue = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360
        assert not 230 <= hue <= 300, f"accent hue {hue:.0f} is violet/indigo"


def _gen_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_tokens", ROOT / "scripts" / "gen_tokens.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_files_are_up_to_date() -> None:
    gen = _gen_module()
    stale = gen.stale(gen.generate())
    assert not stale, f"run python3 scripts/gen_tokens.py (stale: {[str(p) for p in stale]})"


def test_gtk_tokens_keep_legacy_names() -> None:
    gen = _gen_module()
    css = gen.gtk_css(TOKENS, "light")
    for legacy in ("bg_primary", "text_primary", "accent", "border", "accent_light"):
        assert f"@define-color {legacy} @axon_" in css
    assert "@define-color axon_accent #1d64d8;" in css


def test_unknown_placeholder_fails_loudly() -> None:
    gen = _gen_module()
    with pytest.raises(KeyError):
        gen.render_shell("a { color: {{color.nope}}; }", TOKENS, "dark")
    assert "color: #000000;" in gen.render_shell(
        "a { color: {{color.bg}}; }", TOKENS, "high-contrast"
    )
    assert "padding: 8px;" in gen.render_shell("a { padding: {{space.md}}; }", TOKENS, "dark")


def test_shell_template_uses_tokens_for_colours() -> None:
    template = (ROOT / "shell" / "axon-shell" / "stylesheet.css.in").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", template), "use {{color.*}} tokens"
    assert "rgba(" not in template, "use {{color.*}} tokens"
    assert "Windows" not in template and "Acrylic" not in template


def test_every_shell_style_class_has_a_rule() -> None:
    js = " ".join(p.read_text() for p in (ROOT / "shell" / "axon-shell").glob("*.js"))
    used = set(re.findall(r"style_class: '([^']+)'", js))
    used |= set(re.findall(r"add_style_class_name\('([^']+)'\)", js))
    classes = {c for group in used for c in group.split() if c.startswith("axon-")}
    template = (ROOT / "shell" / "axon-shell" / "stylesheet.css.in").read_text()
    missing = sorted(c for c in classes if f".{c}" not in template)
    assert not missing, missing


def test_focusable_shell_widgets_have_a_focus_ring() -> None:
    template = (ROOT / "shell" / "axon-shell" / "stylesheet.css.in").read_text()
    for cls in (
        "axon-chip",
        "axon-intentbar-stop",
        "axon-dock-icon",
        "axon-startmenu-app-item",
        "axon-startmenu-file-item",
        "axon-startmenu-power-btn",
        "axon-dock-menu-item",
        "axon-space-chip",
        "axon-suggestion",
    ):
        rule = re.search(rf"\.{cls}:focus[^{{]*\{{\n(.*?)\n\}}", template, re.DOTALL)
        assert rule and "{{color.focus_ring}}" in rule.group(1), cls


def test_app_css_font_sizes_scale_with_large_text() -> None:
    for css in (ROOT / "apps").rglob("*.css"):
        text = css.read_text()
        assert not re.search(r"font-size:\s*[\d.]+px", text), f"{css}: use pt so Large Text works"
    template = (ROOT / "shell" / "axon-shell" / "stylesheet.css.in").read_text()
    assert not re.search(r"font-size:\s*[\d.]+px", template)


ACCENTS = {k: v for k, v in TOKENS["accents"].items() if not k.startswith("$")}


@pytest.mark.parametrize("accent", sorted(ACCENTS))
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_every_accent_meets_contrast(accent: str, variant: str) -> None:
    colours = {**VARIANTS[variant], **ACCENTS[accent][variant]}
    minimum = 7.0 if variant == "high-contrast" else 4.5
    for surface in ("bg", "surface", "surface_raised"):
        ratio = contrast(colours["accent_text"], colours[surface])
        assert ratio >= minimum, f"{accent}/{variant}: accent_text on {surface} {ratio:.2f}"
    assert contrast(colours["accent_fg"], colours["accent"]) >= 4.5
    assert contrast(colours["focus_ring"], colours["bg"]) >= 3.0


def test_blue_accent_is_the_default_palette() -> None:
    for variant, values in ACCENTS["blue"].items():
        for key, value in values.items():
            assert VARIANTS[variant][key] == value, (variant, key)


def test_accent_choices_match_the_schema_and_shell() -> None:
    schema = (
        ROOT
        / "shell"
        / "axon-shell"
        / "schemas"
        / "org.gnome.shell.extensions.axon-shell.gschema.xml"
    ).read_text()
    choices = re.findall(r'<choice value="([a-z]+)"/>', schema)
    assert choices == list(ACCENTS)
    js = (ROOT / "shell" / "axon-shell" / "ui-logic.js").read_text()
    listed = re.search(r"export const ACCENTS = Object.freeze\(\[([^\]]+)\]\)", js)
    assert listed and re.findall(r"'([a-z]+)'", listed.group(1)) == list(ACCENTS)


def test_shell_accent_override_holds_only_accent_rules() -> None:
    css = (ROOT / "shell" / "axon-shell" / "accents" / "teal-dark.css").read_text()
    teal = ACCENTS["teal"]["dark"]
    assert ".axon-suggestion-selected" in css and teal["focus_ring"] in css
    assert VARIANTS["dark"]["surface"] not in css  # no unrelated rules
    assert "{{" not in css
