"""Sanitising text that crosses the Brain boundary (AI output, context, URLs)."""

import re
import unicodedata

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MAX_CONTEXT_LEN = 500

# Patterns commonly used in prompt injection attacks.
_INJECTION_PATTERNS = re.compile(
    r"(ignore previous|ignore all previous|you are now|system:|"
    r"assistant:|IMPORTANT:|disregard.*instructions|new instructions|"
    r"override.*system|forget everything)",
    re.IGNORECASE,
)


def _require_http_url(url: str) -> None:
    """Reject non-HTTP(S) URLs; urllib would also open file:// and custom schemes."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing non-HTTP URL: {url!r}")


def _sanitize_output(text: str) -> str:
    """Strip ANSI escape sequences and null bytes from AI output."""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\x00", "")
    return text


def _sanitize_context(context: str) -> str:
    """Sanitize and wrap context before embedding in system prompt.

    Strips null bytes, normalizes Unicode to NFKD (to catch homoglyph
    bypasses), removes common prompt-injection patterns, truncates to
    _MAX_CONTEXT_LEN chars, and wraps in untrusted tags so the model
    treats the content as inert data.
    """
    safe = context.replace("\x00", "")
    safe = unicodedata.normalize("NFKD", safe)
    safe = _INJECTION_PATTERNS.sub("", safe)
    if len(safe) > _MAX_CONTEXT_LEN:
        safe = safe[:_MAX_CONTEXT_LEN]
    return f"<untrusted_context>{safe}</untrusted_context>"
