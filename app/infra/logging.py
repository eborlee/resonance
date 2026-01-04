from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler


def setup_logging(log_path: str, max_bytes: int, backup_count: int):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=int(max_bytes),
        backupCount=int(backup_count),
        encoding="utf-8",
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(handler)
