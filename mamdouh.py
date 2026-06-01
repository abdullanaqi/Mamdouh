import json
import math
import os
import pickle
import queue
import re
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from massive import RESTClient, WebSocketClient
from massive.rest.models import Agg, TickerSnapshot
from sklearn.ensemble import GradientBoostingRegressor, RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import classification_report, mean_absolute_error, r2_score

# Scope the warning filter — blanket ignore hides real convergence/data issues
# for a model we trust with capital.
warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
load_dotenv(Path(__file__).parent / ".env")


BASE_DIR        = Path(__file__).parent
DB_PATH         = BASE_DIR / "data" / "market_data.duckdb"
MODEL_PATH      = BASE_DIR / "results" / "models.pkl"
HALAL_JSON      = BASE_DIR / "results" / "halal_stocks.json"
LOG_PATH        = BASE_DIR / "results" / "live_log.csv"
SESSION_LOG_DIR = BASE_DIR / "results" / "sessions"
STATE_PATH      = BASE_DIR / "results" / "live_state.json"
CSV_OUT         = BASE_DIR / "results" / "features_dataset.csv"

TOP_N_WATCH       = 20
MAX_POSITIONS     = 2
CONFIRM_BARS      = 2
MIN_PRICE         = 5.0
MIN_TRAIN_DAYS    = 30
PREMARKET_START_H = 4
OPEN_HOUR_ET      = 9
OPEN_MINUTE_ET    = 30
POST_OPEN_MINS    = 30
CLOSE_HOUR_ET     = 15
CLOSE_MINUTE_ET   = 55

# Reuse a cached model only if it is no more than this many trading days behind the
# available history. 0 = retrain whenever new history exists.
MODEL_STALE_TOLERANCE_DAYS = 0

IBKR_ENABLED      = False
IBKR_HOST         = "127.0.0.1"
IBKR_PORT         = 7497
IBKR_CLIENT_ID    = 1
# Capital is split into IBKR_SPLIT slices (alloc = cash / IBKR_SPLIT per entry) while at most
# MAX_POSITIONS are held — so at most MAX_POSITIONS/IBKR_SPLIT of capital is deployed. The
# headroom is an intentional safety margin against gaps/slippage.
IBKR_SPLIT        = 6

FEATURES = [
    # premarket — known before market open
    "pm_pct", "pm_momentum", "pm_vol_surge",
    # news — strictly pre-open (prior session after-hours through today's 09:30 ET)
    "news_sentiment", "news_count",
    "has_earnings", "has_fda",
    # first 5 minutes after open — captured before entry confirmation
    "open5_range_pct", "open5_vwap", "open5_volume", "open5_pct",
]

SENTIMENT_MAP = {
    "positive": 1.0, "bullish": 1.0, "strong_positive": 1.0,
    "neutral":  0.0, "mixed":   0.0,
    "negative": -1.0, "bearish": -1.0, "strong_negative": -1.0,
}

_EARNINGS_KW = re.compile(
    r"\b(earnings?|EPS|quarterly (results?|report)|revenue|guidance|beat|miss"
    r"|Q[1-4]\s+20\d\d|fiscal (quarter|year)|profit|loss per share)\b",
    re.IGNORECASE,
)
_FDA_KW = re.compile(
    r"\b(FDA|PDUFA|NDA|BLA|IND|sNDA|sBLA|510.?k"
    r"|approval|approv(ed|al)|clinical trial|phase [123]"
    r"|drug application|biologics license|breakthrough therapy)\b",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

class TeeLogger:
    def __init__(self, filepath: Path):
        self._file   = open(filepath, "w", encoding="utf-8", buffering=1)
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout   = self
        sys.stderr   = self

    def write(self, msg: str):
        self._stdout.write(msg)
        self._file.write(msg)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        sys.stdout = self._stdout
        sys.stderr = self._stderr
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def start_session_log(today: datetime, mode: str) -> TeeLogger:
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    fname = SESSION_LOG_DIR / f"session_{today.strftime('%Y-%m-%d')}_{mode}.log"
    logger = TeeLogger(fname)
    print(f"Session log → {fname}")
    return logger


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

_tg_warned = False


def _tg_send(text: str):
    global _tg_warned
    token    = os.getenv("TELEGRAM_TOKEN_V1", "").strip()
    chat_ids = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chat_ids:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for cid in chat_ids:
        try:
            requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "HTML"}, timeout=5)
        except Exception as e:
            if not _tg_warned:
                print(f"  [telegram] send failed (further failures suppressed): {e}")
                _tg_warned = True


def tg(text: str):
    print(text)
    _tg_send(text)


# ══════════════════════════════════════════════════════════════════════════════
# MASSIVE REST CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class MassiveClient:
    def __init__(self):
        self._client = RESTClient(os.getenv("MASSIVE_API_KEY", "").strip())

    def snapshot_all(self) -> list[TickerSnapshot]:
        return list(self._client.get_snapshot_all("stocks"))

    def bars(self, ticker: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
        # from_dt / to_dt are ET-aware datetimes (now_et()-derived). Convert to UTC via the
        # tz database so the boundary is DST-correct year-round (was a fixed +4h offset).
        from_ms = int(from_dt.astimezone(timezone.utc).timestamp() * 1000)
        to_ms   = int(to_dt.astimezone(timezone.utc).timestamp() * 1000)
        aggs = self._client.get_aggs(
            ticker, multiplier=1, timespan="minute",
            from_=from_ms, to=to_ms, adjusted=False, sort="asc", limit=50000,
        )
        result = []
        for a in aggs:
            if not isinstance(a, Agg):
                continue
            result.append({
                "ts": (a.timestamp or 0) / 1000,
                "open":   a.open   or 0.0,
                "close":  a.close  or 0.0,
                "high":   a.high   or 0.0,
                "low":    a.low    or 0.0,
                "volume": a.volume or 0,
            })
        return result

    def price(self, ticker: str) -> tuple[float, str]:
        snaps = list(self._client.get_snapshot_all("stocks", tickers=[ticker]))
        for s in snaps:
            if not isinstance(s, TickerSnapshot):
                continue
            lt = s.last_trade
            if lt and getattr(lt, "price", None):
                px    = float(lt.price)
                ts_ms = getattr(lt, "timestamp", None) or getattr(lt, "t", None)
                if ts_ms:
                    ts_et = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(_ET)
                    return px, ts_et.strftime("%H:%M:%S")
                return px, now_et().strftime("%H:%M:%S")
            if s.day and getattr(s.day, "close", None):
                return float(s.day.close), now_et().strftime("%H:%M:%S")
        return 0.0, now_et().strftime("%H:%M:%S")

    def news(self, ticker: str, from_dt: datetime, to_dt: datetime | None = None) -> list[dict]:
        from_utc = from_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        articles = self._client.list_ticker_news(ticker, published_utc_gte=from_utc, limit=50)
        result = []
        for a in articles:
            published = _parse_published(getattr(a, "published_utc", "") or "")
            # Enforce the upper bound so live news matches the leak-free training window.
            if to_dt is not None and published is not None and published >= to_dt:
                continue
            insights  = getattr(a, "insights", None) or []
            sentiment = ""
            if insights:
                first = insights[0]
                sentiment = (first.get("sentiment", "") if isinstance(first, dict)
                             else getattr(first, "sentiment", "")) or ""
            title = getattr(a, "title", "") or ""
            result.append({
                "title":       title,
                "sentiment":   sentiment,
                "published":   published,
                "is_earnings": bool(_EARNINGS_KW.search(title)),
                "is_fda":      bool(_FDA_KW.search(title)),
            })
        return result


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

_ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(_ET)


def _parse_published(s: str) -> datetime | None:
    """Parse a Massive published_utc ISO-8601 string into an ET-aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_ET)
    except Exception:
        return None


def _wait_until(target: datetime):
    """Block until ET wall-clock reaches `target` (ET-aware), polling gently."""
    while now_et() < target:
        remaining = (target - now_et()).total_seconds()
        time.sleep(min(30.0, max(1.0, remaining)))


def _json_safe(obj):
    """Recursively replace NaN floats with None so json.dumps emits spec-compliant JSON."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def _safe_tickers(tickers) -> list[str]:
    """Validate tickers before SQL interpolation to prevent injection."""
    return [t for t in tickers if _TICKER_RE.match(str(t))]


def _ticker_sql_list(tickers) -> str:
    return ",".join(f"'{t}'" for t in _safe_tickers(tickers))


def load_halal() -> set:
    with open(HALAL_JSON) as f:
        return {e["ticker"] for e in json.load(f)}


def utc_ns(date_str: str, hour_et: int, minute_et: int = 0) -> int:
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=_ET, hour=0, minute=0, second=0, microsecond=0
    )
    dt = base + timedelta(hours=hour_et, minutes=minute_et)
    return int(dt.timestamp() * 1e9)


def _prev_weekday(dt: datetime) -> datetime:
    """Most recent weekday strictly before `dt` (holidays ignored — used only as a news
    lower bound, which is harmless if slightly early)."""
    prev = dt - timedelta(days=1)
    while prev.weekday() >= 5:  # Saturday=5, Sunday=6
        prev -= timedelta(days=1)
    return prev


# ══════════════════════════════════════════════════════════════════════════════
# DATA PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

# DuckDB expression that maps the UTC nanosecond `window_start` to its ET calendar date,
# DST-correct via the tz database (replaces a hardcoded offset subtraction).
_ET_DATE_SQL = "CAST(to_timestamp(window_start / 1e9) AT TIME ZONE 'America/New_York' AS DATE)"


def _trading_dates(con) -> list[str]:
    rows = con.execute(f"""
        SELECT DISTINCT {_ET_DATE_SQL} AS d
        FROM minute_bars ORDER BY d
    """).fetchall()
    return [str(r[0]) for r in rows if r[0]]


def _already_processed(con) -> set:
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT date FROM features").fetchall()}
    except Exception:
        return set()


def _ensure_features_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS features (
            date VARCHAR, ticker VARCHAR,
            pm_open DOUBLE, pm_close DOUBLE, pm_high DOUBLE, pm_low DOUBLE, pm_volume DOUBLE,
            pm_pct DOUBLE, pm_momentum DOUBLE, pm_range_pct DOUBLE, pm_vol_surge DOUBLE,
            news_sentiment DOUBLE, news_count INTEGER, has_earnings INTEGER, has_fda INTEGER,
            open5_pct DOUBLE, open5_range_pct DOUBLE, open5_vwap DOUBLE, open5_volume DOUBLE,
            open_price DOUBLE, post_open_close DOUBLE, label INTEGER,
            intraday_open DOUBLE, intraday_close DOUBLE, intraday_high DOUBLE,
            intraday_low DOUBLE, intraday_volume DOUBLE, intraday_pct DOUBLE,
            intraday_range_pct DOUBLE, intraday_vwap DOUBLE, eod_label INTEGER
        )
    """)


def _premarket_features(con, halal: set, date_str: str) -> pd.DataFrame:
    ticker_list = _ticker_sql_list(halal)
    s_ns   = utc_ns(date_str, PREMARKET_START_H)
    e_ns   = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    mid_ns = s_ns + 15 * 60 * 1_000_000_000

    df = con.execute(f"""
        SELECT ticker, window_start AS ts, open, close, high, low, volume
        FROM minute_bars
        WHERE window_start >= {s_ns} AND window_start < {e_ns}
          AND ticker IN ({ticker_list})
        ORDER BY window_start
    """).fetchdf()

    if df.empty:
        return pd.DataFrame()

    rows = []
    for ticker, g in df.groupby("ticker"):
        g = g.sort_values("ts")
        pm_open = g["open"].iloc[0]
        if pm_open == 0:
            continue
        pm_close = g["close"].iloc[-1]
        pm_high  = g["high"].max()
        pm_low   = g["low"].min()
        pm_vol   = g["volume"].sum()
        pm_pct   = (pm_close - pm_open) / pm_open * 100
        pm_range = (pm_high - pm_low) / pm_open * 100
        first = g[g["ts"] < mid_ns]
        last  = g[g["ts"] >= mid_ns]
        f_ret = ((first["close"].iloc[-1] - first["open"].iloc[0]) / first["open"].iloc[0] * 100
                 if not first.empty and first["open"].iloc[0] != 0 else 0.0)
        l_ret = ((last["close"].iloc[-1] - last["open"].iloc[0]) / last["open"].iloc[0] * 100
                 if not last.empty and last["open"].iloc[0] != 0 else 0.0)
        rows.append({
            "ticker": ticker,
            "pm_open": pm_open, "pm_close": pm_close,
            "pm_high": pm_high, "pm_low": pm_low, "pm_volume": pm_vol,
            "pm_pct": pm_pct, "pm_momentum": l_ret - f_ret, "pm_range_pct": pm_range,
        })
    return pd.DataFrame(rows)


def _volume_surge_col(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df
    ticker_list = _ticker_sql_list(df["ticker"])
    date_dt = datetime.strptime(date_str, "%Y-%m-%d")
    hist: dict[str, list] = {t: [] for t in df["ticker"]}
    days_found = 0
    for d in range(1, 15):
        past = (date_dt - timedelta(days=d)).strftime("%Y-%m-%d")
        s_ns = utc_ns(past, PREMARKET_START_H)
        e_ns = utc_ns(past, OPEN_HOUR_ET, OPEN_MINUTE_ET)
        rows = con.execute(f"""
            SELECT ticker, SUM(volume) FROM minute_bars
            WHERE window_start >= {s_ns} AND window_start < {e_ns}
              AND ticker IN ({ticker_list})
            GROUP BY ticker
        """).fetchall()
        if rows:
            days_found += 1
            for ticker, vol in rows:
                if ticker in hist:
                    hist[ticker].append(vol)
            if days_found >= 5:
                break
    avg = {t: sum(v) / len(v) for t, v in hist.items() if v}
    df["pm_vol_surge"] = df.apply(
        lambda r: r["pm_volume"] / avg[r["ticker"]] if r["ticker"] in avg and avg[r["ticker"]] > 0 else 1.0,
        axis=1,
    )
    return df


def _news_bounds(con, date_str: str) -> tuple[str, str]:
    """News eligible for a trading day spans [prior trading day 16:00 ET, this day 09:30 ET).
    The upper bound (the open) is what prevents look-ahead leakage from intraday/after-close
    reaction articles; the lower bound reaches back to the prior session's after-hours so
    post-close earnings drops are captured.

    Returns timestamp strings for SQL comparison against `published_et`.
    NOTE: assumes market_news.published_et is stored in ET (matches the column name and the
    existing CAST(published_et AS DATE) usage). If it is actually UTC, convert these bounds.
    """
    upper = f"{date_str} 09:30:00"
    row = con.execute(f"""
        SELECT MAX({_ET_DATE_SQL}) FROM minute_bars
        WHERE {_ET_DATE_SQL} < DATE '{date_str}'
    """).fetchone()
    prior_date = row[0] if row and row[0] else None
    if prior_date is None:
        prior_date = _prev_weekday(datetime.strptime(date_str, "%Y-%m-%d")).date()
    lower = f"{prior_date} 16:00:00"
    return lower, upper


def _sentiment_col(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        df["news_sentiment"] = 0.0
        df["news_count"]     = 0
        df["has_earnings"]   = 0
        df["has_fda"]        = 0
        return df
    ticker_list  = _ticker_sql_list(df["ticker"])
    lower, upper = _news_bounds(con, date_str)
    try:
        rows = con.execute(f"""
            SELECT ticker, sentiment, title FROM market_news
            WHERE ticker IN ({ticker_list})
              AND published_et >= TIMESTAMP '{lower}'
              AND published_et <  TIMESTAMP '{upper}'
        """).fetchall()
    except Exception:
        df["news_sentiment"] = 0.0
        df["news_count"]     = 0
        df["has_earnings"]   = 0
        df["has_fda"]        = 0
        return df
    per:      dict[str, list] = {}
    earnings: dict[str, int]  = {}
    fda:      dict[str, int]  = {}
    for ticker, val, title in rows:
        title = title or ""
        if bool(_EARNINGS_KW.search(title)):
            earnings[ticker] = 1
        if bool(_FDA_KW.search(title)):
            fda[ticker] = 1
        if val is None:
            continue
        # Decide string-label vs numeric score per row (robust to mixed types in the column).
        if isinstance(val, str):
            score = SENTIMENT_MAP.get(val.lower().strip(), 0.0)
        else:
            try:
                score = float(val)
            except (TypeError, ValueError):
                continue
        per.setdefault(ticker, []).append(score)
    df["news_sentiment"] = df["ticker"].map({t: np.mean(v) for t, v in per.items()}).fillna(0.0)
    df["news_count"]     = df["ticker"].map({t: len(v) for t, v in per.items()}).fillna(0).astype(int)
    df["has_earnings"]   = df["ticker"].map(earnings).fillna(0).astype(int)
    df["has_fda"]        = df["ticker"].map(fda).fillna(0).astype(int)
    return df


def _post_open_label(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df
    ticker_list = _ticker_sql_list(df["ticker"])
    open_s = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    open_e = open_s + 60 * 1_000_000_000
    post_e = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET + POST_OPEN_MINS)
    opens  = con.execute(f"""
        SELECT ticker, open AS open_price FROM minute_bars
        WHERE window_start >= {open_s} AND window_start < {open_e}
          AND ticker IN ({ticker_list})
    """).fetchdf()
    closes = con.execute(f"""
        SELECT ticker, LAST(close ORDER BY window_start) AS post_close FROM minute_bars
        WHERE window_start >= {open_e} AND window_start < {post_e}
          AND ticker IN ({ticker_list})
        GROUP BY ticker
    """).fetchdf()
    labeled = opens.merge(closes, on="ticker", how="inner")
    labeled["label"] = (labeled["post_close"] > labeled["open_price"]).astype(int)
    labeled = labeled.rename(columns={"post_close": "post_open_close"})
    return df.merge(labeled[["ticker", "open_price", "post_open_close", "label"]], on="ticker", how="left")


def _open5_features(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """First 5 minutes after open — safe to use as features since entry is confirmed after this
    window. NOTE: this 09:30–09:35 window sits at the start of the label window (09:31–10:00);
    it is known before the label resolves, so it is leak-free. Revisit if entry timing moves."""
    if df.empty:
        return df
    ticker_list = _ticker_sql_list(df["ticker"])
    s_ns = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    e_ns = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET + 5)
    bars = con.execute(f"""
        SELECT ticker,
            FIRST(open  ORDER BY window_start) AS o5_open,
            LAST (close ORDER BY window_start) AS o5_close,
            MAX(high)   AS o5_high,
            MIN(low)    AS o5_low,
            SUM(volume) AS o5_vol,
            SUM(close * volume) / NULLIF(SUM(volume), 0) AS o5_vwap
        FROM minute_bars
        WHERE window_start >= {s_ns} AND window_start < {e_ns}
          AND ticker IN ({ticker_list})
        GROUP BY ticker
    """).fetchdf()
    if bars.empty:
        for col in ("open5_pct", "open5_range_pct", "open5_vwap", "open5_volume"):
            df[col] = 0.0
        return df
    ref = bars["o5_open"].replace(0, np.nan)
    bars["open5_pct"]       = (bars["o5_close"] - bars["o5_open"]) / ref * 100
    bars["open5_range_pct"] = (bars["o5_high"]  - bars["o5_low"])  / ref * 100
    bars["open5_vwap"]      = (bars["o5_vwap"]  - bars["o5_open"]) / ref * 100  # % distance from open
    bars["open5_volume"]    = bars["o5_vol"]
    return df.merge(
        bars[["ticker", "open5_pct", "open5_range_pct", "open5_vwap", "open5_volume"]],
        on="ticker", how="left",
    ).fillna({"open5_pct": 0.0, "open5_range_pct": 0.0, "open5_vwap": 0.0, "open5_volume": 0.0})


def _intraday_features(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df
    ticker_list = _ticker_sql_list(df["ticker"])
    s_ns = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    e_ns = utc_ns(date_str, CLOSE_HOUR_ET, CLOSE_MINUTE_ET + 1)
    intra = con.execute(f"""
        SELECT ticker,
            FIRST(open  ORDER BY window_start) AS intraday_open,
            LAST (close ORDER BY window_start) AS intraday_close,
            MAX(high)   AS intraday_high,
            MIN(low)    AS intraday_low,
            SUM(volume) AS intraday_volume,
            SUM(close * volume) / NULLIF(SUM(volume), 0) AS intraday_vwap
        FROM minute_bars
        WHERE window_start >= {s_ns} AND window_start < {e_ns}
          AND ticker IN ({ticker_list})
        GROUP BY ticker
    """).fetchdf()
    if intra.empty:
        for col in ("intraday_open", "intraday_close", "intraday_high", "intraday_low",
                    "intraday_volume", "intraday_pct", "intraday_range_pct", "intraday_vwap", "eod_label"):
            df[col] = np.nan
        return df
    intra["intraday_pct"]       = (intra["intraday_close"] - intra["intraday_open"]) / intra["intraday_open"].replace(0, np.nan) * 100
    intra["intraday_range_pct"] = (intra["intraday_high"]  - intra["intraday_low"])  / intra["intraday_open"].replace(0, np.nan) * 100
    intra["eod_label"]          = (intra["intraday_close"] > intra["intraday_open"]).astype(int)
    return df.merge(
        intra[["ticker", "intraday_open", "intraday_close", "intraday_high", "intraday_low",
               "intraday_volume", "intraday_pct", "intraday_range_pct", "intraday_vwap", "eod_label"]],
        on="ticker", how="left",
    )


def _process_date(con, halal: set, date_str: str) -> int:
    df = _premarket_features(con, halal, date_str)
    if df.empty:
        return 0
    df = _volume_surge_col(con, df, date_str)
    df = _sentiment_col(con, df, date_str)
    df = _open5_features(con, df, date_str)
    df = _post_open_label(con, df, date_str)
    df = _intraday_features(con, df, date_str)
    df.insert(0, "date", date_str)
    con.execute("DELETE FROM features WHERE date = ?", [date_str])
    con.execute("INSERT INTO features SELECT * FROM df")
    con.commit()
    return len(df)


def run_data_pipeline(rebuild: bool = False, date_arg: str | None = None):
    print(f"\n{'='*60}")
    print("  MAMDOUH — DATA PIPELINE")
    print(f"{'='*60}")

    con = duckdb.connect(str(DB_PATH))
    if rebuild:
        con.execute("DROP TABLE IF EXISTS features")
        print("Dropped existing features table.")
    _ensure_features_table(con)
    con.commit()

    halal = load_halal()
    print(f"Halal tickers: {len(halal)}")

    if date_arg:
        dates_to_process = [date_arg]
    else:
        all_dates  = _trading_dates(con)
        done_dates = _already_processed(con)
        dates_to_process = [d for d in all_dates if d not in done_dates]
        print(f"Trading dates: {len(all_dates)}  |  Done: {len(done_dates)}  |  To process: {len(dates_to_process)}")

    total_rows = 0
    for i, date_str in enumerate(dates_to_process, 1):
        n = _process_date(con, halal, date_str)
        total_rows += n
        print(f"\r  [{i}/{len(dates_to_process)}] {date_str} → {n} tickers  (total: {total_rows})", end="", flush=True)

    print(f"\nData pipeline done. {total_rows} rows written.")

    CSV_OUT.parent.mkdir(exist_ok=True)
    df_all = con.execute("SELECT * FROM features ORDER BY date, ticker").fetchdf()
    df_all.to_csv(CSV_OUT, index=False)
    print(f"Saved: {CSV_OUT.relative_to(BASE_DIR)}")
    con.close()


# ══════════════════════════════════════════════════════════════════════════════
# MODEL TRAINING
# ══════════════════════════════════════════════════════════════════════════════

def _load_features_from_db(date_str: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        dates = [r[0] for r in con.execute("SELECT DISTINCT date FROM features ORDER BY date").fetchall()]
    except Exception:
        con.close()
        raise RuntimeError("No features table. Run data pipeline first.")
    if not dates:
        con.close()
        raise RuntimeError("features table is empty.")
    target_date = date_str or dates[-1]
    hist_dates  = [d for d in dates if d < target_date]
    print(f"  Target date : {target_date}  |  History: {len(hist_dates)} dates")
    if len(hist_dates) < MIN_TRAIN_DAYS:
        print(f"  WARNING: only {len(hist_dates)} historical dates (need {MIN_TRAIN_DAYS}) — proceeding anyway")
    train_df = pd.DataFrame()
    if hist_dates:
        placeholders = ",".join("?" * len(hist_dates))
        train_df = con.execute(f"""
            SELECT * FROM features
            WHERE date IN ({placeholders}) AND label IS NOT NULL AND post_open_close IS NOT NULL
        """, hist_dates).fetchdf()
    today_df = con.execute(f"SELECT * FROM features WHERE date = '{target_date}'").fetchdf()
    con.close()
    return train_df, today_df, target_date


def _prepare_targets(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # target_gain = post-open 30-min return, consistent with label (not full-day intraday_pct)
    if "post_open_close" in df.columns and "open_price" in df.columns:
        df["target_gain"] = np.where(
            df["open_price"] > 0,
            (df["post_open_close"] - df["open_price"]) / df["open_price"] * 100,
            np.nan,
        )
    else:
        df["target_gain"] = np.nan
    if "intraday_low" in df.columns and "intraday_open" in df.columns:
        df["target_dd"] = np.where(
            df["intraday_open"] > 0,
            (df["intraday_low"] - df["intraday_open"]) / df["intraday_open"] * 100,
            np.nan,
        )
    else:
        df["target_dd"] = np.nan
    dates = sorted(df["date"].unique())
    train_cut = dates[int(len(dates) * 0.8)]
    for col in ["target_gain", "target_dd"]:
        train_vals = df.loc[df["date"] <= train_cut, col].dropna()
        if len(train_vals) > 100:
            lo, hi = train_vals.quantile(0.02), train_vals.quantile(0.98)
            df[col] = df[col].clip(lo, hi)
    return df


def _split(df: pd.DataFrame):
    dates = sorted(df["date"].unique())
    cut   = dates[int(len(dates) * 0.8)]
    return df[df["date"] <= cut], df[df["date"] > cut]


def _ensure_features(df: pd.DataFrame) -> pd.DataFrame:
    for col in FEATURES:
        if col not in df.columns:
            df[col] = 0.0
    return df


def _train_classifier(df: pd.DataFrame) -> RandomForestClassifier:
    clf = RandomForestClassifier(
        n_estimators=500, max_depth=7, min_samples_leaf=8,
        max_features="sqrt", class_weight="balanced", random_state=42, n_jobs=-1,
    )
    train, test = _split(df)
    if len(test) > 50:
        clf.fit(train[FEATURES].fillna(0), train["label"])
        preds = clf.predict(test[FEATURES].fillna(0))
        # Metrics below describe the train-split model on a holdout; the saved model is
        # refit on all data immediately after, so treat these as pre-refit estimates.
        print("\n  [Classifier] Holdout validation (pre-refit):")
        print(classification_report(test["label"], preds, target_names=["DOWN", "UP"], digits=3))
    clf.fit(df[FEATURES].fillna(0), df["label"])
    return clf


def _train_regressor(df: pd.DataFrame, target_col: str, label: str) -> GradientBoostingRegressor | None:
    sub = df[df[target_col].notna()].copy()
    if len(sub) < 200:
        print(f"  [Regressor {label}] Not enough data ({len(sub)} rows), skipping.")
        return None
    reg = GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=15, max_features=0.8, random_state=42,
    )
    train, test = _split(sub)
    if len(test) > 50:
        reg.fit(train[FEATURES].fillna(0), train[target_col])
        preds = reg.predict(test[FEATURES].fillna(0))
        mae  = mean_absolute_error(test[target_col], preds)
        rmse = float(np.sqrt(((test[target_col] - preds) ** 2).mean()))
        r2   = r2_score(test[target_col], preds)
        print(f"  [Regressor {label}] Holdout (pre-refit) — MAE: {mae:.2f}%  RMSE: {rmse:.2f}%  R²: {r2:.3f}")
    reg.fit(sub[FEATURES].fillna(0), sub[target_col])
    return reg


def _load_or_train(train_df: pd.DataFrame, n_hist: int, retrain: bool) -> dict:
    feat_sig = ",".join(sorted(FEATURES))
    if not retrain and MODEL_PATH.exists():
        with open(MODEL_PATH, "rb") as f:
            cached = pickle.load(f)
        if (cached.get("feat_sig") == feat_sig
                and cached.get("n_dates", 0) >= n_hist - MODEL_STALE_TOLERANCE_DAYS):
            print(f"  Cached models loaded (trained on {cached['n_dates']} dates)")
            return cached["models"]
        print("  Features changed or stale — retraining.")

    print(f"  Training on {len(train_df):,} rows from {n_hist} dates …")
    if train_df.empty or len(train_df) < 200:
        raise RuntimeError("Not enough training data.")

    df = _ensure_features(_prepare_targets(train_df))
    models = {
        "clf":      _train_classifier(df),
        "reg_gain": _train_regressor(df, "target_gain", "gain%"),
        "reg_dd":   _train_regressor(df, "target_dd",   "drawdown%"),
    }
    MODEL_PATH.parent.mkdir(exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"models": models, "n_dates": n_hist, "feat_sig": feat_sig}, f)
    print(f"  Models saved → results/models.pkl")
    return models


def run_training(retrain: bool = False, date_arg: str | None = None) -> tuple[dict, pd.DataFrame, str]:
    print(f"\n{'='*60}")
    print("  MAMDOUH — PREDICTING (model training & scoring)")
    print(f"{'='*60}")

    train_df, today_df, target_date = _load_features_from_db(date_arg)

    if today_df.empty:
        raise RuntimeError(f"No features for {target_date}. Run data pipeline first.")

    n_hist = train_df["date"].nunique() if not train_df.empty else 0
    models = _load_or_train(train_df, n_hist, retrain)

    today_df = _apply_scores(today_df, models)
    # Rank by the same composite `score` used live so the offline top-N reflects live ordering
    # (the intraday id_* boost is simply absent here — the DB path has no live id_* columns).
    ranked = today_df.sort_values("score", ascending=False)

    print(f"\n  TOP {TOP_N_WATCH} predictions for {target_date}:")
    print(f"  {'#':<3} {'Ticker':<8} {'UpProb':>7} {'PredGain%':>10} {'PredDD%':>9}")
    print(f"  {'-'*44}")
    for i, (_, r) in enumerate(ranked.head(TOP_N_WATCH).iterrows(), 1):
        pg  = f"{r['pred_gain_pct']:>+9.2f}%" if pd.notna(r.get("pred_gain_pct")) else "       n/a"
        dd  = f"{r['pred_dd_pct']:>+8.2f}%"   if pd.notna(r.get("pred_dd_pct"))   else "      n/a"
        print(f"  {i:<3} {r['ticker']:<8} {r['up_prob']*100:>6.1f}%  {pg}  {dd}")

    return models, ranked, target_date


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — feature builder
# ══════════════════════════════════════════════════════════════════════════════

def _pm_volume_history(tickers: list[str], date_str: str) -> dict[str, float]:
    """Average premarket volume per ticker over the last ~5 trading days, from DuckDB."""
    if not tickers:
        return {}
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
    except Exception:
        return {}
    ticker_list = _ticker_sql_list(tickers)
    if not ticker_list:
        con.close()
        return {}
    date_dt = datetime.strptime(date_str, "%Y-%m-%d")
    avgs: dict[str, list] = {}
    days_found = 0
    for d in range(1, 15):
        past = (date_dt - timedelta(days=d)).strftime("%Y-%m-%d")
        s_ns = utc_ns(past, PREMARKET_START_H)
        e_ns = utc_ns(past, OPEN_HOUR_ET, OPEN_MINUTE_ET)
        try:
            rows = con.execute(f"""
                SELECT ticker, SUM(volume) FROM minute_bars
                WHERE window_start >= {s_ns} AND window_start < {e_ns}
                  AND ticker IN ({ticker_list})
                GROUP BY ticker
            """).fetchall()
        except Exception:
            break
        if rows:
            days_found += 1
            for ticker, vol in rows:
                avgs.setdefault(ticker, []).append(vol or 0)
            if days_found >= 5:
                break
    con.close()
    return {t: sum(v) / len(v) for t, v in avgs.items() if v}


def build_features(client: MassiveClient, tickers: list[str], today: datetime) -> pd.DataFrame:
    mkt_open  = today.replace(hour=9,  minute=30, second=0, microsecond=0)
    pm_start  = today.replace(hour=PREMARKET_START_H, minute=0, second=0, microsecond=0)
    pm_mid    = pm_start + timedelta(minutes=15)
    # News window mirrors training (_news_bounds): prior session 16:00 ET → today 09:30 ET.
    news_from = _prev_weekday(today).replace(hour=16, minute=0, second=0, microsecond=0)
    news_to   = mkt_open
    id_end    = now_et().replace(second=0, microsecond=0)
    total     = len(tickers)
    pm_vol_avg = _pm_volume_history(tickers, today.strftime("%Y-%m-%d"))

    print(f"  Fetching intraday bars + news ({total} tickers) …")
    print(f"  {'#':<4} {'Ticker':<7} {'ID%':>7} {'Price':>8} {'High':>8} {'Low':>8}"
          f" {'Vol':>11} {'Mom':>7} {'VWAP+':>7} {'%Up':>5} {'News':>4} {'Sent':>5} {'Earn':>4} {'FDA':>3}")
    print(f"  {'-'*97}")

    mkt_open_ts = mkt_open.timestamp()
    pm_mid_ts   = pm_mid.timestamp()

    def _fetch(ticker: str) -> dict | None:
        try:
            all_bars = client.bars(ticker, pm_start, id_end)
            if not all_bars:
                return None
            adf = pd.DataFrame(all_bars).sort_values("ts")
            pdf = adf[adf["ts"] < mkt_open_ts]
            idf = adf[adf["ts"] >= mkt_open_ts]

            pm_pct = pm_momentum = 0.0
            pm_volume = 0.0
            if not pdf.empty:
                pm_open  = float(pdf["open"].iloc[0])
                pm_close = float(pdf["close"].iloc[-1])
                pm_volume = float(pdf["volume"].sum())
                if pm_open > 0:
                    pm_pct = (pm_close - pm_open) / pm_open * 100
                    first = pdf[pdf["ts"] < pm_mid_ts]
                    last  = pdf[pdf["ts"] >= pm_mid_ts]
                    f_open = float(first["open"].iloc[0]) if not first.empty else 0.0
                    l_open = float(last["open"].iloc[0])  if not last.empty  else 0.0
                    f_ret = ((float(first["close"].iloc[-1]) - f_open) / f_open * 100
                             if f_open > 0 else 0.0)
                    l_ret = ((float(last["close"].iloc[-1])  - l_open) / l_open * 100
                             if l_open > 0 else 0.0)
                    pm_momentum = l_ret - f_ret
            avg_pm_vol = pm_vol_avg.get(ticker, 0.0)
            pm_vol_surge = pm_volume / avg_pm_vol if avg_pm_vol > 0 else 1.0

            if idf.empty:
                return None
            id_open_p = idf["open"].iloc[0]
            if not id_open_p:
                return None

            # full intraday — for display only, NOT fed into model
            id_close   = idf["close"].iloc[-1]
            id_high    = idf["high"].max()
            id_low     = idf["low"].min()
            id_vol     = idf["volume"].sum()
            id_pct     = (id_close - id_open_p) / id_open_p * 100
            id_range   = (id_high  - id_low)    / id_open_p * 100
            id_hi_dist = (id_high  - id_close)  / id_open_p * 100
            vwap       = (idf["close"] * idf["volume"]).sum() / id_vol if id_vol > 0 else id_close
            id_vwap_diff = (id_close - vwap) / vwap * 100 if vwap else 0.0
            id_up_pct    = (idf["close"] > idf["open"]).sum() / len(idf) * 100
            last5 = idf.tail(5)
            l5 = ((last5["close"].iloc[-1] - last5["open"].iloc[0]) / last5["open"].iloc[0] * 100
                  if len(last5) > 0 and last5["open"].iloc[0] else 0.0)

            # first 5 minutes only — safe model features (no future leakage)
            o5       = idf.head(5)
            o5_open  = o5["open"].iloc[0]
            o5_close = o5["close"].iloc[-1]
            o5_high  = o5["high"].max()
            o5_low   = o5["low"].min()
            o5_vol   = o5["volume"].sum()
            o5_vwap_abs     = (o5["close"] * o5["volume"]).sum() / o5_vol if o5_vol > 0 else o5_close
            open5_pct       = (o5_close - o5_open)    / o5_open * 100 if o5_open else 0.0
            open5_range_pct = (o5_high  - o5_low)     / o5_open * 100 if o5_open else 0.0
            # normalized: vwap distance from open (%), not absolute price
            o5_vwap         = (o5_vwap_abs - o5_open) / o5_open * 100 if o5_open else 0.0

            news_items   = client.news(ticker, news_from, news_to)
            sentiments   = [SENTIMENT_MAP.get((n.get("sentiment") or "").lower().strip(), 0.0) for n in news_items]
            avg_sent     = float(np.mean(sentiments)) if sentiments else 0.0
            has_earnings = int(any(n.get("is_earnings") for n in news_items))
            has_fda      = int(any(n.get("is_fda")      for n in news_items))
            headline     = (news_items[0].get("title") or "")[:70] if news_items else ""
            return {
                "ticker": ticker, "headline": headline,
                # model features — pre-open + first 5 min only
                "open5_pct": open5_pct, "open5_range_pct": open5_range_pct,
                "open5_vwap": o5_vwap,  "open5_volume": float(o5_vol),
                "news_sentiment": avg_sent, "news_count": len(sentiments),
                "has_earnings": has_earnings, "has_fda": has_fda,
                "pm_pct": pm_pct, "pm_momentum": pm_momentum, "pm_vol_surge": pm_vol_surge,
                # display-only intraday fields
                "id_pct": id_pct, "id_open": id_open_p, "id_close": id_close,
                "id_high": id_high, "id_low": id_low, "id_volume": id_vol,
                "id_momentum": l5, "id_range_pct": id_range,
                "id_vwap_diff": id_vwap_diff, "id_up_bars_pct": id_up_pct, "id_high_dist": id_hi_dist,
            }
        except Exception as e:
            return {"ticker": ticker, "_error": str(e)}

    rows       = []
    idx_map    = {t: i + 1 for i, t in enumerate(tickers)}
    print_lock = threading.Lock()

    def _p(v, w=7):
        if v is None:
            return f"{'n/a':>{w+1}}"
        fv = float(v)
        return f"{fv:>+{w}.2f}%" if not np.isnan(fv) else f"{'n/a':>{w+1}}"

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            idx    = idx_map[ticker]
            result = fut.result()
            with print_lock:
                if result is None:
                    print(f"  {idx:<4} {ticker:<7}  — no bars")
                    continue
                if "_error" in result:
                    print(f"  {idx:<4} {ticker:<7}  ERROR — {result['_error']}")
                    continue
                r  = result
                ia = "▲" if r["id_pct"] >= 0 else "▼"
                sl = "+" if r["news_sentiment"] > 0.1 else ("-" if r["news_sentiment"] < -0.1 else "~")
                earn = "E" if r.get("has_earnings") else " "
                fda  = "F" if r.get("has_fda")      else " "
                print(f"  {idx:<4} {ticker:<7}"
                      f" {ia}{abs(r['id_pct']):>5.2f}%"
                      f" {r['id_close']:>8.3f} {r['id_high']:>8.3f} {r['id_low']:>8.3f}"
                      f" {int(r['id_volume']):>10,}"
                      f" {_p(r['id_momentum'])} {_p(r['id_vwap_diff'])}"
                      f" {r['id_up_bars_pct']:>4.0f}%"
                      f" {r['news_count']:>3}n {sl}{abs(r['news_sentiment']):.2f}"
                      f" {earn:>4} {fda:>3}")
                if r["headline"]:
                    print(f"       └ {r['headline']}")
                rows.append(r)

    order = {t: i for i, t in enumerate(tickers)}
    rows.sort(key=lambda r: order.get(r["ticker"], 9999))
    print(f"  {'-'*97}")
    print(f"  {len(rows)} of {total} tickers returned data.\n")
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# IBKR ORDER PLACEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _round_tick(price: float) -> float:
    """Round to the instrument's minimum price increment: 2 dp at/above $1, 4 dp below."""
    return round(price, 2) if price >= 1.0 else round(price, 4)


class IBKRClient:
    _instance = None

    def __init__(self):
        self.ib = None
        self._cash_val = 0.0
        self._cash_ts  = 0.0
        if not IBKR_ENABLED:
            return
        try:
            from ib_insync import IB
            self.ib = IB()
            self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            print(f"  IBKR connected → {IBKR_HOST}:{IBKR_PORT}")
        except Exception as e:
            print(f"  IBKR connect failed: {e}")
            self.ib = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = IBKRClient()
        return cls._instance

    def place_entry(self, ticker: str, qty: int, entry: float, fill_timeout: float = 30.0) -> int:
        """LimitOrder BUY at Massive's last price. Returns filled qty (0 if not filled)."""
        if not IBKR_ENABLED or self.ib is None:
            return 0
        try:
            from ib_insync import Stock, LimitOrder
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            limit_px = _round_tick(entry)
            trade = self.ib.placeOrder(contract, LimitOrder("BUY", qty, limit_px))
            print(f"  IBKR BUY sent: {ticker} qty={qty} @ {limit_px:.4f}")
            deadline = time.time() + fill_timeout
            while time.time() < deadline:
                self.ib.waitOnUpdate(timeout=1.0)
                if trade.isDone():
                    break
            filled = int(trade.filled())
            if filled <= 0:
                try:
                    self.ib.cancelOrder(trade.order)
                except Exception:
                    pass
                print(f"  IBKR BUY {ticker}: not filled, cancelled.")
                return 0
            print(f"  IBKR BUY {ticker}: filled {filled}/{qty}")
            return filled
        except Exception as e:
            print(f"  IBKR BUY failed for {ticker}: {e}")
            return 0

    def cash(self, ttl: float = 30.0) -> float:
        if not IBKR_ENABLED or self.ib is None:
            return 0.0
        if time.time() - self._cash_ts < ttl:
            return self._cash_val
        try:
            for v in self.ib.accountSummary():
                if v.tag == "TotalCashValue" and v.currency == "USD":
                    self._cash_val = float(v.value)
                    self._cash_ts  = time.time()
                    return self._cash_val
        except Exception as e:
            print(f"  IBKR cash query failed: {e}")
        return self._cash_val

    def close_position(self, ticker: str, qty: int) -> bool:
        if not IBKR_ENABLED or self.ib is None:
            return False
        try:
            from ib_insync import Stock, MarketOrder
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            self.ib.placeOrder(contract, MarketOrder("SELL", qty))
            print(f"  IBKR close sent: {ticker} qty={qty}")
            return True
        except Exception as e:
            print(f"  IBKR close failed for {ticker}: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — TP/SL
# ══════════════════════════════════════════════════════════════════════════════

def calc_tp_sl(
    entry_price: float, pred_gain: float, pred_dd: float,
    pm_pct: float, up_prob: float,
    id_range_pct: float = 0.0, id_momentum: float = 0.0,
    id_vwap_diff: float = 0.0, id_high_dist: float = 0.0,
) -> tuple[float, float]:
    raw_tp = pred_gain if not np.isnan(pred_gain) else pm_pct * 1.5
    raw_tp = raw_tp * (0.7 + 0.3 * up_prob)
    if id_momentum > 0.5 and id_vwap_diff > 0:
        raw_tp *= min(1.25, 1.0 + id_momentum * 0.02)
    if id_high_dist > 2.0:
        raw_tp *= max(0.75, 1.0 - id_high_dist * 0.04)
    tp_pct = float(raw_tp)

    if not np.isnan(pred_dd) and pred_dd < 0:
        raw_sl = pred_dd * 1.1
    else:
        raw_sl = -abs(pm_pct) * 0.5 if pm_pct != 0 else -1.0
    if 0 < id_range_pct < 1.5:
        raw_sl = max(raw_sl, -1.0)
    sl_pct = float(raw_sl)

    min_rr = 1.5 if up_prob < 0.6 else 1.0
    tp_pct = float(max(tp_pct, abs(sl_pct) * min_rr))

    return round(entry_price * (1 + tp_pct / 100), 4), round(entry_price * (1 + sl_pct / 100), 4)


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — entry / exit helpers
# ══════════════════════════════════════════════════════════════════════════════

def confirm_entry(client: MassiveClient, ticker: str, today: datetime,
                  n_bars: int = CONFIRM_BARS) -> tuple[bool, float, str]:
    open_start = today.replace(hour=9, minute=30, second=0, microsecond=0)
    open_end   = open_start + timedelta(minutes=n_bars + 1)
    bars = []
    for _ in range(12):
        bars = client.bars(ticker, open_start, open_end)
        if len(bars) >= n_bars:
            break
        time.sleep(5)
    if not bars:
        print(f"    {ticker}: no opening bars — skip")
        return False, 0.0, ""
    df         = pd.DataFrame(bars).sort_values("ts")
    bar_open   = float(df["open"].iloc[0])
    last_close = float(df["close"].iloc[-1])
    confirmed  = last_close > bar_open
    chg    = (last_close - bar_open) / bar_open * 100
    arrow  = "▲" if confirmed else "▼"
    status = "CONFIRMED" if confirmed else "REJECTED"
    print(f"    {ticker}: bar_open={bar_open:.4f}  bar_close={last_close:.4f}  {arrow}{abs(chg):.2f}%  [{status}]")
    if not confirmed:
        return False, 0.0, ""
    live_price, entry_time = client.price(ticker)
    entry_price = live_price if live_price > 0 else last_close
    print(f"    {ticker}: live entry price = {entry_price:.4f}  @ {entry_time} ET")
    return True, entry_price, entry_time


def _make_position(row: dict, entry_price: float, entry_time: str, today: datetime) -> dict:
    pred_gain = float(row["pred_gain_pct"]) if pd.notna(row.get("pred_gain_pct")) else np.nan
    pred_dd   = float(row["pred_dd_pct"])   if pd.notna(row.get("pred_dd_pct"))   else np.nan
    up_prob   = float(row["up_prob"])
    tp_price, sl_price = calc_tp_sl(
        entry_price, pred_gain, pred_dd, row.get("pm_pct", 0.0), up_prob,
        id_range_pct=float(row.get("id_range_pct", 0.0) or 0.0),
        id_momentum =float(row.get("id_momentum",  0.0) or 0.0),
        id_vwap_diff=float(row.get("id_vwap_diff", 0.0) or 0.0),
        id_high_dist=float(row.get("id_high_dist", 0.0) or 0.0),
    )
    return {
        "ticker":     row["ticker"],
        "date":       today.strftime("%Y-%m-%d"),
        "entry":      entry_price,
        "entry_time": entry_time,
        "tp":         tp_price,
        "sl":         sl_price,
        "tp_pct":     (tp_price - entry_price) / entry_price * 100,
        "sl_pct":     (sl_price - entry_price) / entry_price * 100,
        "pred_gain":  pred_gain,
        "pred_dd":    pred_dd,
        "up_prob":    up_prob,
    }


def _log_exit(p: dict, price: float, reason: str) -> dict:
    pnl       = (price - p["entry"]) / p["entry"] * 100
    exit_time = now_et().strftime("%H:%M:%S")
    ibkr_qty  = int(p.get("ibkr_qty", 0) or 0)
    if IBKR_ENABLED and ibkr_qty > 0:
        IBKRClient.get().close_position(p["ticker"], ibkr_qty)
    emoji     = "🎯" if reason == "TP" else ("🛑" if reason == "SL" else "🔔")
    sep       = "=" * 50
    print(f"\n{sep}")
    print(f"  {emoji} [{reason}] {p['ticker']}")
    print(f"  Buy  : {p['entry']:.4f}  @ {p.get('entry_time', '?')} ET")
    print(f"  Exit : {price:.4f}  @ {exit_time} ET")
    print(f"  PnL  : {pnl:+.2f}%")
    print(f"  TP   : {p['tp']:.4f}  |  SL : {p['sl']:.4f}")
    print(sep)
    _tg_send(
        f"{emoji} <b>[{reason}] {p['ticker']}</b>\n"
        f"  Buy  : {p['entry']:.4f} @ {p.get('entry_time', '?')} ET\n"
        f"  Exit : {price:.4f} @ {exit_time} ET\n"
        f"  PnL  : {pnl:+.2f}%\n"
        f"  TP={p['tp']:.4f}  SL={p['sl']:.4f}"
    )
    return {**p, "exit": price, "exit_time": exit_time, "pnl_pct": round(pnl, 3), "reason": reason}


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — state persistence
# ══════════════════════════════════════════════════════════════════════════════

def save_state(open_pos: dict, today: str):
    STATE_PATH.parent.mkdir(exist_ok=True)
    # Sanitize NaN → None so the state file is spec-compliant JSON (NaN is a Python-only token).
    STATE_PATH.write_text(json.dumps(_json_safe({"date": today, "positions": open_pos}), indent=2))


def load_state(today: str) -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text())
        if data.get("date") == today:
            pos = data.get("positions", {})
            if pos:
                print(f"  Resuming {len(pos)} saved position(s) from state file.")
            return pos
    except Exception:
        pass
    return {}


def clear_state():
    if STATE_PATH.exists():
        STATE_PATH.unlink()


def save_log(results: list[dict]):
    if not results:
        return
    df_new = pd.DataFrame(results)
    df_all = pd.concat([pd.read_csv(LOG_PATH), df_new], ignore_index=True) if LOG_PATH.exists() else df_new
    LOG_PATH.parent.mkdir(exist_ok=True)
    df_all.to_csv(LOG_PATH, index=False)
    print(f"\nLog saved → results/live_log.csv  ({len(df_all)} total trades)")


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — scoring
# ══════════════════════════════════════════════════════════════════════════════

def _apply_scores(df: pd.DataFrame, models: dict) -> pd.DataFrame:
    X = df[FEATURES].fillna(0)
    df = df.copy()
    df["up_prob"]       = models["clf"].predict_proba(X)[:, 1]
    df["pred_gain_pct"] = models["reg_gain"].predict(X) if models.get("reg_gain") else float("nan")
    df["pred_dd_pct"]   = models["reg_dd"].predict(X)   if models.get("reg_dd")   else float("nan")
    df["score"] = df["pred_gain_pct"].fillna(0) * df["up_prob"]
    if "id_pct" in df.columns:
        df["score"] += (
            df["id_pct"].fillna(0)            * 0.5
            + df["id_momentum"].fillna(0)     * 0.3
            + df["id_vwap_diff"].fillna(0)    * 0.2
            + df["id_up_bars_pct"].fillna(50) * 0.02
            - df["id_high_dist"].fillna(0)    * 0.2
        )
    return df


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — live watchlist re-rank
# ══════════════════════════════════════════════════════════════════════════════

def _live_watchlist(client: MassiveClient, halal: set, traded: set,
                    models: dict, today: datetime) -> list[dict]:
    try:
        snaps = client.snapshot_all()
    except Exception as e:
        print(f"  Live snapshot failed: {e}")
        return []
    rows = []
    for s in snaps:
        if not isinstance(s, TickerSnapshot):
            continue
        if s.ticker not in halal or s.ticker in traded:
            continue
        day = s.day if isinstance(s.day, Agg) else s.prev_day
        if not isinstance(day, Agg):
            continue
        o, c, vol = day.open, day.close, day.volume or 0
        if not (isinstance(o, float) and isinstance(c, float) and o > 0):
            continue
        if c < MIN_PRICE:
            continue
        pct   = (c - o) / o * 100
        score = pct * np.log1p(vol)
        rows.append({"ticker": s.ticker, "pct": pct, "price": c, "volume": int(vol), "score": score})
    if not rows:
        return []
    rows.sort(key=lambda x: x["score"], reverse=True)
    candidates = [r["ticker"] for r in rows[:TOP_N_WATCH * 2]]
    df = build_features(client, candidates, today)
    if df.empty:
        return []
    df = _apply_scores(df, models)
    return df.sort_values("score", ascending=False).head(TOP_N_WATCH).to_dict("records")


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — daily report
# ══════════════════════════════════════════════════════════════════════════════

def _send_daily_report(results: list[dict], date_str: str, mode: str):
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  DAILY REPORT  —  {date_str}  [{mode}]")
    print(f"{sep}")
    if not results:
        print("  No trades taken today.")
        _tg_send(f"📋 <b>Daily Report</b> — {date_str} [{mode}]\n  No trades taken.")
        return
    tp_count  = sum(1 for r in results if r["reason"] == "TP")
    sl_count  = sum(1 for r in results if r["reason"] == "SL")
    eod_count = sum(1 for r in results if r["reason"] == "EOD")
    total_pnl = sum(r["pnl_pct"] for r in results)
    win_rate  = tp_count / len(results) * 100
    print(f"  {'Ticker':<8} {'Reason':<6} {'BuyTime':>9} {'Buy':>8} {'ExitTime':>9} {'Exit':>8} {'PnL%':>7}")
    print(f"  {'-'*62}")
    lines = [f"📋 <b>Daily Report</b> — {date_str} [{mode}]\n"]
    for r in results:
        emoji = "🎯" if r["reason"] == "TP" else ("🛑" if r["reason"] == "SL" else "🔔")
        bt    = r.get("entry_time", "?")
        xt    = r.get("exit_time",  "?")
        print(f"  {r['ticker']:<8} {r['reason']:<6} {bt:>9} {r['entry']:>8.4f}"
              f" {xt:>9} {r['exit']:>8.4f} {r['pnl_pct']:>+6.2f}%")
        lines.append(f"{emoji} {r['ticker']}  [{r['reason']}]  buy={bt}  exit={xt}  pnl={r['pnl_pct']:+.2f}%")
    print(f"  {'-'*62}")
    print(f"  Trades : {len(results)}  (TP={tp_count}  SL={sl_count}  EOD={eod_count})")
    print(f"  Win %  : {win_rate:.0f}%")
    print(f"  Total  : {total_pnl:+.2f}%")
    print(f"{sep}\n")
    lines += [
        f"\nTrades: {len(results)}  (TP={tp_count}  SL={sl_count}  EOD={eod_count})",
        f"Win rate: {win_rate:.0f}%",
        f"<b>Total PnL: {total_pnl:+.2f}%</b>",
    ]
    _tg_send("\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# LIVE ENGINE — all-day WebSocket monitor
# ══════════════════════════════════════════════════════════════════════════════

def run_all_day(client: MassiveClient, watchlist: list[dict],
                today: datetime,
                halal: set | None = None, models: dict | None = None) -> list[dict]:
    CLOSE_TIME = today.replace(hour=15, minute=55, second=0, microsecond=0)
    today_str  = today.strftime("%Y-%m-%d")

    saved    = load_state(today_str)
    open_pos : dict[str, dict] = dict(saved)
    traded   : set[str]        = set(saved.keys())
    results  : list[dict]      = []
    pos_lock : threading.Lock  = threading.Lock()

    def _try_enter(row: dict) -> bool:
        ticker = row["ticker"]
        with pos_lock:
            if ticker in traded or ticker in open_pos:
                return False
            traded.add(ticker)
        print(f"\n  Confirming {ticker} …")
        confirmed, entry_price, entry_time = confirm_entry(client, ticker, today)
        if not confirmed or entry_price == 0:
            print(f"  {ticker}: no confirmation — skip")
            return False

        pos = _make_position(row, entry_price, entry_time, today)
        ibkr_cash = 0.0
        ibkr_qty  = 0
        ibkr_status = "disabled"
        if IBKR_ENABLED:
            ibkr = IBKRClient.get()
            ibkr_cash = ibkr.cash()
            alloc     = ibkr_cash / IBKR_SPLIT
            req_qty   = int(alloc // entry_price) if entry_price > 0 else 0
            if req_qty <= 0:
                ibkr_status = f"skipped (alloc=${alloc:.2f} < price)"
                print(f"  IBKR skip {ticker}: alloc ${alloc:.2f} < price ${entry_price:.2f}")
                return False
            ibkr_qty = ibkr.place_entry(ticker, req_qty, entry_price)
            if ibkr_qty <= 0:
                ibkr_status = "not filled"
                return False
            pos["ibkr_qty"] = ibkr_qty
            ibkr_status = f"filled qty={ibkr_qty} cash=${ibkr_cash:.2f}"

        with pos_lock:
            open_pos[ticker] = pos
            save_state(open_pos, today_str)

        g  = f"{pos['pred_gain']:+.1f}%" if not np.isnan(pos["pred_gain"]) else "n/a"
        dd = f"{pos['pred_dd']:+.1f}%"   if not np.isnan(pos["pred_dd"])   else "n/a"
        tg(
            f"✅ <b>[ENTRY] {ticker}</b>\n"
            f"  Buy   : {entry_price:.4f} @ {entry_time} ET\n"
            f"  TP    : {pos['tp']:.4f} ({pos['tp_pct']:+.1f}%)\n"
            f"  SL    : {pos['sl']:.4f} ({pos['sl_pct']:+.1f}%)\n"
            f"  Model : gain={g}  dd={dd}  up={pos['up_prob']*100:.0f}%\n"
            f"  IBKR  : {ibkr_status}"
        )
        return True

    print("\n  Entering initial positions …")
    for row in watchlist:
        if len(open_pos) >= MAX_POSITIONS:
            break
        _try_enter(row)

    all_watch = (halal or set()) | {r["ticker"] for r in watchlist} | set(open_pos.keys())
    print(f"  Monitoring {len(open_pos)} position(s) via WebSocket "
          f"(subscribed to {len(all_watch)} tickers — full halal universe for reentry coverage)\n")

    trade_q:        queue.Queue     = queue.Queue()
    reentry_active: threading.Event = threading.Event()

    def _ws_handler(msgs):
        for m in msgs:
            sym = getattr(m, "symbol", None)
            px  = getattr(m, "price",  None)
            if sym and px is not None and sym in open_pos:
                trade_q.put((sym, float(px)))

    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    ws = WebSocketClient(api_key=api_key, subscriptions=[f"T.{t}" for t in all_watch])
    threading.Thread(target=ws.run, kwargs={"handle_msg": _ws_handler}, daemon=True).start()
    print(f"  WS subscribed to {len(all_watch)} tickers")

    def _do_reentry():
        slots = MAX_POSITIONS - len(open_pos)
        if slots <= 0:
            reentry_active.clear()
            return
        print(f"\n  Slot freed — fetching live snapshot …")
        fresh = _live_watchlist(client, halal, traded, models, today) if halal and models else []
        if not fresh:
            fresh = [r for r in watchlist if r["ticker"] not in traded]
        print(f"  {len(fresh)} fresh candidates — trying top {slots} …")
        filled = 0
        for row in fresh:
            if filled >= slots:
                break
            if _try_enter(row):
                filled += 1
        if filled == 0:
            print("  No new entries confirmed.")
        reentry_active.clear()

    last_status = time.time()

    while True:
        try:
            sym, price = trade_q.get(timeout=1.0)
        except queue.Empty:
            if now_et() >= CLOSE_TIME:
                break
            if time.time() - last_status >= 60 and open_pos:
                et_now = now_et()
                print(f"\n  ── {et_now.strftime('%H:%M:%S')} ET (heartbeat) ─────────────────────")
                print(f"  {'Ticker':<8} {'BuyTime':>9} {'Entry':>8} {'TP':>8} {'SL':>8}  Holding")
                for tk, p in open_pos.items():
                    bt = p.get("entry_time", "?")
                    print(f"  {tk:<8} {bt:>9} {p['entry']:>8.4f} {p['tp']:>8.4f} {p['sl']:>8.4f}  …")
                last_status = time.time()
            continue

        with pos_lock:
            if sym not in open_pos:
                continue
            p   = open_pos[sym]
            pnl = (price - p["entry"]) / p["entry"] * 100
            if price >= p["tp"]:
                reason, exit_price = "TP", p["tp"]
            elif price <= p["sl"]:
                reason, exit_price = "SL", p["sl"]
            else:
                to_tp = (p["tp"] - price) / price * 100
                to_sl = (price - p["sl"]) / price * 100
                print(f"  {sym:<8} px={price:.4f}  pnl={pnl:>+.2f}%  →TP={to_tp:>+.2f}%  →SL={to_sl:>+.2f}%")
                continue
            result = _log_exit(p, exit_price, reason)
            results.append(result)
            del open_pos[sym]
            save_state(open_pos, today_str)

        if not reentry_active.is_set():
            reentry_active.set()
            threading.Thread(target=_do_reentry, daemon=True).start()

        last_status = time.time()
        if now_et() >= CLOSE_TIME:
            break

    if open_pos:
        print(f"\n  15:55 ET — force-closing {len(open_pos)} remaining position(s).")
        for ticker, p in list(open_pos.items()):
            try:
                price, _ = client.price(ticker)
                price = price or p["entry"]
            except Exception:
                price = p["entry"]
            results.append(_log_exit(p, price, "EOD"))

    clear_state()
    _send_daily_report(results, today_str, "LIVE")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# LIVE — main runner
# ══════════════════════════════════════════════════════════════════════════════

def run_live(today_et: datetime, models: dict, ranked: pd.DataFrame):
    print(f"\n{'='*60}")
    print(f"  MAMDOUH — DECISION (live execution)")
    print(f"{'='*60}\n")

    client    = MassiveClient()
    halal     = load_halal()
    today_str = today_et.strftime("%Y-%m-%d")

    saved = load_state(today_str)
    if saved:
        print(f"  Resuming {len(saved)} open position(s) from saved state — skipping scan.")
        for t, p in saved.items():
            print(f"    {t:<8}  entry={p['entry']:.4f}  TP={p['tp']:.4f}  SL={p['sl']:.4f}")
        results = run_all_day(client, [], today_et, halal=halal, models=models)
        save_log(results)
        return

    # Gate scoring until the 09:30–09:35 ET open5 window is complete, otherwise `idf.head(5)`
    # is degenerate and the model is scored on out-of-distribution inputs (it trains on the
    # full fixed 5-minute window).
    open5_done = today_et.replace(hour=OPEN_HOUR_ET, minute=OPEN_MINUTE_ET + 5,
                                  second=0, microsecond=0)
    if now_et() < open5_done:
        print(f"  Waiting until {open5_done.strftime('%H:%M')} ET for the open5 window to complete …")
        _wait_until(open5_done)

    print("Fetching market snapshot …")
    try:
        snaps = client.snapshot_all()
        rows  = []
        for s in snaps:
            if not isinstance(s, TickerSnapshot):
                continue
            if s.ticker not in halal:
                continue
            day = s.day if isinstance(s.day, Agg) else s.prev_day
            if not isinstance(day, Agg):
                continue
            o, c, vol = day.open, day.close, day.volume or 0
            if not (isinstance(o, float) and isinstance(c, float) and o > 0):
                continue
            if c < MIN_PRICE:
                continue
            pct   = (c - o) / o * 100
            score = pct * np.log1p(vol)
            rows.append({"ticker": s.ticker, "pct": round(pct, 2), "price": round(c, 4),
                         "volume": int(vol), "score": score})
        rows.sort(key=lambda x: x["score"], reverse=True)
        candidates = [r["ticker"] for r in rows[:TOP_N_WATCH * 2]]
        print(f"  {len(rows)} halal tickers → top {len(candidates)} candidates")
    except Exception as e:
        print(f"  Snapshot failed ({e}), falling back to halal list.")
        candidates = sorted(halal)[:40]

    if not candidates:
        print("No candidates found. Exiting.")
        return

    print("\nBuilding live features …")
    df = build_features(client, candidates, today_et)
    if df.empty:
        print("No feature data. Exiting.")
        return

    df = _apply_scores(df, models)
    ranked_live = df.sort_values("score", ascending=False).head(TOP_N_WATCH)

    results = run_all_day(client, ranked_live.to_dict("records"), today_et,
                          halal=halal, models=models)
    save_log(results)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def run_train(rebuild: bool, retrain: bool, date_arg: str | None, today_et: datetime):
    """MAMDOUH train: data pipeline + model training. No live execution."""
    session_log = start_session_log(today_et, "TRAIN")
    print(f"\n{'='*60}")
    print(f"  MAMDOUH TRAIN  —  {today_et.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    try:
        run_data_pipeline(rebuild=rebuild, date_arg=date_arg)
        run_training(retrain=retrain, date_arg=date_arg)
    finally:
        session_log.close()


def run_live_only(date_arg: str | None, today_et: datetime):
    """MAMDOUH live: load cached models, run live execution. Requires prior train run."""
    session_log = start_session_log(today_et, "LIVE")
    print(f"\n{'='*60}")
    print(f"  MAMDOUH LIVE  —  {today_et.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    try:
        if not MODEL_PATH.exists():
            raise RuntimeError(f"No model found at {MODEL_PATH}. Run train first.")
        models, ranked, _ = run_training(retrain=False, date_arg=date_arg)
        run_live(today_et, models, ranked)
    finally:
        session_log.close()


def main():
    rebuild  = "--rebuild" in sys.argv
    retrain  = "--retrain" in sys.argv
    do_train = "--train"   in sys.argv
    do_live  = "--live"    in sys.argv
    date_arg = next((a for a in sys.argv[1:] if not a.startswith("--")), None)

    today_et = now_et().replace(hour=0, minute=0, second=0, microsecond=0)

    if do_train and not do_live:
        run_train(rebuild, retrain, date_arg, today_et)
        return
    if do_live and not do_train:
        run_live_only(date_arg, today_et)
        return

    session_log = start_session_log(today_et, "LIVE")
    print(f"\n{'='*60}")
    print(f"  MAMDOUH  —  {today_et.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    try:
        run_data_pipeline(rebuild=rebuild, date_arg=date_arg)
        models, ranked, _ = run_training(retrain=retrain, date_arg=date_arg)
        run_live(today_et, models, ranked)
    finally:
        session_log.close()


if __name__ == "__main__":
    main()
