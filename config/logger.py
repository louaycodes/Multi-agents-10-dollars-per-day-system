"""
config/logger.py — Centralized logging configuration.

Provides three log streams:
  - logs/daily.log    : INFO+ (all activity)
  - logs/errors.log   : ERROR+ only
  - logs/transactions.log : Transaction-specific events (custom filter)

Usage:
    from config.logger import get_logger
    logger = get_logger(__name__)
"""

import logging
import logging.handlers
import os
from pathlib import Path
import colorlog

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_LOGS_DIR = _PROJECT_ROOT / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

DAILY_LOG = _LOGS_DIR / "daily.log"
ERROR_LOG = _LOGS_DIR / "errors.log"
TRANSACTION_LOG = _LOGS_DIR / "transactions.log"


# ──────────────────────────────────────────────
# Custom Filters
# ──────────────────────────────────────────────
class TransactionFilter(logging.Filter):
    """Only passes log records that contain 'TRANSACTION' in the message."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "TRANSACTION" in record.getMessage()


class NoTransactionFilter(logging.Filter):
    """Excludes transaction-only records from daily/error logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "TRANSACTION" not in record.getMessage()


# ──────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────
_FILE_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)-20s | %(funcName)-25s | %(message)s"
)
_CONSOLE_FORMAT = (
    "%(log_color)s%(asctime)s%(reset)s | "
    "%(log_color)s%(levelname)-8s%(reset)s | "
    "%(cyan)s%(name)-20s%(reset)s | %(message)s"
)

_file_formatter = logging.Formatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

_console_formatter = colorlog.ColoredFormatter(
    _CONSOLE_FORMAT,
    datefmt="%H:%M:%S",
    log_colors={
        "DEBUG": "white",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold_red",
    },
)


# ──────────────────────────────────────────────
# Handler factory
# ──────────────────────────────────────────────
def _rotating_handler(
    path: Path,
    level: int,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 7,
) -> logging.handlers.RotatingFileHandler:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(_file_formatter)
    return handler


# ──────────────────────────────────────────────
# Root logger setup (call once at startup)
# ──────────────────────────────────────────────
def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger. Call once in main.py."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Capture everything; handlers filter

    if root.handlers:
        return  # Already configured

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(_console_formatter)
    console.addFilter(NoTransactionFilter())
    root.addHandler(console)

    # Daily log (INFO+, no transactions)
    daily = _rotating_handler(DAILY_LOG, logging.INFO)
    daily.addFilter(NoTransactionFilter())
    root.addHandler(daily)

    # Error log (ERROR+ only)
    error = _rotating_handler(ERROR_LOG, logging.ERROR, backup_count=14)
    root.addHandler(error)

    # Transaction log (all levels, transactions only)
    txn = _rotating_handler(TRANSACTION_LOG, logging.DEBUG)
    txn.addFilter(TransactionFilter())
    root.addHandler(txn)

    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Call setup_logging() before first use."""
    return logging.getLogger(name)
