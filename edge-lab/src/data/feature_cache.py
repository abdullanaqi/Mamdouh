"""Disk-backed cache of point-in-time features, labeled rows, and candidate
day-bars.

Motivation: the walk-forward grid re-evaluates the SAME (date, decision_time)
features for every parameter set. Computing them live costs ~40s per day
(25MB minute parquet load + view build + per-ticker features), which makes a
108-config grid over 3 years intractable. This cache stores, per trading day:

    data/cache/features/<date>_<hhmm>.parquet   features, ALL universe tickers
    data/cache/rows/<date>_<hhmm>.parquet       features + labels (ATR ref exit)
    data/cache/candbars/<date>.parquet          full-day minute bars for every
                                                ticker that could pass ANY
                                                strategy gate (with margin)

The cache is a pure performance layer: entries are produced by the exact
same code paths (compute_features / label_candidate) used live, and every
consumer falls back to live computation on a cache miss. Nothing about the
research protocol (point-in-time discipline, train/val/test separation)
changes. See scripts/precompute_feature_cache.py.

Candidate-bars coverage: tickers passing a LOOSER version of every gate in
strategy_search (GapRvol loose gate; ML gate with margin). If a strategy
ever picks a ticker outside this union, the lazy view in the backtester
falls back to loading the full minute file, so coverage gaps cost time,
never correctness.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pandas as pd

from src.config import CFG

FEAT_DIR = CFG.paths.cache / "features"
ROWS_DIR = CFG.paths.cache / "rows"
BARS_DIR = CFG.paths.cache / "candbars"

# Small in-memory LRUs so a val-window sweep (63 days x 3 times) served from
# disk does not re-read parquet 108 times per fold. Sizes are RAM-critical:
# one day-list is ~7MB of dicts (~3.7k tickers). Features are re-read across
# all 108 configs per fold (val+test windows: <=189 (date,time) keys), so
# that LRU must cover a window sweep; rows are consumed once per fold via
# the memoized training frame, so a token LRU suffices.
_MAX_FEAT_LISTS = 200
_MAX_ROWS_LISTS = 16
_MAX_BARS_DAYS = 8
_lru_feat: OrderedDict = OrderedDict()
_lru_rows: OrderedDict = OrderedDict()
_lru_bars: OrderedDict = OrderedDict()


def clear_memory() -> None:
    _lru_feat.clear()
    _lru_rows.clear()
    _lru_bars.clear()


def _dstr(d) -> str:
    return str(pd.Timestamp(d).date())


def _feat_path(d, hhmm: str) -> Path:
    return FEAT_DIR / f"{_dstr(d)}_{hhmm.replace(':', '')}.parquet"


def _rows_path(d, hhmm: str) -> Path:
    return ROWS_DIR / f"{_dstr(d)}_{hhmm.replace(':', '')}.parquet"


def _bars_path(d) -> Path:
    return BARS_DIR / f"{_dstr(d)}.parquet"


def _lru_get(lru: OrderedDict, key, maxn: int, loader):
    if key in lru:
        lru.move_to_end(key)
        return lru[key]
    val = loader()
    if val is not None:
        lru[key] = val
        while len(lru) > maxn:
            lru.popitem(last=False)
    return val


def _load_records(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    return df.to_dict("records")


def has_rows(d, decision_times) -> bool:
    return all(_rows_path(d, h).exists() for h in decision_times)


def load_features(d, hhmm: str) -> list[dict] | None:
    """Cached features for ALL universe tickers at (d, hhmm), or None."""
    key = (_dstr(d), hhmm)
    return _lru_get(_lru_feat, key, _MAX_FEAT_LISTS,
                    lambda: _load_records(_feat_path(d, hhmm)))


def load_rows(d, hhmm: str) -> list[dict] | None:
    """Cached features+labels for (d, hhmm), or None."""
    key = (_dstr(d), hhmm)
    return _lru_get(_lru_rows, key, _MAX_ROWS_LISTS,
                    lambda: _load_records(_rows_path(d, hhmm)))


def load_cand_bars(d, ticker: str) -> pd.DataFrame | None:
    """Full-day minute bars for `ticker` from the candidate-bars file.
    Returns None when the file is missing OR the ticker is not covered —
    callers must then fall back to the full minute file."""
    key = _dstr(d)

    def _loader():
        p = _bars_path(d)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(CFG.session.tz)
        return dict(tuple(df.groupby("ticker", sort=False)))

    by_t = _lru_get(_lru_bars, key, _MAX_BARS_DAYS, _loader)
    if by_t is None:
        return None
    bars = by_t.get(ticker)
    if bars is None:
        return None
    return bars.sort_values("ts").reset_index(drop=True)


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)


def save_day(d, feats_by_hhmm: dict[str, list[dict]],
             rows_by_hhmm: dict[str, list[dict]],
             cand_bars: pd.DataFrame) -> None:
    """Persist one day's cache entries (atomic per file)."""
    for hhmm, feats in feats_by_hhmm.items():
        df = pd.DataFrame(feats) if feats else pd.DataFrame({"ticker": []})
        _write(df, _feat_path(d, hhmm))
    for hhmm, rows in rows_by_hhmm.items():
        df = pd.DataFrame(rows) if rows else pd.DataFrame({"ticker": []})
        _write(df, _rows_path(d, hhmm))
    if cand_bars is None or not len(cand_bars):
        cand_bars = pd.DataFrame({"ticker": [], "ts": []})
    _write(cand_bars, _bars_path(d))
