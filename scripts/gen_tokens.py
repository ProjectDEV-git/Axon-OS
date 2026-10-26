#!/usr/bin/env python3
"""Generate Axon's GTK and GNOME Shell styles from the design tokens.

Source: theme/tokens/axon-tokens.json (see that file for the token names).

Outputs:
  apps/shared/axon-design-tokens.css        GTK @define-color, dark (default)
  apps/shared/axon-design-tokens-light.css  GTK @define-color, light
  apps/shared/axon-design-tokens-hc.css     GTK @define-color, high contrast
  shell/axon-shell/stylesheet.css           Shell, dark (fallback)
  shell/axon-shell/stylesheet-dark.css      Shell, dark (GNOME 46+ picks it)
  shell/axon-shell/stylesheet-light.css     Shell, light
  theme/axon-gtk/gtk-dark.css               system GTK 4 theme: GTK's own default
                                            theme plus the Axon accent, focus ring
                                            and selection
  theme/axon-gtk/libadwaita.css             the same accent for libadwaita apps
                                            (installed as ~/.config/gtk-4.0/gtk.css)
  apps/shared/accents/*.css                 GTK accent overrides, one per accent and
                                            variant (Axon Preferences > Appearance)
  shell/axon-shell/accents/*.css            Shell accent overrides: only the template
                                            rules that use the accent or focus ring

GNOME Shell's St CSS has no variables or @define-color, so the Shell
stylesheet is rendered from shell/axon-shell/stylesheet.css.in, replacing
placeholders such as {{color.accent}}, {{space.md}}, {{radius.lg}},
{{font.family}}, {{font_size.md}}, {{font_weight.bold}}, {{duration.fast}},
{{focus.width}} and {{shadow}}.

Usage:
  python3 scripts/gen_tokens.py          write the files
  python3 scripts/gen_tokens.py --check  exit 1 if any file is out of date
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = ROOT / "theme" / "tokens" / "axon-tokens.json"
SHELL_TEMPLATE = ROOT / "shell" / "axon-shell" / "stylesheet.css.in"
GTK_OUT = {
    "dark": ROOT / "apps" / "shared" / "axon-design-tokens.css",
    "light": ROOT / "apps" / "shared" / "axon-design-tokens-light.css",
    "high-contrast": ROOT / "apps" / "shared" / "axon-design-tokens-hc.css",
}
GTK_ACCENT_DIR = ROOT / "apps" / "shared" / "accents"
SHELL_ACCENT_DIR = ROOT / "shell" / "axon-shell" / "accents"
ACCENT_KEYS = ("accent", "accent_hover", "accent_fg", "accent_text", "accent_subtle", "focus_ring")
GTK_THEME_OUT = ROOT / "theme" / "axon-gtk" / "gtk-dark.css"
LIBADWAITA_OUT = ROOT / "theme" / "axon-gtk" / "libadwaita.css"
SHELL_OUT = {
    ROOT / "shell" / "axon-shell" / "stylesheet.css": "dark",
    ROOT / "shell" / "axon-shell" / "stylesheet-dark.css": "dark",
    ROOT / "shell" / "axon-shell" / "stylesheet-light.css": "light",
}

# Names the existing app CSS already uses, kept as aliases of the new tokens.
LEGACY_ALIASES = {
    "bg_primary": "bg",
    "bg_secondary": "surface",
    "bg_surface": "surface_raised",
    "text_primary": "fg",
    "text_secondary": "fg_secondary",
    "text_muted": "fg_muted",
    "accent": "accent",
    "accent_hover": "accent_hover",
    "accent_active": "accent_hover",
    "accent_light": "accent_text",
    "border": "border",
    "success": "success",
    "error": "error",
    "cyan": "info",
}

_NOT_COLOURS = {"shadow"}
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)(?:\.([a-z_-]+))?\s*\}\}")


def header(kind: str) -> str:
    return (
        f"/* {kind} generated from theme/tokens/axon-tokens.json by scripts/gen_tokens.py.\n"
        " * Do not edit: change the tokens or the template and run the script. */\n"
    )


def load_tokens(path: Path = TOKENS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gtk_css(tokens: dict, variant: str) -> str:
    """@define-color lines for one variant: axon_* names plus legacy aliases."""
    colours = tokens["variants"][variant]
    lines = [header("GTK colour tokens"), f"/* Variant: {variant} */", ""]
    for name, value in colours.items():
        if name not in _NOT_COLOURS:
            lines.append(f"@define-color axon_{name} {value};")
    lines += ["", "/* Names used by older app stylesheets */"]
    for alias, target in LEGACY_ALIASES.items():
        lines.append(f"@define-color {alias} @axon_{target};")
    return "\n".join(lines) + "\n"


def render_shell(template: str, tokens: dict, variant: str) -> str:
    """Fill the Shell template's {{group.name}} placeholders for *variant*."""
    colours = tokens["variants"][variant]

    def value(match: re.Match[str]) -> str:
        group, name = match.group(1), match.group(2)
        if group == "shadow" and name is None:
            return str(colours["shadow"])
        if group == "color" and name in colours:
            return str(colours[name])
        if name is not None and group in tokens and name in tokens[group]:
            return str(tokens[group][name])
        raise KeyError(f"unknown token {match.group(0)}")

    return (
        header("Shell stylesheet")
        + f"/* Variant: {variant} */\n\n"
        + _PLACEHOLDER.sub(value, template)
    )


def accent_names(tokens: dict) -> list[str]:
    return [name for name in tokens["accents"] if not name.startswith("$")]


def gtk_accent_css(tokens: dict, accent: str, variant: str) -> str:
    """@define-color overrides for one accent; loaded after the token file."""
    colours = tokens["accents"][accent][variant]
    lines = [header("GTK accent override"), f"/* Accent: {accent}, variant: {variant} */", ""]
    lines += [f"@define-color axon_{key} {colours[key]};" for key in ACCENT_KEYS]
    lines += ["", "/* Names used by older app stylesheets */"]
    lines += [
        f"@define-color {alias} @axon_{target};"
        for alias, target in LEGACY_ALIASES.items()
        if target in ACCENT_KEYS
    ]
    # libadwaita's own widgets (switches, suggested buttons, links) in Axon apps
    lines += [
        "",
        "/* libadwaita accent names */",
        "@define-color accent_bg_color @axon_accent;",
        "@define-color accent_fg_color @axon_accent_fg;",
        "@define-color accent_color @axon_accent_text;",
    ]
    return "\n".join(lines) + "\n"


_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_ACCENT_TOKEN = re.compile(r"\{\{\s*color\.(accent\w*|focus_ring)\s*\}\}")


def accent_rules(template: str) -> str:
    """The template's rules cut down to the declarations that use the accent."""
    # Placeholders contain braces; swap them out while finding the rules
    hidden = re.sub(r"\{\{\s*([a-z_]+(?:\.[a-z_-]+)?)\s*\}\}", r"@@\1@@", template)
    out = []
    for selector, body in _RULE.findall(_COMMENT.sub("", hidden)):
        body = re.sub(r"@@([^@]+)@@", r"{{\1}}", body)
        decls = [line.strip() for line in body.split(";") if _ACCENT_TOKEN.search(line)]
        if decls:
            out.append(selector.strip() + " {\n" + "".join(f"    {d};\n" for d in decls) + "}\n")
    return "\n".join(out)


def shell_accent_css(tokens: dict, template: str, accent: str, variant: str) -> str:
    merged = {**tokens, "variants": {**tokens["variants"]}}
    merged["variants"][variant] = {
        **tokens["variants"][variant],
        **tokens["accents"][accent][variant],
    }
    body = render_shell(accent_rules(template), merged, variant)
    return body.replace("Shell stylesheet", "Shell accent override", 1).replace(
        f"/* Variant: {variant} */", f"/* Accent: {accent}, variant: {variant} */", 1
    )


def gtk_theme_css(tokens: dict) -> str:
    """The system GTK 4 theme: GTK's built-in default theme with Axon colours.

    GTK's default theme already has neutral greys; only the accent, the focus
    ring and selections change, so non-libadwaita apps look native.
    """
    c = tokens["variants"]["dark"]
    return (
        header("GTK 4 theme")
        + '@import url("resource:///org/gtk/libgtk/theme/Default/Default-dark.css");\n\n'
        + "/* Focus ring */\n"
        + f"*:focus-visible {{\n  outline-color: {c['focus_ring']};\n}}\n\n"
        + "/* Selected text and rows */\n"
        + f"selection, row:selected {{\n  background-color: {c['accent_subtle']};\n}}\n\n"
        + "/* The one main action and checked controls */\n"
        + "button.suggested-action, button.default {\n"
        + f"  background-color: {c['accent']};\n  color: {c['accent_fg']};\n}}\n\n"
        + f"button.suggested-action:hover {{\n  background-color: {c['accent_hover']};\n}}\n\n"
        + "check:checked, radio:checked, switch:checked {\n"
        + f"  background-color: {c['accent']};\n  color: {c['accent_fg']};\n}}\n\n"
        + f"link, button.link {{\n  color: {c['accent_text']};\n}}\n"
    )


def libadwaita_css(tokens: dict) -> str:
    """libadwaita apps ignore the GTK theme but read the user's gtk.css."""
    light = tokens["variants"]["light"]
    return (
        header("libadwaita accent")
        + "/* Fills that hold white text in both light and dark style; text colours\n"
        + " * stay libadwaita's own, which already differ per style. */\n"
        + f"@define-color accent_bg_color {light['accent']};\n"
        + f"@define-color accent_fg_color {light['accent_fg']};\n"
        + f"@define-color destructive_bg_color {light['error']};\n"
    )


def generate(tokens: dict | None = None, template: str | None = None) -> dict[Path, str]:
    """Every output file and its expected content."""
    tokens = tokens if tokens is not None else load_tokens()
    template = template if template is not None else SHELL_TEMPLATE.read_text(encoding="utf-8")
    out = {path: gtk_css(tokens, variant) for variant, path in GTK_OUT.items()}
    for path, variant in SHELL_OUT.items():
        out[path] = render_shell(template, tokens, variant)
    for accent in accent_names(tokens):
        for variant in tokens["variants"]:
            out[GTK_ACCENT_DIR / f"axon-accent-{accent}-{variant}.css"] = gtk_accent_css(
                tokens, accent, variant
            )
            out[SHELL_ACCENT_DIR / f"{accent}-{variant}.css"] = shell_accent_css(
                tokens, template, accent, variant
            )
    out[GTK_THEME_OUT] = gtk_theme_css(tokens)
    out[LIBADWAITA_OUT] = libadwaita_css(tokens)
    return out


def stale(files: dict[Path, str]) -> list[Path]:
    """Outputs whose content on disk differs from *files*."""
    return [
        p for p, text in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != text
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if outputs are out of date")
    args = parser.parse_args(argv)
    files = generate()
    out_of_date = stale(files)
    if args.check:
        for path in out_of_date:
            sys.stdout.write(f"out of date: {path.relative_to(ROOT)}\n")
        if out_of_date:
            sys.stdout.write("Run: python3 scripts/gen_tokens.py\n")
        return 1 if out_of_date else 0
    for path in out_of_date:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[path], encoding="utf-8")
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
