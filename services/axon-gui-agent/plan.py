"""Plan validation for the Axon GUI Agent — pure and unit-testable.

The Brain turns a natural-language desktop request into a JSON list of
operations. Only operations that pass this allowlist are executed, so a
hallucinated or malicious plan can never touch anything outside GNOME
desktop preferences, app launching, and notifications.
"""

from __future__ import annotations

import json

# gsettings schemas the agent may write to. Deliberately excludes anything
# security-sensitive (polkit, gdm, network) — preferences only.
ALLOWED_SCHEMA_PREFIXES = (
    "org.gnome.desktop.interface",
    "org.gnome.desktop.background",
    "org.gnome.desktop.screensaver",
    "org.gnome.desktop.wm.preferences",
    "org.gnome.desktop.peripherals",
    "org.gnome.desktop.session",
    "org.gnome.desktop.input-sources",
    "org.gnome.desktop.a11y",
    "org.gnome.desktop.calendar",
    "org.gnome.desktop.privacy",
    "org.gnome.desktop.notifications",
    "org.gnome.desktop.media-handling",
    "org.gnome.settings-daemon.plugins.color",  # night light
    "org.gnome.settings-daemon.plugins.power",
    "org.gnome.mutter",
    "org.gnome.shell.extensions",
    "org.gnome.nautilus",
)

VALID_OP_TYPES = {"gsettings_set", "launch_app", "notify"}

MAX_OPS = 12


def _check_op(op: dict) -> str | None:
    """Return an error string, or None when the op is allowed."""
    if not isinstance(op, dict):
        return "operation is not an object"
    op_type = op.get("type", "")
    if op_type not in VALID_OP_TYPES:
        return f"unknown operation type: {op_type!r}"

    if op_type == "gsettings_set":
        schema = str(op.get("schema", ""))
        key = str(op.get("key", ""))
        if "value" not in op:
            return "gsettings_set missing value"
        if not schema or not key:
            return "gsettings_set missing schema/key"
        if not any(schema == p or schema.startswith(p + ".") for p in ALLOWED_SCHEMA_PREFIXES):
            return f"schema not allowed: {schema}"
        if any(c in schema + key for c in ";|&$`\n"):
            return "illegal characters in schema/key"

    elif op_type == "launch_app":
        app = str(op.get("app", ""))
        if not app:
            return "launch_app missing app"
        if any(c in app for c in ";|&$`\n/"):
            return "illegal characters in app name"

    elif op_type == "notify":
        if not str(op.get("message", "")):
            return "notify missing message"

    return None


def validate_plan(raw: str) -> tuple[list[dict], list[str]]:
    """Parse + filter a Brain-generated plan.

    Returns (allowed_ops, errors). Tolerates the model wrapping the JSON in
    a markdown fence or returning {"operations": [...]}.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Strip markdown fence: find the closing ```
        first_newline = text.find("\n", 3)
        if first_newline > 0:
            rest = text[first_newline + 1 :]
            closing = rest.rfind("```")
            if closing >= 0:
                rest = rest[:closing]
            text = rest.strip()
        else:
            # No newline after opening fence — treat as single line
            text = text.strip("`").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return [], [f"plan is not valid JSON: {exc}"]

    if isinstance(data, dict):
        data = data.get("operations", data.get("ops", []))
    if not isinstance(data, list):
        return [], ["plan is not a list of operations"]

    ops: list[dict] = []
    errors: list[str] = []
    for op in data[:MAX_OPS]:
        err = _check_op(op)
        if err:
            errors.append(err)
        else:
            ops.append(op)
    if len(data) > MAX_OPS:
        errors.append(f"plan truncated to {MAX_OPS} operations")
    return ops, errors


def to_gvariant(value: object) -> str:
    """Serialise a JSON value for `gsettings set`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return json.dumps(value).replace('"', "'")
    # Escape single quotes to prevent GVariant injection
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"
