from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from src.config import CFG

_FMT = "%(asctime)s %(levelname)s %(name)s :: %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(sh)
    CFG.paths.logs.mkdir(parents=True, exist_ok=True)
    fname = CFG.paths.logs / f"{datetime.now(timezone.utc):%Y%m%d}_edge_lab.log"
    fh = logging.FileHandler(fname)
    fh.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(fh)
    logger.propagate = False
    return logger
