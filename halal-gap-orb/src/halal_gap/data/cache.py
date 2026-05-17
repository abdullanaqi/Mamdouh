"""Parquet-on-disk cache for FMP responses. Cache-first lookup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from halal_gap.utils.config import cache_root
from halal_gap.utils.logging import log


def _safe_key(payload: dict[str, Any]) -> str:
    """Hash a parameter dict into a short stable filename component."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:16]


def cache_path(endpoint: str, key: dict[str, Any]) -> Path:
    """Compute the on-disk parquet path for a given endpoint+params."""
    safe_endpoint = endpoint.replace("/", "__").replace("?", "_")
    return cache_root() / safe_endpoint / f"{_safe_key(key)}.parquet"


def cache_read(endpoint: str, key: dict[str, Any]) -> pd.DataFrame | None:
    """Return cached DataFrame if present, else None."""
    p = cache_path(endpoint, key)
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except (OSError, ValueError) as exc:
        log.warning(f"cache read failed at {p}: {exc}")
        return None


def cache_write(endpoint: str, key: dict[str, Any], df: pd.DataFrame) -> Path:
    """Persist `df` to the cache for the given endpoint+params."""
    p = cache_path(endpoint, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p
