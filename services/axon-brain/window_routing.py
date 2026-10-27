"""Window → Space classification helpers for the Brain.

``classify_known_app`` answers instantly for well-known apps, and
``score_model_answer`` turns the model's free-text reply into a Space and a
confidence. The Shell asks the user instead of moving a window when the
confidence is low.
"""

from __future__ import annotations

SPACES: tuple[str, ...] = (
    "Code",
    "Web",
    "Chat",
    "Files",
    "Media",
    "Work",
    "Personal",
    "Terminal",
    "Notes",
)
DEFAULT_SPACE = "Default"

# Confidence for each way of reaching an answer.
KNOWN_APP_CONFIDENCE = 0.95
EXACT_ANSWER_CONFIDENCE = 0.75
LOOSE_ANSWER_CONFIDENCE = 0.4

# Lower-case WM_CLASS / app-id fragments → Space. Checked in order, so put
# more specific fragments first.
_KNOWN_APPS: tuple[tuple[str, str], ...] = (
    ("code", "Code"),  # VS Code, Code - OSS
    ("codium", "Code"),
    ("jetbrains", "Code"),
    ("gnome-builder", "Code"),
    ("org.gnome.builder", "Code"),
    ("zed", "Code"),
    ("gnome-terminal", "Terminal"),
    ("org.gnome.terminal", "Terminal"),
    ("org.gnome.console", "Terminal"),
    ("kgx", "Terminal"),
    ("ptyxis", "Terminal"),
    ("axon-terminal", "Terminal"),
    ("kitty", "Terminal"),
    ("alacritty", "Terminal"),
    ("wezterm", "Terminal"),
    ("tilix", "Terminal"),
    ("firefox", "Web"),
    ("chromium", "Web"),
    ("google-chrome", "Web"),
    ("brave", "Web"),
    ("epiphany", "Web"),
    ("discord", "Chat"),
    ("telegram", "Chat"),
    ("slack", "Chat"),
    ("signal", "Chat"),
    ("element", "Chat"),
    ("nautilus", "Files"),
    ("org.gnome.files", "Files"),
    ("axon-files", "Files"),
    ("thunar", "Files"),
    ("vlc", "Media"),
    ("spotify", "Media"),
    ("gimp", "Media"),
    ("totem", "Media"),
    ("rhythmbox", "Media"),
    ("libreoffice", "Work"),
    ("onlyoffice", "Work"),
    ("thunderbird", "Personal"),
    ("evolution", "Personal"),
    ("gnome-calendar", "Personal"),
    ("obsidian", "Notes"),
    ("notion", "Notes"),
    ("gnome-text-editor", "Notes"),
    ("org.gnome.texteditor", "Notes"),
    ("gedit", "Notes"),
)


def classify_known_app(wm_class: str) -> str | None:
    """Return the Space for a well-known app, or None to ask the model."""
    key = (wm_class or "").strip().lower()
    if not key or key == "none":
        return None
    for fragment, space in _KNOWN_APPS:
        if fragment in key:
            return space
    return None


def score_model_answer(raw: str) -> tuple[str, float]:
    """Map the model's reply to ``(space, confidence)``.

    An answer that is exactly one Space name is trusted more than a reply that
    merely mentions one somewhere. Replies naming no Space, or several, get
    ``("Default", 0.0)``.
    """
    text = (raw or "").strip().strip("\"'`.").strip()
    for space in SPACES:
        if text.lower() == space.lower():
            return space, EXACT_ANSWER_CONFIDENCE
    words = {w.strip("\"'`.,:;!()[]").lower() for w in text.split()}
    named = [s for s in SPACES if s.lower() in words]
    if len(named) == 1:
        return named[0], LOOSE_ANSWER_CONFIDENCE
    return DEFAULT_SPACE, 0.0
