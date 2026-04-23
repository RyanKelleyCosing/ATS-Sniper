"""Logging configuration for ATS Sniper - max 5 log files from last 5 runs."""

import logging
import logging.handlers

from utils.runtime_paths import log_file_path


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with rotating file handler (5 backups) and console."""
    log_path = log_file_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(level)

    root.addHandler(file_handler)
    root.addHandler(console_handler)
