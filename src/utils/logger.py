"""Readable, consistent logging for every layer of the platform."""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO").upper(),
            format=_FORMAT,
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stdout,
        )
        _configured = True
    return logging.getLogger(name)
