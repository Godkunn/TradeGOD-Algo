"""
TradeGOD — Centralized Logging System
Provides structured, color-coded log output to console + file.
"""

import logging
import os
from pathlib import Path
from datetime import datetime

try:
    import colorlog
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_LOG_FILE = LOG_DIR / f"tradegod_{datetime.utcnow().strftime('%Y%m%d')}.log"

def get_logger(name: str = "TradeGOD") -> logging.Logger:
    """Return a configured logger with file + console handlers."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)

    # ── File Handler (always plain text) ──────────────────────────────────────
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # ── Console Handler (colored if available) ────────────────────────────────
    if HAS_COLOR:
        ch = colorlog.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s | %(levelname)-8s%(reset)s | %(cyan)s%(name)s%(reset)s | %(message)s",
            datefmt="%H:%M:%S",
            log_colors={
                "DEBUG":    "white",
                "INFO":     "green",
                "WARNING":  "yellow",
                "ERROR":    "red",
                "CRITICAL": "bold_red",
            }
        ))
    else:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        ))
    logger.addHandler(ch)

    return logger

# Module-level default logger
log = get_logger("TradeGOD")
