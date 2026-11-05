"""Run logging.

Every step writes what it did *and the evidence it checked*, because the log
doubles as the audit trail for a flow that writes into an accounting system.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

LOGGER_NAME = "fakturama"


def setup_logging(
    artifacts_dir: Optional[str] = None,
    level: int = logging.INFO,
    filename: str = "run.log",
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                           datefmt="%H:%M:%S"))
    logger.addHandler(console)

    if artifacts_dir:
        os.makedirs(artifacts_dir, exist_ok=True)
        file_handler = logging.FileHandler(
            os.path.join(artifacts_dir, filename), encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)
