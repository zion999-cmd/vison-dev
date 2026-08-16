"""
Centralized logging configuration for Vision Perception Runtime.

Usage:
    from runtime.utils.logging_config import setup_logging
    setup_logging()  # call once at startup

All modules use standard logging.getLogger(__name__) and inherit
the configuration set up here.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import (
    LOG_LEVEL, LOG_DIR, LOG_FILE_MAX_BYTES, LOG_FILE_BACKUP_COUNT,
)

# Color codes for console output
_COLORS = {
    "DEBUG":    "\033[90m",   # grey
    "INFO":     "\033[0m",    # default
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[1;31m", # bold red
}
_RESET = "\033[0m"
_LEVEL_COLOR = {
    "L1": "\033[36m",  # cyan
    "L2": "\033[36m",
    "L3": "\033[34m",  # blue
    "L4": "\033[35m",  # magenta
    "L5": "\033[33m",  # yellow
    "L6": "\033[31m",  # red
    "Intention": "\033[35m",
    "Memory": "\033[33m",
    "API": "\033[32m",  # green
    "EventBus": "\033[90m",
}


class _ColoredFormatter(logging.Formatter):
    """Console formatter with color and compact layout."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = _COLORS.get(level, "")
        layer = record.name.split(".")[0] if "." in record.name else record.name
        layer_color = _LEVEL_COLOR.get(layer, "")

        ts = self.formatTime(record, self.datefmt)
        header = f"{color}{ts} [{level:<7}]{_RESET} {layer_color}{record.name:<18}{_RESET}"
        msg = record.getMessage()

        return f"{header}  {msg}"


class _FileFormatter(logging.Formatter):
    """Detailed file formatter with full module path and line number."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        return (
            f"{ts} [{record.levelname:<7}] "
            f"{record.name}:{record.lineno}  {record.getMessage()}"
        )


def setup_logging(
    console_level: Optional[int] = None,
    file_level: Optional[int] = None,
) -> None:
    """
    Configure logging for the entire runtime.

    Sets up:
      - Console handler (compact, colored output)
      - File handler (detailed output with rotation)

    Called once at startup from main.py.
    """
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    if console_level is None:
        console_level = level
    if file_level is None:
        file_level = logging.DEBUG  # always log DEBUG to file

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # root captures all; handlers filter
    root.handlers.clear()

    # ── Console handler ──
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(_ColoredFormatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)-18s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # ── File handler (with rotation) ──
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"runtime_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(_FileFormatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s:%(lineno)d  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    # ── Link log file as "latest.log" ──
    latest_link = log_dir / "latest.log"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink()
    latest_link.symlink_to(log_file.name)

    # Suppress noisy third-party libraries
    for noisy in ("urllib3", "requests", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging initialized (console=%s, file=DEBUG → %s)",
                logging.getLevelName(console_level), log_file)
