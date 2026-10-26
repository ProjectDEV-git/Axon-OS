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


def generate(tokens: dict | None = None, template: str | None = None) -> dict[Path, str]:
    """Every output file and its expected content."""
    tokens = tokens if tokens is not None else load_tokens()
    template = template if template is not None else SHELL_TEMPLATE.read_text(encoding="utf-8")
    out = {path: gtk_css(tokens, variant) for variant, path in GTK_OUT.items()}
    for path, variant in SHELL_OUT.items():
        out[path] = render_shell(template, tokens, variant)
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
        path.write_text(files[path], encoding="utf-8")
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
