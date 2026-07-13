"""Tests for _log_helper — shared logger resolution helper."""

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure services/ is on sys.path for imports
_services_dir = str(Path(__file__).resolve().parent.parent / "services")
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)


class TestResolveLogger:
    """Test the resolve_logger function from _log_helper."""

    def test_resolve_logger_returns_logger(self):
        from _log_helper import resolve_logger

        logger = resolve_logger("test-logger")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test-logger"

    def test_resolve_logger_default_level(self):
        from _log_helper import resolve_logger

        logger = resolve_logger("test-default-level")
        assert logger.level == logging.INFO

    def test_resolve_logger_custom_level(self):
        from _log_helper import resolve_logger

        logger = resolve_logger("test-debug-level", level=logging.DEBUG)
        assert logger.level == logging.DEBUG

    def test_resolve_logger_fallback_to_stdlib(self):
        """When axon_logger is not importable, resolve_logger falls back to stdlib."""
        from _log_helper import resolve_logger

        # Force ImportError for axon_logger
        with patch.dict(sys.modules, {"axon_logger": None}):
            logger = resolve_logger("test-fallback")
            assert isinstance(logger, logging.Logger)
            assert logger.name == "test-fallback"

    def test_resolve_logger_idempotent(self):
        """Calling resolve_logger with the same name returns the same logger."""
        from _log_helper import resolve_logger

        logger1 = resolve_logger("test-idempotent")
        logger2 = resolve_logger("test-idempotent")
        assert logger1 is logger2
