"""Config loader. Single source of truth for tunable parameters."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


@lru_cache(maxsize=8)
def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file from disk, cached by path.

    Example:
        >>> cfg = load_yaml(CONFIG_DIR / "settings.yaml")
        >>> isinstance(cfg, dict)
        True
    """
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}, got {type(data)}")
    return data


def settings() -> dict[str, Any]:
    """Return the parsed `config/settings.yaml`."""
    return load_yaml(CONFIG_DIR / "settings.yaml")


def halal_exclusions() -> dict[str, Any]:
    """Return the parsed `config/halal_exclusions.yaml`."""
    return load_yaml(CONFIG_DIR / "halal_exclusions.yaml")


def cache_root() -> Path:
    """Resolve the cache directory from config, creating it if needed."""
    p = REPO_ROOT / settings()["paths"]["cache_dir"]
    p.mkdir(parents=True, exist_ok=True)
    return p


def reports_dir() -> Path:
    """Resolve the reports directory, creating it if needed."""
    p = REPO_ROOT / settings()["paths"]["reports_dir"]
    p.mkdir(parents=True, exist_ok=True)
    return p
