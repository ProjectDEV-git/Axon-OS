"""Internationalization (i18n) helper for Axon OS services.

Provides gettext setup for Python services with fallback to English.
"""

import gettext
import os
from collections.abc import Callable
from pathlib import Path

# Default locale directory
_LOCALE_DIR = Path("/usr/share/locale")
_DOMAIN = "axon-os"

# Fallback translations (when .mo files are not available)
_FALLBACK_TRANSLATIONS = {
    "What do you want to do?": "What do you want to do?",
    "Thinking…": "Thinking…",
    "AI service unavailable. Is Axon Brain running?": "AI service unavailable. Is Axon Brain running?",
    "Start Menu": "Start Menu",
    "Intent Bar (Search / AI)": "Intent Bar (Search / AI)",
    "Empty workspace": "Empty workspace",
    "Workspace active": "Workspace active",
    "Error": "Error",
    "Warning": "Warning",
    "Info": "Info",
    "OK": "OK",
    "Cancel": "Cancel",
    "Yes": "Yes",
    "No": "No",
    "Close": "Close",
    "Save": "Save",
    "Delete": "Delete",
}


def setup_i18n(domain: str = _DOMAIN, locale_dir: str | None = None) -> gettext.NullTranslations:
    """Set up internationalization for the current module.

    Args:
        domain: The gettext domain (default: 'axon-os')
        locale_dir: Directory containing locale files (default: /usr/share/locale)

    Returns:
        A gettext translation object.
    """
    if locale_dir is None:
        locale_dir = str(_LOCALE_DIR)

    # Get the current language from environment
    lang = os.environ.get("LANG", "en_US.UTF-8").split(".")[0]
    language = os.environ.get("LANGUAGE", lang).split(":")[0]

    try:
        translation = gettext.translation(
            domain,
            localedir=locale_dir,
            languages=[language],
            fallback=True,
        )
    except Exception:
        translation = gettext.NullTranslations()

    return translation


def get_translator(domain: str = _DOMAIN, locale_dir: str | None = None) -> "Callable[[str], str]":
    """Get a translator function for the given domain.

    Returns:
        A function that translates strings.
    """
    translation = setup_i18n(domain, locale_dir)
    return translation.gettext


# Convenience function for quick translations
_ = get_translator()


def translate(text: str) -> str:
    """Translate a string using the default domain.

    Args:
        text: The string to translate.

    Returns:
        The translated string, or the original if no translation is found.
    """
    return str(_(text))
