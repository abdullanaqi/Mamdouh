"""Massive flat-file loader.

Downloads day/minute aggregate flat files from Massive's S3-compatible
endpoint and normalizes them into the repo's parquet layout:

    data/raw/<prefix>/YYYY-MM-DD.csv.gz          (as downloaded)
    data/processed/minute/YYYY-MM-DD.parquet     (ticker, ts[ET], o,h,l,c,v,n)
    data/processed/daily/daily_YYYY.parquet      (ticker, date, o,h,l,c,v,n)

Truth notes:
- Flat-file aggregates are UNADJUSTED for splits/dividends. Multi-day
  features must handle this (see universe_builder.suspect_split_days).
- Flat files publish end-of-day. They CANNOT power a live same-day pick;
  that requires the Massive REST/WebSocket API (not implemented -- see
  massive_rest_client.py).
- Expected file path pattern (verify in your Massive file browser):
      <prefix>/YYYY/MM/YYYY-MM-DD.csv.gz
- Expected minute-agg columns (verify on first download):
      ticker, volume, open, close, high, low, window_start (epoch ns, UTC),
      transactions
  The normalizer detects common variants and FAILS LOUDLY on unknown
  schemas rather than guessing.
"""
from __future__ import annotations

import gzip
import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.config import CFG
from src.utils.logging import get_logger
from src.utils.validation import basic_ohlc_sanity

log = get_logger(__name__)

_COL_MAP = {
    "T": "ticker", "ticker": "ticker",
    "o": "open", "open": "open",
    "h": "high", "high": "high",
    "l": "low", "low": "low",
    "c": "close", "close": "close",
    "v": "volume", "volume": "volume",
    "n": "transactions", "transactions": "transactions",
    "t": "window_start", "window_start": "window_start", "timestamp": "window_start",
}


def _s3_client():
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as e:  # pragma: no cover
        raise ImportError("boto3 required for downloads: pip install boto3") from e
    m = CFG.massive
    if not m.access_key_id or not m.secret_access_key:
        raise RuntimeError("Set MASSIVE_ACCESS_KEY_ID / MASSIVE_SECRET_ACCESS_KEY in .env")
    return boto3.client(
        "s3", endpoint_url=m.s3_endpoint,
        aws_access_key_id=m.access_key_id,
        aws_secret_access_key=m.secret_access_key,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5}),
    )


def _key_for(prefix: str, d: date) -> str:
    return f"{prefix}/{d:%Y}/{d:%m}/{d:%Y-%m-%d}.csv.gz"


def download_day(kind: str, d: date, overwrite: bool = False) -> Path | None:
    """kind in {'minute','day'}. Returns local raw path, or None if the
    object does not exist (holiday/weekend/not yet published)."""
    prefix = CFG.massive.minute_prefix if kind == "minute" else CFG.massive.day_prefix
    key = _key_for(prefix, d)
    local = CFG.paths.raw / prefix / f"{d:%Y-%m-%d}.csv.gz"
    if local.exists() and not overwrite:
        return local
    local.parent.mkdir(parents=True, exist_ok=True)
    s3 = _s3_client()
    try:
        s3.download_file(CFG.massive.s3_bucket, key, str(local))
    except Exception as e:
        msg = str(e)
        if "404" in msg or "Not Found" in msg or "NoSuchKey" in msg:
            log.info("no file for %s %s (holiday/weekend/unpublished)", kind, d)
            return None
        raise
    return local


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    ren = {c: _COL_MAP[c] for c in df.columns if c in _COL_MAP}
    df = df.rename(columns=ren)
    required = {"ticker", "open", "high", "low", "close", "volume", "window_start"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Unknown flat-file schema; missing {missing}. Got {list(df.columns)}. "
            "Refusing to guess -- inspect the raw file and extend _COL_MAP."
        )
    if "transactions" not in df.columns:
        df["transactions"] = pd.NA
    return df


def _ts_from_window_start(s: pd.Series) -> pd.Series:
    """window_start is epoch time; detect ns vs ms vs s by magnitude."""
    x = pd.to_numeric(s, errors="raise")
    mx = float(x.iloc[0]) if len(x) else 0.0
    if mx > 1e17:
        unit = "ns"
    elif mx > 1e14:
        unit = "us"
    elif mx > 1e11:
        unit = "ms"
    else:
        unit = "s"
    return pd.to_datetime(x, unit=unit, utc=True).dt.tz_convert(CFG.session.tz)


def process_minute_file(raw_path: Path, d: date) -> Path:
    with gzip.open(raw_path, "rb") as f:
        df = pd.read_csv(io.BytesIO(f.read()))
    df = normalize_columns(df)
    df["ts"] = _ts_from_window_start(df["window_start"])
    df = df[["ticker", "ts", "open", "high", "low", "close", "volume", "transactions"]]
    df = basic_ohlc_sanity(df)
    df = df.sort_values(["ticker", "ts"]).reset_index(drop=True)
    out = CFG.paths.minute / f"{d:%Y-%m-%d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    if df.attrs.get("dropped_bad_bars"):
        log.warning("%s: dropped %d corrupt bars", d, df.attrs["dropped_bad_bars"])
    return out


def process_daily_file(raw_path: Path, d: date) -> pd.DataFrame:
    with gzip.open(raw_path, "rb") as f:
        df = pd.read_csv(io.BytesIO(f.read()))
    df = normalize_columns(df)
    df["date"] = pd.Timestamp(d)
    df = df[["ticker", "date", "open", "high", "low", "close", "volume", "transactions"]]
    return basic_ohlc_sanity(df)


def build_dataset(start: date, end: date, kinds: tuple[str, ...] = ("day", "minute")) -> None:
    """Download + process a date range. Daily rows are appended per-year."""
    daily_buf: dict[int, list[pd.DataFrame]] = {}
    d = start
    while d <= end:
        if d.weekday() < 5:
            if "minute" in kinds:
                p = download_day("minute", d)
                if p is not None:
                    process_minute_file(p, d)
            if "day" in kinds:
                p = download_day("day", d)
                if p is not None:
                    daily_buf.setdefault(d.year, []).append(process_daily_file(p, d))
        d += timedelta(days=1)
    for year, frames in daily_buf.items():
        out = CFG.paths.daily / f"daily_{year}.parquet"
        new = pd.concat(frames, ignore_index=True)
        if out.exists():
            old = pd.read_parquet(out)
            new = (
                pd.concat([old, new], ignore_index=True)
                .drop_duplicates(["ticker", "date"], keep="last")
            )
        new = new.sort_values(["date", "ticker"]).reset_index(drop=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        new.to_parquet(out, index=False)
        log.info("daily_%s.parquet: %d rows", year, len(new))


# ---------- read-side helpers used by the rest of the pipeline ----------

def load_minute_day(d: date | str) -> pd.DataFrame | None:
    d = pd.Timestamp(d).date()
    p = CFG.paths.minute / f"{d:%Y-%m-%d}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(CFG.session.tz)
    return df


def load_daily_history() -> pd.DataFrame:
    files = sorted(CFG.paths.daily.glob("daily_*.parquet"))
    if not files:
        raise FileNotFoundError("No daily parquet built yet. Run scripts/build_dataset.py")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "ticker"]).reset_index(drop=True)


def available_minute_dates() -> list[pd.Timestamp]:
    return sorted(pd.Timestamp(p.stem) for p in CFG.paths.minute.glob("*.parquet"))
