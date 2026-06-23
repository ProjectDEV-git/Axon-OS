"""Centralized logging shim for Axon OS (root importable).

Provides `configure_app_logger` for simple, consistent logging across packages.
"""
import json
import logging
import logging.handlers
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

LOG_DIR = Path("/var/log/axon")


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line (NDJSON) for structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["exc"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        return json.dumps(entry, ensure_ascii=False)


def configure_app_logger(
    name: str,
    level: int = logging.INFO,
    log_file: str | None = None,
    json_output: bool = False,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter: logging.Formatter
    if json_output:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file is None:
        log_file = str(LOG_DIR / f"{name}.log")

    try:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass  # log directory not writable (e.g. /var/log in tests)

    return logger
