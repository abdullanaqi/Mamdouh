"""Load the halal ticker allowlist and screen candidates against it."""
from __future__ import annotations

import json
import os


def load_halal_tickers(path: str) -> set[str]:
    """Read the allowlist (JSON array or one-per-line text) into a set."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Halal tickers file not found: {path!r}. "
            "Point HALAL_TICKERS_FILE at your screened list."
        )
    tickers: set[str] = set()
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data:
            sym = (item if isinstance(item, str) else item.get("ticker", "")).strip().upper()
            if sym:
                tickers.add(sym)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                                                                  
                symbol = line.split(",")[0].strip().upper()
                if symbol:
                    tickers.add(symbol)
    if not tickers:
        raise ValueError(f"No tickers parsed from {path!r}.")
    return tickers


def screen_halal_gainers(gainers: list[dict], halal: set[str]) -> list[dict]:
    """Keep only the gainers whose ticker is in the halal allowlist."""
    return [g for g in gainers if g["ticker"].upper() in halal]
