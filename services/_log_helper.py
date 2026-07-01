"""Shared logger resolution helper for Axon OS services.

Eliminates the repeated try/except ImportError boilerplate found in every
service file.  Usage::

    from _log_helper import resolve_logger
    log = resolve_logger("axon-brain")
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def resolve_logger(
    name: str,
    *,
    level: int = logging.INFO,
    log_file: str | None = None,
    json_output: bool = False,
) -> logging.Logger:
    """Return a configured logger, falling back to stdlib if axon_logger is missing.

    Args:
        name: Logger name (typically the service name).
        level: Logging level.
        log_file: Optional log file path.
        json_output: Whether to use JSON formatting.

    Returns:
        Configured logger instance.
    """
    try:
        from axon_logger import configure_app_logger

        return configure_app_logger(name, level=level, log_file=log_file, json_output=json_output)
    except ImportError:
        # Ensure axon_logger is discoverable from the repo layout
        _root = Path(__file__).resolve().parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        try:
            from axon_logger import configure_app_logger

            return configure_app_logger(
                name, level=level, log_file=log_file, json_output=json_output
            )
        except ImportError:
            logging.basicConfig(level=level)
            return logging.getLogger(name)
