"""Structured logging via loguru. Import `log` from here, not loguru directly."""
from __future__ import annotations

import os
import sys

from loguru import logger as _logger

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _logger.remove()
    level = os.environ.get("HALAL_GAP_LOG_LEVEL", "INFO")
    _logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "| <level>{level: <7}</level> "
            "| <cyan>{name}:{line}</cyan> - {message}"
        ),
        backtrace=False,
        diagnose=False,
    )
    _CONFIGURED = True


_configure()
log = _logger
