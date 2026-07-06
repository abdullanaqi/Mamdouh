import json
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
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    brier_score_loss,
    classification_report,
    mean_absolute_error,
    r2_score,
)
from sklearn.model_selection import TimeSeriesSplit

try:
    import lightgbm as lgb
    _HAS_LGB = True
except ImportError:
    lgb = None
    _HAS_LGB = False

try:
    from catboost import CatBoostClassifier, CatBoostRegressor
    _HAS_CAT = True
except ImportError:
    CatBoostClassifier = CatBoostRegressor = None
    _HAS_CAT = False

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
load_dotenv(Path(__file__).parent / ".env")


BASE_DIR        = Path(__file__).parent
DB_PATH         = BASE_DIR / "data" / "market_data.duckdb"
MODEL_PATH      = BASE_DIR / "result v2" / "models.pkl"
HALAL_JSON      = BASE_DIR / "results" / "halal_stocks.json"
LOG_PATH        = BASE_DIR / "result v2" / "live_log.csv"
SESSION_LOG_DIR = BASE_DIR / "result v2" / "sessions"
STATE_PATH      = BASE_DIR / "result v2" / "live_state.json"
CSV_OUT         = BASE_DIR / "result v2" / "features_dataset.csv"

CAPITAL_PER_TRADE   = 10_000.0
SLIPPAGE_BPS        = 5.0
COMMISSION_PER_SIDE = 1.0

TOP_N_WATCH       = 20
MAX_POSITIONS     = 3
MIN_GAINER_PCT    = 2.0
MIN_OPEN5_PCT     = 0.0
GAINER_BIAS_POW   = 0.5
CONFIRM_BARS      = 2
MIN_PRICE         = 5.0
MIN_TRAIN_DAYS    = 30
BT_RETRAIN_EVERY  = 10
BT_SIZE_LO, BT_SIZE_HI = 0.3, 2.5
BT_SLIP_BPS_PER_SURGE  = 0.5
BT_SLIP_CAP_BPS        = 40.0
PREMARKET_START_H = 4
OPEN_HOUR_ET      = 9
OPEN_MINUTE_ET    = 30
LABEL_ANCHOR_MIN  = 5
POST_OPEN_MINS    = 30
HOLD_MINUTES      = 400

LABEL_TP_BARRIER_PCT = 1.0
LABEL_SL_BARRIER_PCT = 1.0
LABEL_RECENCY_HALFLIFE_DAYS = 60
CLOSE_HOUR_ET     = 15
CLOSE_MINUTE_ET   = 55
STOP_ENTRY_HOUR   = 11
STOP_ENTRY_MINUTE = 0

NEWS_CUTOFF_MIN   = 5
MIN_UP_PROB       = 0.507
MIN_PRED_GAIN     = 0.0
GATE_PERCENTILE   = 95.0
WS_RECONNECT_DELAY_S = 5
REST_BACKSTOP_S      = 30
REENTRY_TIME_BUDGET_S = 60
PICK_REFRESH_DELAY_S  = 30
API_MAX_RETRIES   = 3
API_BACKOFF_BASE  = 1.5

AGENT_ENABLED      = True
ANTHROPIC_API_URL  = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL    = os.getenv("AGENT_MODEL", "claude-haiku-4-5-20251001")
AGENT_TIMEOUT_S    = 25
AGENT_MAX_HEADLINE = 280
AGENT_REQUIRE_ALL  = True

TELEGRAM_ENABLED  = False

IBKR_ENABLED      = False
IBKR_HOST         = "127.0.0.1"
IBKR_PORT         = 7497
IBKR_CLIENT_ID    = 1
IBKR_SPLIT        = 6

MARKET_TICKER = "SPY"

FEATURES = [
    "pm_pct", "pm_momentum", "pm_range_pct", "pm_vol_surge", "gap_vol_quality",
    "news_sentiment", "news_count",
    "has_earnings", "has_fda",
    "open5_range_pct", "open5_vwap", "open5_volume", "open5_pct",
    "mkt_pm_pct", "mkt_open5_pct", "mkt_rel_pm_pct",
    "price_bucket",
]

LEAK_FORBIDDEN_PREFIXES = ("intraday_", "post_open_", "eod_")
LEAK_FORBIDDEN_EXACT    = {"open_price", "label", "eod_label", "intraday_pct",
                           "post_open_close", "target_gain", "target_dd", "target_mfe"}

def assert_no_leakage(feats=FEATURES):
    bad = [f for f in feats
           if f in LEAK_FORBIDDEN_EXACT or f.startswith(LEAK_FORBIDDEN_PREFIXES)]
    if bad:
        raise SystemExit(f"[FIREWALL] LEAKAGE BLOCKED — forbidden inputs in FEATURES: {bad}")
    return feats


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
    fname = SESSION_LOG_DIR / f"session_{today.strftime('%Y-%m-%d')}_{mode}_{now_et().strftime('%H%M%S')}.log"
    logger = TeeLogger(fname)
    print(f"Session log → {fname}")
    return logger



def _tg_send(text: str):
    if not TELEGRAM_ENABLED:
        return
    token    = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chat_ids:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for cid in chat_ids:
        try:
            r = requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "HTML"}, timeout=5)
            if r.status_code != 200:
                print(f"  [tg] send to {cid} failed: HTTP {r.status_code} {r.text[:120]}")
        except Exception as e:
            print(f"  [tg] send to {cid} raised: {e}")


def tg(text: str):
    print(text)
    _tg_send(text)



def _row_context(ticker: str, row: dict, headline: str) -> dict:
    """Pull and format the per-ticker context once; reused across all agents."""
    return {
        "ticker":      ticker,
        "pm_pct":      float(row.get("pm_pct", 0.0) or 0.0),
        "open5_pct":   float(row.get("open5_pct", 0.0) or 0.0),
        "vol_surge":   float(row.get("pm_vol_surge", 1.0) or 1.0),
        "news_count":  int(row.get("news_count", 0) or 0),
        "sentiment":   float(row.get("news_sentiment", 0.0) or 0.0),
        "has_earn":    bool(row.get("has_earnings")),
        "has_fda":     bool(row.get("has_fda")),
        "mkt_pm":      float(row.get("mkt_pm_pct", 0.0) or 0.0),
        "mkt_open5":   float(row.get("mkt_open5_pct", 0.0) or 0.0),
        "pred_gain":   float(row.get("pred_gain_pct", 0.0) or 0.0),
        "pred_dd":     float(row.get("pred_dd_pct", 0.0) or 0.0),
        "pred_mfe":    float(row.get("pred_mfe_pct", 0.0) or 0.0),
        "up_prob":     float(row.get("up_prob", 0.0) or 0.0),
        "headline":    (headline or "")[:AGENT_MAX_HEADLINE],
    }


def _news_agent_prompt(c: dict) -> str:
    return (
        "You are the NEWS AGENT for an intraday long-only trading bot.\n"
        "Your ONLY job: read the news context and veto if there are qualitative red flags.\n"
        "Veto reasons: dilution/secondary offering, SEC investigation, fraud allegation, "
        "going-concern doubt, FDA rejection, post-earnings fade risk, lawsuit, halt risk, "
        "obvious pump-and-dump catalyst. Do NOT consider price action — that's another agent's job.\n"
        "Default to TRADE if news is neutral, supportive, or absent.\n\n"
        f"Ticker: {c['ticker']}\n"
        f"News: {c['news_count']} articles, sentiment={c['sentiment']:+.2f}, "
        f"earnings={'yes' if c['has_earn'] else 'no'}, fda={'yes' if c['has_fda'] else 'no'}\n"
        f"Top headline: {c['headline']!r}\n\n"
        'Reply JSON: {"action": "TRADE" or "SKIP", "reason": "<one short sentence>"}'
    )


def _risk_agent_prompt(c: dict) -> str:
    rr = abs(c["pred_mfe"] / c["pred_dd"]) if c["pred_dd"] < 0 else 0.0
    return (
        "You are the RISK AGENT for an intraday long-only trading bot.\n"
        "Your job: veto if predicted risk/reward is unfavorable or price action looks weak.\n"
        "Veto when: predicted reward < |predicted drawdown|, open5 is sharply negative while pm was positive "
        "(rejection), volume surge is weak (<1.5x), or model confidence (up_prob) is low.\n"
        "Approve when: pred_mfe substantially exceeds |pred_dd|, open5 confirms premarket direction, "
        "and volume is elevated. Default to TRADE when ambiguous.\n\n"
        f"Ticker: {c['ticker']}\n"
        f"Premarket %: {c['pm_pct']:+.2f}   Open5 %: {c['open5_pct']:+.2f}\n"
        f"Volume surge: {c['vol_surge']:.1f}x\n"
        f"Model: up_prob={c['up_prob']:.2f}  pred_mfe={c['pred_mfe']:+.2f}%  "
        f"pred_dd={c['pred_dd']:+.2f}%  R/R={rr:.2f}\n\n"
        'Reply JSON: {"action": "TRADE" or "SKIP", "reason": "<one short sentence>"}'
    )


def _market_agent_prompt(c: dict) -> str:
    rel = c["pm_pct"] - c["mkt_pm"]
    return (
        "You are the MARKET CONTEXT AGENT for an intraday long-only trading bot.\n"
        "Your job: veto when the broad market is strongly hostile to long entries.\n"
        "Veto when: SPY premarket is sharply negative (< -1%) AND the ticker is NOT showing relative "
        "strength, or SPY's first 5 min after open is heavily down (< -0.5%).\n"
        "Approve when: market is flat-to-positive, or ticker shows strong relative strength even in a "
        "weak tape. Default to TRADE when market is neutral.\n\n"
        f"Ticker: {c['ticker']}\n"
        f"SPY premarket %: {c['mkt_pm']:+.2f}   SPY open5 %: {c['mkt_open5']:+.2f}\n"
        f"Ticker premarket %: {c['pm_pct']:+.2f}   Relative strength vs SPY: {rel:+.2f}\n\n"
        'Reply JSON: {"action": "TRADE" or "SKIP", "reason": "<one short sentence>"}'
    )


def _call_llm_agent(prompt: str, label: str, ticker: str) -> dict:
    """Single Claude (Anthropic API) call with hardened JSON parsing.
    Defaults to TRADE on any failure — same fail-open behavior as before."""
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY missing from .env")
        r = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 120,
                "temperature": 0.0,
                "system": ('Reply with ONLY a JSON object exactly like '
                           '{"action": "TRADE", "reason": "<one short sentence>"} '
                           'where action is TRADE or SKIP. No other text.'),
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=AGENT_TIMEOUT_S,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        raw = "".join(b.get("text", "") for b in blocks
                      if b.get("type") == "text").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        data = json.loads(raw)
        action = str(data.get("action", "TRADE")).upper().strip()
        reason = str(data.get("reason", ""))[:200]
        if action not in ("TRADE", "SKIP"):
            action, reason = "TRADE", f"unparseable action, defaulting"
        return {"agent": label, "action": action, "reason": reason}
    except Exception as e:
        print(f"  [{label}] {ticker} error ({type(e).__name__}: {e}) — defaulting to TRADE")
        return {"agent": label, "action": "TRADE", "reason": f"{label} error: {type(e).__name__}"}


def agent_decide(ticker: str, row: dict, headline: str = "") -> dict:
    """Run all specialized Claude agents (Anthropic API) in parallel; aggregate their votes.

    Returns {action, reason, votes} where:
      - action: 'TRADE' if (AGENT_REQUIRE_ALL ? unanimous : majority) approve, else 'SKIP'
      - reason: combined short reason string
      - votes: list of per-agent decisions for logging
    """
    if not AGENT_ENABLED:
        return {"action": "TRADE", "reason": "agents disabled", "votes": []}

    c = _row_context(ticker, row, headline)
    agents = [
        ("news",   _news_agent_prompt(c)),
        ("risk",   _risk_agent_prompt(c)),
        ("market", _market_agent_prompt(c)),
    ]

    votes: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(agents)) as pool:
        futures = {pool.submit(_call_llm_agent, prompt, name, ticker): name
                   for name, prompt in agents}
        for fut in as_completed(futures):
            votes.append(fut.result())
    votes.sort(key=lambda v: v["agent"])

    skip_count = sum(1 for v in votes if v["action"] == "SKIP")
    if AGENT_REQUIRE_ALL:
        approved = (skip_count == 0)
    else:
        approved = (skip_count <= len(votes) // 2)

    if approved:
        action = "TRADE"
        reason = " | ".join(f"{v['agent']}:OK" for v in votes if v["action"] == "TRADE")
    else:
        action = "SKIP"
        reason = " | ".join(f"{v['agent']}:✗ {v['reason']}"
                            for v in votes if v["action"] == "SKIP")
    return {"action": action, "reason": reason[:280], "votes": votes}



def _with_retry(fn, *, label: str, max_retries: int = API_MAX_RETRIES):
    """Retry a REST call with exponential backoff. Raises after exhausting retries
    so the caller can distinguish 'API failure' from 'no data'."""
    delay = API_BACKOFF_BASE
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            msg = str(e).lower()
            is_rate = "429" in msg or "rate" in msg or "throttl" in msg
            if attempt >= max_retries:
                print(f"  [API] {label}: giving up after {attempt} attempts — {e}")
                raise
            print(f"  [API] {label}: attempt {attempt} failed ({'rate-limit' if is_rate else 'error'}: {e}); retrying in {delay:.1f}s")
            time.sleep(delay)
            delay *= 2
    if last_exc:
        raise last_exc


class MassiveClient:
    def __init__(self):
        self._client = RESTClient(os.getenv("MASSIVE_API_KEY", "").strip())

    def snapshot_all(self) -> list[TickerSnapshot]:
        return list(self._client.get_snapshot_all("stocks"))

    def bars(self, ticker: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=_ET)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=_ET)
        from_ms = int(from_dt.timestamp() * 1000)
        to_ms   = int(to_dt.timestamp() * 1000)
        aggs = _with_retry(
            lambda: self._client.get_aggs(
                ticker, multiplier=1, timespan="minute",
                from_=from_ms, to=to_ms, adjusted=False, sort="asc", limit=500,
            ),
            label=f"bars({ticker})",
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
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=_ET)
        from_utc = from_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        kwargs = {"published_utc_gte": from_utc, "limit": 50}
        if to_dt is not None:
            if to_dt.tzinfo is None:
                to_dt = to_dt.replace(tzinfo=_ET)
            kwargs["published_utc_lt"] = to_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        articles = _with_retry(
            lambda: self._client.list_ticker_news(ticker, **kwargs),
            label=f"news({ticker})",
        )
        result = []
        for a in articles:
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
                "is_earnings": bool(_EARNINGS_KW.search(title)),
                "is_fda":      bool(_FDA_KW.search(title)),
            })
        return result



_ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(_ET)


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



def _trading_dates(con) -> list[str]:
    rows = con.execute("""
        SELECT DISTINCT CAST(
            timezone('America/New_York',
                epoch_ms(CAST(window_start / 1000000 AS BIGINT))
            ) AS DATE
        ) AS d
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
            gap_vol_quality DOUBLE, price_bucket INTEGER,
            news_sentiment DOUBLE, news_count INTEGER, has_earnings INTEGER, has_fda INTEGER,
            open5_pct DOUBLE, open5_range_pct DOUBLE, open5_vwap DOUBLE, open5_volume DOUBLE,
            open_price DOUBLE, post_open_close DOUBLE, label INTEGER,
            intraday_open DOUBLE, intraday_close DOUBLE, intraday_high DOUBLE,
            intraday_low DOUBLE, intraday_volume DOUBLE, intraday_pct DOUBLE,
            intraday_range_pct DOUBLE, intraday_vwap DOUBLE, eod_label INTEGER,
            mkt_pm_pct DOUBLE, mkt_open5_pct DOUBLE, mkt_rel_pm_pct DOUBLE
        )
    """)
    for col in ("mkt_pm_pct", "mkt_open5_pct", "mkt_rel_pm_pct", "gap_vol_quality"):
        try:
            con.execute(f"ALTER TABLE features ADD COLUMN {col} DOUBLE")
        except Exception:
            pass
    try:
        con.execute("ALTER TABLE features ADD COLUMN price_bucket INTEGER")
    except Exception:
        pass


def _market_context(con, date_str: str) -> dict[str, float]:
    """SPY premarket % and first-5-min % — used as market-context features."""
    pm_s  = utc_ns(date_str, PREMARKET_START_H)
    pm_e  = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    o5_e  = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET + LABEL_ANCHOR_MIN)
    try:
        pm = con.execute(f"""
            SELECT FIRST(open ORDER BY window_start), LAST(close ORDER BY window_start)
            FROM minute_bars
            WHERE ticker = '{MARKET_TICKER}'
              AND window_start >= {pm_s} AND window_start < {pm_e}
        """).fetchone()
        o5 = con.execute(f"""
            SELECT FIRST(open ORDER BY window_start), LAST(close ORDER BY window_start)
            FROM minute_bars
            WHERE ticker = '{MARKET_TICKER}'
              AND window_start >= {pm_e} AND window_start < {o5_e}
        """).fetchone()
    except Exception:
        return {"mkt_pm_pct": 0.0, "mkt_open5_pct": 0.0}
    pm_pct = ((pm[1] - pm[0]) / pm[0] * 100) if (pm and pm[0]) else 0.0
    o5_pct = ((o5[1] - o5[0]) / o5[0] * 100) if (o5 and o5[0]) else 0.0
    return {"mkt_pm_pct": pm_pct, "mkt_open5_pct": o5_pct}


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


def _pm_volume_baseline(con, tickers, date_str: str, lookback_days: int = 14,
                        need_days: int = 5) -> dict[str, float]:
    """Single-query average premarket volume per ticker over the trailing window.
    Replaces the prior N×14 query pattern.
    """
    ticker_list = _ticker_sql_list(tickers)
    if not ticker_list:
        return {}
    end_ns   = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    start_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=lookback_days)
    start_ns = utc_ns(start_dt.strftime("%Y-%m-%d"), PREMARKET_START_H)
    try:
        rows = con.execute(f"""
            WITH pm AS (
                SELECT ticker,
                       CAST(timezone('America/New_York',
                            epoch_ms(CAST(window_start / 1000000 AS BIGINT))) AS DATE) AS d,
                       volume,
                       EXTRACT('hour' FROM timezone('America/New_York',
                            epoch_ms(CAST(window_start / 1000000 AS BIGINT)))) AS h
                FROM minute_bars
                WHERE window_start >= {start_ns} AND window_start < {end_ns}
                  AND ticker IN ({ticker_list})
            ),
            daily AS (
                SELECT ticker, d, SUM(volume) AS vol
                FROM pm
                WHERE h >= {PREMARKET_START_H} AND h < {OPEN_HOUR_ET}
                GROUP BY ticker, d
            ),
            ranked AS (
                SELECT ticker, d, vol,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY d DESC) AS rn
                FROM daily
            )
            SELECT ticker, AVG(vol) FROM ranked
            WHERE rn <= {need_days}
            GROUP BY ticker
        """).fetchall()
    except Exception:
        return {}
    return {t: float(v) for t, v in rows if v}


def _volume_surge_col(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df
    avg = _pm_volume_baseline(con, df["ticker"].tolist(), date_str)
    avg_series = df["ticker"].map(avg)
    df["pm_vol_surge"] = (df["pm_volume"] / avg_series).where(avg_series > 0, 1.0).fillna(1.0)

    pm_pct       = df["pm_pct"].fillna(0.0)
    pm_range_pct = df["pm_range_pct"].fillna(0.0).clip(lower=1e-6)
    df["gap_vol_quality"] = (df["pm_vol_surge"].fillna(1.0)
                             * pm_pct.abs()
                             / pm_range_pct)

    price = df["pm_close"].fillna(0.0)
    bins   = [-np.inf, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0, np.inf]
    df["price_bucket"] = pd.cut(price, bins=bins, labels=False).astype("Int64").fillna(0).astype(int)
    return df


def _sentiment_col(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        df["news_sentiment"] = 0.0
        df["news_count"]     = 0
        df["has_earnings"]   = 0
        df["has_fda"]        = 0
        return df
    ticker_list = _ticker_sql_list(df["ticker"])
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_ET)
    news_lo = (base - timedelta(days=1)).replace(hour=16, minute=0)
    news_hi = base.replace(hour=OPEN_HOUR_ET, minute=OPEN_MINUTE_ET + NEWS_CUTOFF_MIN)
    lo_str  = news_lo.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    hi_str  = news_hi.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = con.execute(f"""
            SELECT ticker, sentiment, title FROM market_news
            WHERE ticker IN ({ticker_list})
              AND published_utc >= TIMESTAMP '{lo_str}'
              AND published_utc <  TIMESTAMP '{hi_str}'
        """).fetchall()
    except Exception:
        df["news_sentiment"] = 0.0
        df["news_count"]     = 0
        df["has_earnings"]   = 0
        df["has_fda"]        = 0
        return df
    sample     = next((r[1] for r in rows if r[1] is not None), None)
    use_labels = isinstance(sample, str)
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
        score = SENTIMENT_MAP.get(str(val).lower().strip(), 0.0) if use_labels else float(val)
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
    open_s = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET + LABEL_ANCHOR_MIN)
    open_e = open_s + 60 * 1_000_000_000
    post_e = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET + LABEL_ANCHOR_MIN + HOLD_MINUTES)
    tp_mult = 1.0 + LABEL_TP_BARRIER_PCT / 100.0
    sl_mult = 1.0 - LABEL_SL_BARRIER_PCT / 100.0
    res = con.execute(f"""
        WITH opens AS (
            SELECT ticker, FIRST(open ORDER BY window_start) AS open_price
            FROM minute_bars
            WHERE window_start >= {open_s} AND window_start < {open_e}
              AND ticker IN ({ticker_list})
            GROUP BY ticker
        ),
        win AS (
            SELECT b.ticker, b.window_start, b.high, b.low, b.close, o.open_price
            FROM minute_bars b
            JOIN opens o ON b.ticker = o.ticker
            WHERE b.window_start >= {open_e} AND b.window_start < {post_e}
        )
        SELECT ticker, open_price,
               MIN(CASE WHEN high >= open_price * {tp_mult} THEN window_start END) AS tp_ts,
               MIN(CASE WHEN low  <= open_price * {sl_mult} THEN window_start END) AS sl_ts,
               LAST(close ORDER BY window_start) AS post_open_close
        FROM win
        GROUP BY ticker, open_price
    """).fetchdf()
    if res.empty:
        return df

    def _barrier_label(r) -> int:
        tp, sl = r["tp_ts"], r["sl_ts"]
        if pd.notna(tp) and (pd.isna(sl) or tp < sl):
            return 1
        if pd.notna(sl) and (pd.isna(tp) or sl <= tp):
            return 0
        op, pc = r["open_price"], r["post_open_close"]
        return int(pd.notna(pc) and pd.notna(op) and op > 0 and pc > op)

    res["label"] = res.apply(_barrier_label, axis=1)
    return df.merge(res[["ticker", "open_price", "post_open_close", "label"]],
                    on="ticker", how="left")


def _open5_features(con, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """First 5 minutes after open — safe to use as features since entry is confirmed after this window."""
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
    bars["open5_vwap"]      = (bars["o5_vwap"]  - bars["o5_open"]) / ref * 100
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


def _process_date(con, halal: set, date_str: str, verbose: bool = False) -> int:
    """Build features for one trading date. Prints per-stage timing when verbose."""
    def _t(label: str, fn, *args):
        t0 = time.perf_counter()
        out = fn(*args)
        if verbose:
            dt = (time.perf_counter() - t0) * 1000
            n  = len(out) if hasattr(out, "__len__") else "—"
            print(f"      · {label:<14} {dt:>7.0f} ms   rows={n}")
        return out

    t_total = time.perf_counter()
    if verbose:
        print(f"    {date_str} — building features:")
    df = _t("premarket",      _premarket_features, con, halal, date_str)
    if df.empty:
        if verbose:
            print(f"      (no premarket bars for {date_str} — skipping)")
        return 0
    df = _t("vol_surge",      _volume_surge_col,   con, df,    date_str)
    df = _t("sentiment",      _sentiment_col,      con, df,    date_str)
    df = _t("open5",          _open5_features,     con, df,    date_str)
    df = _t("post_open_lbl",  _post_open_label,    con, df,    date_str)
    df = _t("intraday",       _intraday_features,  con, df,    date_str)
    mkt = _t("market_ctx",    _market_context,     con, date_str)
    df["mkt_pm_pct"]     = mkt["mkt_pm_pct"]
    df["mkt_open5_pct"]  = mkt["mkt_open5_pct"]
    df["mkt_rel_pm_pct"] = df["pm_pct"] - mkt["mkt_pm_pct"]
    df.insert(0, "date", date_str)

    t_db = time.perf_counter()
    con.execute("DELETE FROM features WHERE date = ?", [date_str])
    con.execute("INSERT INTO features SELECT * FROM df")
    con.commit()
    if verbose:
        dt = (time.perf_counter() - t_db) * 1000
        print(f"      · db_write       {dt:>7.0f} ms   rows={len(df)}")
        print(f"      ─ total          {(time.perf_counter() - t_total)*1000:>7.0f} ms")
    return len(df)


def run_data_pipeline(rebuild: bool = False, date_arg: str | None = None,
                      verbose: bool = True):
    """Build the per-date feature table. When verbose, prints per-stage timing.

    Set verbose=False (or pass --quiet on the CLI) to revert to one-line-per-date.
    """
    t_pipeline_start = time.perf_counter()
    print(f"\n{'='*60}")
    print("  MAMDOUH — DATA PIPELINE")
    print(f"  Start: {now_et().strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"{'='*60}")

    print(f"\n  [1/4] Connecting to DuckDB at {DB_PATH.name} …")
    t0 = time.perf_counter()
    con = duckdb.connect(str(DB_PATH))
    print(f"        connected ({(time.perf_counter()-t0)*1000:.0f} ms)")

    if rebuild:
        print("\n  [2/4] Dropping existing features table …")
        con.execute("DROP TABLE IF EXISTS features")
        print("        dropped.")
    else:
        print("\n  [2/4] Ensuring features table schema …")
    _ensure_features_table(con)
    con.commit()
    print("        schema ready.")

    print("\n  [3/4] Loading halal universe and computing date list …", flush=True)
    halal = load_halal()
    print(f"        Halal tickers: {len(halal)}", flush=True)

    if date_arg:
        dates_to_process = [date_arg]
        print(f"        Single date requested: {date_arg}", flush=True)
    else:
        print("        scanning minute_bars for trading dates "
              "(slow on large tables — full scan with timezone conversion) … ",
              end="", flush=True)
        t0 = time.perf_counter()
        all_dates  = _trading_dates(con)
        print(f"found {len(all_dates)} ({time.perf_counter()-t0:.1f}s)", flush=True)

        print("        querying features table for already-processed dates … ",
              end="", flush=True)
        t0 = time.perf_counter()
        done_dates = _already_processed(con)
        print(f"{len(done_dates)} done ({time.perf_counter()-t0:.1f}s)", flush=True)

        dates_to_process = [d for d in all_dates if d not in done_dates]
        print(f"        → to process: {len(dates_to_process)}", flush=True)

    if not dates_to_process:
        print("        Nothing to process. Skipping per-date loop.")
    else:
        print(f"\n  [4/4] Processing {len(dates_to_process)} date(s) …")

    total_rows = 0
    t_loop = time.perf_counter()
    for i, date_str in enumerate(dates_to_process, 1):
        t_date = time.perf_counter()
        n = _process_date(con, halal, date_str, verbose=verbose)
        total_rows += n
        dt = time.perf_counter() - t_date
        elapsed = time.perf_counter() - t_loop
        avg = elapsed / i
        eta_s = avg * (len(dates_to_process) - i)
        if verbose:
            print(f"    [{i}/{len(dates_to_process)}] {date_str} → {n} tickers in "
                  f"{dt:.1f}s   (total rows={total_rows}, "
                  f"elapsed={elapsed/60:.1f}m, ETA {eta_s/60:.1f}m)")
        else:
            print(f"\r    [{i}/{len(dates_to_process)}] {date_str} → {n} tickers  "
                  f"(total: {total_rows}, ETA {eta_s/60:.1f}m)",
                  end="", flush=True)

    if not verbose:
        print()
    print(f"\n  Data pipeline complete. {total_rows} rows written across "
          f"{len(dates_to_process)} date(s).")
    print(f"  Loop time: {(time.perf_counter()-t_loop)/60:.2f} min")

    print(f"\n  Exporting features → CSV …")
    t0 = time.perf_counter()
    CSV_OUT.parent.mkdir(exist_ok=True)
    csv_path = str(CSV_OUT).replace("'", "''")
    con.execute(f"COPY (SELECT * FROM features ORDER BY date, ticker) "
                f"TO '{csv_path}' (HEADER, DELIMITER ',')")
    print(f"  Saved: {CSV_OUT.relative_to(BASE_DIR)}  ({(time.perf_counter()-t0)*1000:.0f} ms)")
    con.close()

    print(f"\n  Pipeline total wall time: "
          f"{(time.perf_counter() - t_pipeline_start)/60:.2f} min")



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
    if len(hist_dates) < MIN_TRAIN_DAYS and "--allow-undertrained" not in sys.argv:
        raise RuntimeError(
            f"Only {len(hist_dates)} historical dates available (need {MIN_TRAIN_DAYS}). "
            f"Pass --allow-undertrained to override."
        )
    train_df = pd.DataFrame()
    if hist_dates:
        placeholders = ",".join("?" * len(hist_dates))
        train_df = con.execute(f"""
            SELECT * FROM features
            WHERE date IN ({placeholders}) AND label IS NOT NULL AND post_open_close IS NOT NULL
        """, hist_dates).fetchdf()
    today_df = con.execute("SELECT * FROM features WHERE date = ?", [target_date]).fetchdf()
    con.close()
    return train_df, today_df, target_date


def _prepare_targets(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
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
    if "intraday_high" in df.columns and "intraday_open" in df.columns:
        df["target_mfe"] = np.where(
            df["intraday_open"] > 0,
            (df["intraday_high"] - df["intraday_open"]) / df["intraday_open"] * 100,
            np.nan,
        )
    else:
        df["target_mfe"] = np.nan
    df["target_mag"] = df["target_gain"].abs()
    dates = sorted(df["date"].unique())
    train_cut = dates[int(len(dates) * 0.8)]
    for col in ["target_gain", "target_dd", "target_mfe", "target_mag"]:
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


class _EnsembleClassifier:
    """Soft-voting ensemble of CatBoost + LightGBM probability estimates.

    `predict_proba` averages each member's class-1 probability, so downstream
    code that reads `proba[:, 1]` keeps working unchanged.
    """

    def __init__(self, members):
        self.members = [m for m in members if m is not None]
        self.classes_ = np.array([0, 1])

    def predict_proba(self, X):
        if not self.members:
            n = len(X)
            out = np.full((n, 2), 0.5)
            return out
        probs = []
        for m in self.members:
            p = m.predict_proba(X)
            probs.append(np.asarray(p)[:, 1])
        avg = np.mean(probs, axis=0)
        return np.column_stack([1.0 - avg, avg])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


def _recency_weights(dates: pd.Series, half_life_days: int = LABEL_RECENCY_HALFLIFE_DAYS):
    """Exponentially decaying sample weights — recent days count more, since the
    market regime drifts and stale history shouldn't dominate the fit."""
    d   = pd.to_datetime(dates)
    age = (d.max() - d).dt.days.to_numpy().astype(float)
    return np.power(0.5, age / max(half_life_days, 1))


def _make_base_classifier():
    """Strongest available gradient-boosted classifier, falling back to RF.
    CatBoost/LightGBM beat RandomForest on this tabular feature set; all expose
    a sklearn-compatible fit(X, y, sample_weight=...) so they slot into
    CalibratedClassifierCV and accept recency weights."""
    if _HAS_CAT:
        return CatBoostClassifier(
            iterations=500, learning_rate=0.03, depth=6, l2_leaf_reg=3.0,
            loss_function="Logloss", auto_class_weights="Balanced",
            random_seed=42, verbose=0, allow_writing_files=False,
        )
    if _HAS_LGB:
        return lgb.LGBMClassifier(
            n_estimators=500, learning_rate=0.03, num_leaves=31,
            min_child_samples=20, subsample=0.85, colsample_bytree=0.85,
            class_weight="balanced", random_state=42, n_jobs=-1, verbose=-1,
        )
    return RandomForestClassifier(
        n_estimators=500, max_depth=7, min_samples_leaf=8,
        max_features="sqrt", class_weight="balanced", random_state=42, n_jobs=-1,
    )


def _train_classifier(df: pd.DataFrame):
    """Probability-calibrated gradient-boosted classifier.

    Two changes over the old plain-RandomForest fit:
      • The base learner is CatBoost/LightGBM (RF only as a last resort).
      • The deployed model is wrapped in CalibratedClassifierCV with a
        TimeSeriesSplit, so `up_prob` is *calibrated* — essential because the
        live engine gates on an absolute probability threshold (MIN_UP_PROB).
    Recency-weighted so recent regime dominates. Validation on the held-out tail
    reports both classification quality and the Brier score (calibration error).
    """
    train, test = _split(df)
    use_eval = len(test) > 50

    X_test,  y_test  = test[FEATURES].fillna(0), test["label"]
    X_full,  y_full  = df  [FEATURES].fillna(0), df  ["label"]

    if use_eval:
        X_train, y_train = train[FEATURES].fillna(0), train["label"]
        base = _make_base_classifier()
        base.fit(X_train, y_train, sample_weight=_recency_weights(train["date"]))
        up_idx = int(np.where(base.classes_ == 1)[0][0]) if 1 in base.classes_ else -1
        proba  = base.predict_proba(X_test)[:, up_idx]
        preds  = (proba >= 0.5).astype(int)
        print("\n  [Classifier · gradient-boosted] Validation:")
        print(classification_report(y_test, preds, target_names=["DOWN", "UP"], digits=3))
        try:
            print(f"  Brier score (uncalibrated, lower is better): "
                  f"{brier_score_loss(y_test, proba):.4f}")
        except Exception:
            pass

    n_splits   = min(4, max(2, df["date"].nunique() - 1))
    w_full     = _recency_weights(df["date"])
    calibrated = CalibratedClassifierCV(
        _make_base_classifier(), method="sigmoid",
        cv=TimeSeriesSplit(n_splits=n_splits),
    )
    try:
        calibrated.fit(X_full, y_full, sample_weight=w_full)
        return calibrated
    except Exception as e:
        print(f"  [Classifier] calibration failed ({type(e).__name__}: {e}) — "
              f"returning uncalibrated base.")
        base = _make_base_classifier()
        base.fit(X_full, y_full, sample_weight=w_full)
        return base


def _derive_up_prob_gate(clf, df: pd.DataFrame, percentile: float = GATE_PERCENTILE) -> float:
    """Auto-derive the conviction gate from the classifier's own probability
    distribution: the `percentile`-th percentile of up_prob, i.e. keep only the
    top (100 − percentile)% most-confident names. Evaluated on the held-out tail
    (less optimistic than the rows the model was fit on) when one is available.
    Replaces the hardcoded MIN_UP_PROB so the gate tracks calibration automatically.
    """
    _, test = _split(df)
    sample  = test if len(test) > 50 else df
    X = sample[FEATURES].fillna(0)
    up_idx = int(np.where(clf.classes_ == 1)[0][0]) if 1 in clf.classes_ else -1
    probs  = clf.predict_proba(X)[:, up_idx]
    gate   = float(np.percentile(probs, percentile))
    print(f"  Auto up_prob gate: {gate:.4f}  "
          f"(P{percentile:.0f} of {len(probs):,} probs; "
          f"range {probs.min():.3f}–{probs.max():.3f})")
    return gate


def _train_ranker(df: pd.DataFrame):
    """LightGBM LambdaRank on date-grouped data.

    Labels are graded by within-day quintile of post-open return — the model
    learns to rank top names per day, which is what we actually act on
    (we only enter MAX_POSITIONS trades, so NDCG@2 is the right objective).
    """
    if not _HAS_LGB:
        print("  [Ranker] lightgbm not installed — skipping. `pip install lightgbm` to enable.")
        return None
    sub = df[df["target_gain"].notna()].copy()
    if sub.empty:
        return None
    sub = sub.sort_values(["date", "ticker"]).reset_index(drop=True)
    sub["rank_label"] = (
        sub.groupby("date")["target_gain"]
           .transform(lambda s: pd.qcut(s.rank(method="first"), q=5,
                                        labels=False, duplicates="drop"))
           .fillna(0).astype(int)
    )
    train, test = _split(sub)
    if len(test) < 50 or train.empty:
        return None
    g_train = train.groupby("date").size().to_numpy()
    g_test  = test.groupby("date").size().to_numpy()
    ranker = lgb.LGBMRanker(
        objective="lambdarank", metric="ndcg",
        n_estimators=400, learning_rate=0.05,
        num_leaves=31, min_child_samples=20,
        subsample=0.85, colsample_bytree=0.85,
        random_state=42, n_jobs=-1, verbose=-1,
        label_gain=[0, 1, 3, 7, 15],
    )
    try:
        ranker.fit(
            train[FEATURES].fillna(0), train["rank_label"],
            group=g_train,
            eval_set=[(test[FEATURES].fillna(0), test["rank_label"])],
            eval_group=[g_test], eval_at=[2, 3, 5],
            callbacks=[lgb.early_stopping(30, verbose=False),
                       lgb.log_evaluation(period=0)],
        )
        best_iter = getattr(ranker, "best_iteration_", None) or ranker.n_estimators
        full = lgb.LGBMRanker(
            objective="lambdarank", metric="ndcg",
            n_estimators=best_iter, learning_rate=0.05,
            num_leaves=31, min_child_samples=20,
            subsample=0.85, colsample_bytree=0.85,
            random_state=42, n_jobs=-1, verbose=-1,
            label_gain=[0, 1, 3, 7, 15],
        )
        g_all = sub.groupby("date").size().to_numpy()
        full.fit(sub[FEATURES].fillna(0), sub["rank_label"], group=g_all)
        print(f"  [Ranker] Trained on {len(sub):,} rows / {sub['date'].nunique()} days "
              f"(best_iter={best_iter}). Validation NDCG@2 reported above.")
        return full
    except Exception as e:
        print(f"  [Ranker] failed: {e}")
        return None


def _train_regressor(df: pd.DataFrame, target_col: str, label: str):
    """CatBoost regressor with early stopping; refits on full data using best iter.
    Falls back to a simple CatBoost without early stopping if catboost is missing
    (sklearn GradientBoosting fallback removed; install catboost for full quality)."""
    sub = df[df[target_col].notna()].copy()
    if len(sub) < 200:
        print(f"  [Regressor {label}] Not enough data ({len(sub)} rows), skipping.")
        return None
    train, test = _split(sub)
    use_eval = len(test) > 50

    if _HAS_CAT:
        reg = CatBoostRegressor(
            iterations=800, learning_rate=0.03, depth=6,
            l2_leaf_reg=3.0, random_seed=42, loss_function="RMSE",
            od_type="Iter", od_wait=50,
            verbose=0, allow_writing_files=False,
        )
        if use_eval:
            reg.fit(
                train[FEATURES].fillna(0), train[target_col],
                eval_set=(test[FEATURES].fillna(0), test[target_col]),
                use_best_model=True,
            )
            preds = reg.predict(test[FEATURES].fillna(0))
            mae  = mean_absolute_error(test[target_col], preds)
            rmse = float(np.sqrt(((test[target_col] - preds) ** 2).mean()))
            r2   = r2_score(test[target_col], preds)
            print(f"  [Regressor · CatBoost {label}] MAE: {mae:.2f}%  RMSE: {rmse:.2f}%  R²: {r2:.3f}")
            best_iter = int(getattr(reg, "best_iteration_", 0) or reg.tree_count_)
            final = CatBoostRegressor(
                iterations=best_iter, learning_rate=0.03, depth=6,
                l2_leaf_reg=3.0, random_seed=42, loss_function="RMSE",
                verbose=0, allow_writing_files=False,
            )
            final.fit(sub[FEATURES].fillna(0), sub[target_col])
            return final
        reg.fit(sub[FEATURES].fillna(0), sub[target_col])
        return reg

    if _HAS_LGB:
        print(f"  [Regressor {label}] catboost missing — using LightGBM fallback.")
        reg = lgb.LGBMRegressor(
            n_estimators=600, learning_rate=0.03, num_leaves=31,
            min_child_samples=20, subsample=0.85, colsample_bytree=0.85,
            random_state=42, n_jobs=-1, verbose=-1,
        )
        if use_eval:
            reg.fit(train[FEATURES].fillna(0), train[target_col],
                    eval_set=[(test[FEATURES].fillna(0), test[target_col])],
                    callbacks=[lgb.early_stopping(40, verbose=False),
                               lgb.log_evaluation(period=0)])
            preds = reg.predict(test[FEATURES].fillna(0))
            mae  = mean_absolute_error(test[target_col], preds)
            r2   = r2_score(test[target_col], preds)
            print(f"  [Regressor · LGB {label}] MAE: {mae:.2f}%  R²: {r2:.3f}")
        reg.fit(sub[FEATURES].fillna(0), sub[target_col])
        return reg

    from sklearn.ensemble import GradientBoostingRegressor
    print(f"  [Regressor {label}] no boosting lib — using sklearn GBR fallback.")
    reg = GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=15, max_features=0.8, random_state=42,
    )
    reg.fit(sub[FEATURES].fillna(0), sub[target_col])
    return reg


def _load_or_train(train_df: pd.DataFrame, n_hist: int, retrain: bool) -> dict:
    feat_sig = ",".join(sorted(FEATURES))
    if not retrain and MODEL_PATH.exists():
        with open(MODEL_PATH, "rb") as f:
            cached = pickle.load(f)
        if cached.get("feat_sig") == feat_sig and cached.get("n_dates", 0) >= n_hist - 5:
            print(f"  Cached models loaded (trained on {cached['n_dates']} dates)")
            return cached["models"]
        print("  Features changed or stale — retraining.")

    print(f"  Training on {len(train_df):,} rows from {n_hist} dates …")
    if train_df.empty or len(train_df) < 200:
        raise RuntimeError("Not enough training data.")

    assert_no_leakage()
    df = _ensure_features(_prepare_targets(train_df))
    models = {
        "clf":      _train_classifier(df),
        "reg_gain": _train_regressor(df, "target_gain", "gain%"),
        "reg_dd":   _train_regressor(df, "target_dd",   "drawdown% (MAE all-day)"),
        "reg_mfe":  _train_regressor(df, "target_mfe",  "max-favorable% (MFE all-day)"),
        "reg_mag":  _train_regressor(df, "target_mag",  "move-magnitude% (sizing)"),
        "ranker":   _train_ranker(df),
    }
    models["up_prob_gate"] = _derive_up_prob_gate(models["clf"], df)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"models": models, "n_dates": n_hist, "feat_sig": feat_sig}, f)
    print(f"  Models saved → result v2/models.pkl")
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
    ranked = today_df.sort_values("pred_gain_pct", ascending=False)

    print(f"\n  TOP {TOP_N_WATCH} predictions for {target_date}:")
    print(f"  {'#':<3} {'Ticker':<8} {'UpProb':>7} {'PredGain%':>10} {'PredDD%':>9}")
    print(f"  {'-'*44}")
    for i, (_, r) in enumerate(ranked.head(TOP_N_WATCH).iterrows(), 1):
        pg  = f"{r['pred_gain_pct']:>+9.2f}%" if pd.notna(r.get("pred_gain_pct")) else "       n/a"
        dd  = f"{r['pred_dd_pct']:>+8.2f}%"   if pd.notna(r.get("pred_dd_pct"))   else "      n/a"
        print(f"  {i:<3} {r['ticker']:<8} {r['up_prob']*100:>6.1f}%  {pg}  {dd}")

    return models, ranked, target_date



def _pm_volume_history(tickers: list[str], date_str: str) -> dict[str, float]:
    """Live wrapper around `_pm_volume_baseline` — single-query DuckDB lookup."""
    if not tickers:
        return {}
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
    except Exception:
        return {}
    try:
        return _pm_volume_baseline(con, tickers, date_str)
    finally:
        con.close()


def build_features(client: MassiveClient, tickers: list[str], today: datetime) -> pd.DataFrame:
    mkt_open  = today.replace(hour=9,  minute=30, second=0, microsecond=0)
    pm_start  = today.replace(hour=PREMARKET_START_H, minute=0, second=0, microsecond=0)
    pm_mid    = pm_start + timedelta(minutes=15)
    news_from = (today - timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    news_to   = today.replace(hour=OPEN_HOUR_ET, minute=OPEN_MINUTE_ET + NEWS_CUTOFF_MIN,
                              second=0, microsecond=0)
    id_end    = now_et().replace(second=0, microsecond=0)
    total     = len(tickers)
    pm_vol_avg = _pm_volume_history(tickers, today.strftime("%Y-%m-%d"))

    mkt_pm_pct = mkt_open5_pct = 0.0
    try:
        mkt_bars = client.bars(MARKET_TICKER, pm_start, id_end)
        if mkt_bars:
            mdf = pd.DataFrame(mkt_bars).sort_values("ts")
            mkt_open_ts0 = mkt_open.timestamp()
            mpm = mdf[mdf["ts"] < mkt_open_ts0]
            mid = mdf[mdf["ts"] >= mkt_open_ts0].head(LABEL_ANCHOR_MIN)
            if not mpm.empty and float(mpm["open"].iloc[0]):
                mkt_pm_pct = (float(mpm["close"].iloc[-1]) - float(mpm["open"].iloc[0])) / float(mpm["open"].iloc[0]) * 100
            if not mid.empty and float(mid["open"].iloc[0]):
                mkt_open5_pct = (float(mid["close"].iloc[-1]) - float(mid["open"].iloc[0])) / float(mid["open"].iloc[0]) * 100
    except Exception as e:
        print(f"  [market context] {MARKET_TICKER} fetch failed: {e}")
    print(f"  Market context: {MARKET_TICKER} pm={mkt_pm_pct:+.2f}%  open5={mkt_open5_pct:+.2f}%")

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

            pm_pct = pm_momentum = pm_range_pct = 0.0
            pm_volume = 0.0
            pm_close = 0.0
            if not pdf.empty:
                pm_open  = float(pdf["open"].iloc[0])
                pm_close = float(pdf["close"].iloc[-1])
                pm_high  = float(pdf["high"].max())
                pm_low   = float(pdf["low"].min())
                pm_volume = float(pdf["volume"].sum())
                if pm_open > 0:
                    pm_pct = (pm_close - pm_open) / pm_open * 100
                    pm_range_pct = (pm_high - pm_low) / pm_open * 100
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

            gap_vol_quality = pm_vol_surge * abs(pm_pct) / max(pm_range_pct, 1e-6)
            _pb_bins = [-np.inf, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0, np.inf]
            price_bucket = int(pd.cut([pm_close], bins=_pb_bins, labels=False)[0]) \
                if pm_close > 0 else 0

            if idf.empty:
                return None
            id_open_p = idf["open"].iloc[0]
            if not id_open_p:
                return None

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

            o5       = idf.head(5)
            o5_open  = o5["open"].iloc[0]
            o5_close = o5["close"].iloc[-1]
            o5_high  = o5["high"].max()
            o5_low   = o5["low"].min()
            o5_vol   = o5["volume"].sum()
            o5_vwap_abs     = (o5["close"] * o5["volume"]).sum() / o5_vol if o5_vol > 0 else o5_close
            open5_pct       = (o5_close - o5_open)    / o5_open * 100 if o5_open else 0.0
            open5_range_pct = (o5_high  - o5_low)     / o5_open * 100 if o5_open else 0.0
            o5_vwap         = (o5_vwap_abs - o5_open) / o5_open * 100 if o5_open else 0.0

            news_items   = client.news(ticker, news_from, news_to)
            sentiments   = [SENTIMENT_MAP.get((n.get("sentiment") or "").lower().strip(), 0.0) for n in news_items]
            avg_sent     = float(np.mean(sentiments)) if sentiments else 0.0
            has_earnings = int(any(n.get("is_earnings") for n in news_items))
            has_fda      = int(any(n.get("is_fda")      for n in news_items))
            headline     = (news_items[0].get("title") or "")[:70] if news_items else ""
            return {
                "ticker": ticker, "headline": headline,
                "open5_pct": open5_pct, "open5_range_pct": open5_range_pct,
                "open5_vwap": o5_vwap,  "open5_volume": float(o5_vol),
                "news_sentiment": avg_sent, "news_count": len(sentiments),
                "has_earnings": has_earnings, "has_fda": has_fda,
                "pm_pct": pm_pct, "pm_momentum": pm_momentum, "pm_vol_surge": pm_vol_surge,
                "pm_range_pct": pm_range_pct, "gap_vol_quality": gap_vol_quality,
                "price_bucket": price_bucket,
                "mkt_pm_pct":     mkt_pm_pct,
                "mkt_open5_pct":  mkt_open5_pct,
                "mkt_rel_pm_pct": pm_pct - mkt_pm_pct,
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
        return f"{v:>+{w}.2f}%" if not np.isnan(float(v)) else f"{'n/a':>{w+1}}"

    with ThreadPoolExecutor(max_workers=10) as pool:
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

    def place_entry(self, ticker: str, qty: int, entry: float,
                    fill_timeout: float = 30.0) -> tuple[int, float]:
        """LimitOrder BUY at Massive's last price. Returns (filled_qty, avg_fill_price)."""
        if not IBKR_ENABLED or self.ib is None:
            return 0, 0.0
        try:
            from ib_insync import Stock, LimitOrder
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            trade = self.ib.placeOrder(contract, LimitOrder("BUY", qty, round(entry, 2)))
            print(f"  IBKR BUY sent: {ticker} qty={qty} @ {entry:.2f}")
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
                return 0, 0.0
            try:
                fills = getattr(trade, "fills", []) or []
                num = sum(float(f.execution.shares) * float(f.execution.price) for f in fills)
                den = sum(float(f.execution.shares) for f in fills)
                avg_px = num / den if den > 0 else float(trade.orderStatus.avgFillPrice or entry)
            except Exception:
                avg_px = float(getattr(trade.orderStatus, "avgFillPrice", 0.0)) or entry
            print(f"  IBKR BUY {ticker}: filled {filled}/{qty} @ avg {avg_px:.4f}")
            return filled, avg_px
        except Exception as e:
            print(f"  IBKR BUY failed for {ticker}: {e}")
            return 0, 0.0

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

    def close_position(self, ticker: str, qty: int,
                       fill_timeout: float = 15.0) -> tuple[bool, float]:
        """Returns (success, avg_fill_price). Price is 0.0 if not filled in time."""
        if not IBKR_ENABLED or self.ib is None:
            return False, 0.0
        try:
            from ib_insync import Stock, MarketOrder
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            trade = self.ib.placeOrder(contract, MarketOrder("SELL", qty))
            print(f"  IBKR close sent: {ticker} qty={qty}")
            deadline = time.time() + fill_timeout
            while time.time() < deadline:
                self.ib.waitOnUpdate(timeout=1.0)
                if trade.isDone():
                    break
            try:
                fills = getattr(trade, "fills", []) or []
                num = sum(float(f.execution.shares) * float(f.execution.price) for f in fills)
                den = sum(float(f.execution.shares) for f in fills)
                avg_px = num / den if den > 0 else float(trade.orderStatus.avgFillPrice or 0.0)
            except Exception:
                avg_px = float(getattr(trade.orderStatus, "avgFillPrice", 0.0))
            return True, avg_px
        except Exception as e:
            print(f"  IBKR close failed for {ticker}: {e}")
            return False, 0.0



TP_FRACTION = 0.60
SL_FRACTION = 0.80
TP_MIN_PCT  = 0.8
SL_MIN_PCT  = -0.8
TP_MAX_PCT  = 15.0
SL_MAX_PCT  = -10.0
MIN_RR      = 1.2


def calc_tp_sl(
    entry_price: float,
    pred_gain: float, pred_dd: float, pred_mfe: float,
    pm_pct: float, up_prob: float,
    **_ignored,
) -> tuple[float, float]:
    """Full-day TP/SL using learned MFE/MAE predictions.

    TP = TP_FRACTION × pred_mfe  (conservative — exit before the peak)
    SL = SL_FRACTION × pred_dd   (cushioned — give room but cap loss)

    Falls back to pm_pct-scaled heuristic if predictions are NaN.
    """
    if not np.isnan(pred_mfe) and pred_mfe > 0:
        raw_tp = pred_mfe * TP_FRACTION
    elif not np.isnan(pred_gain) and pred_gain > 0:
        raw_tp = pred_gain * 1.3
    else:
        raw_tp = max(abs(pm_pct) * 1.5, TP_MIN_PCT)
    raw_tp *= 0.7 + 0.3 * up_prob
    tp_pct = float(np.clip(raw_tp, TP_MIN_PCT, TP_MAX_PCT))

    if not np.isnan(pred_dd) and pred_dd < 0:
        raw_sl = pred_dd * SL_FRACTION
    else:
        raw_sl = -max(abs(pm_pct) * 0.5, abs(SL_MIN_PCT))
    sl_pct = float(np.clip(raw_sl, SL_MAX_PCT, SL_MIN_PCT))

    if abs(sl_pct) * MIN_RR > tp_pct:
        tp_pct = float(min(abs(sl_pct) * MIN_RR, TP_MAX_PCT))

    return (
        round(entry_price * (1 + tp_pct / 100), 2),
        round(entry_price * (1 + sl_pct / 100), 2),
    )



def confirm_entry(client: MassiveClient, ticker: str, today: datetime,
                  n_bars: int = CONFIRM_BARS,
                  max_wait_s: float = 20.0) -> tuple[bool, float, str]:
    open_start = today.replace(hour=9, minute=30, second=0, microsecond=0)
    open_end   = open_start + timedelta(minutes=n_bars + 1)
    bars = []
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        bars = client.bars(ticker, open_start, open_end)
        if len(bars) >= n_bars:
            break
        time.sleep(2)
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
    print(f"    {ticker}: bar_open={bar_open:.2f}  bar_close={last_close:.2f}  {arrow}{abs(chg):.2f}%  [{status}]")
    if not confirmed:
        return False, 0.0, ""
    live_price, entry_time = client.price(ticker)
    entry_price = live_price if live_price > 0 else last_close
    print(f"    {ticker}: live entry price = {entry_price:.2f}  @ {entry_time} ET")
    return True, entry_price, entry_time


def _make_position(row: dict, entry_price: float, entry_time: str, today: datetime) -> dict:
    pred_gain = float(row["pred_gain_pct"]) if pd.notna(row.get("pred_gain_pct")) else np.nan
    pred_dd   = float(row["pred_dd_pct"])   if pd.notna(row.get("pred_dd_pct"))   else np.nan
    pred_mfe  = float(row["pred_mfe_pct"])  if pd.notna(row.get("pred_mfe_pct"))  else np.nan
    up_prob   = float(row["up_prob"])
    slip      = SLIPPAGE_BPS / 10_000.0
    fill_px   = entry_price * (1.0 + slip)
    shares    = int(CAPITAL_PER_TRADE // fill_px) if fill_px > 0 else 0
    tp_price, sl_price = calc_tp_sl(
        fill_px, pred_gain, pred_dd, pred_mfe,
        row.get("pm_pct", 0.0), up_prob,
    )
    entry_ts = now_et().timestamp()
    return {
        "ticker":     row["ticker"],
        "date":       today.strftime("%Y-%m-%d"),
        "entry":      fill_px,
        "quote_px":   entry_price,
        "shares":     shares,
        "entry_time": entry_time,
        "entry_ts":   entry_ts,
        "tp":         tp_price,
        "sl":         sl_price,
        "tp_pct":     (tp_price - entry_price) / entry_price * 100,
        "sl_pct":     (sl_price - entry_price) / entry_price * 100,
        "pred_gain":  pred_gain,
        "pred_dd":    pred_dd,
        "pred_mfe":   pred_mfe,
        "up_prob":    up_prob,
    }


def _log_exit(p: dict, price: float, reason: str) -> dict:
    exit_time = now_et().strftime("%H:%M:%S")
    ibkr_qty  = int(p.get("ibkr_qty", 0) or 0)
    if IBKR_ENABLED and ibkr_qty > 0:
        ok, fill_px = IBKRClient.get().close_position(p["ticker"], ibkr_qty)
        if ok and fill_px > 0:
            price = fill_px
    else:
        price = price * (1.0 - SLIPPAGE_BPS / 10_000.0)
    shares    = int(p.get("shares", 0) or 0)
    fees      = 2.0 * COMMISSION_PER_SIDE
    pnl_gross = (price - p["entry"]) * shares
    pnl_net   = pnl_gross - fees
    pnl       = (price - p["entry"]) / p["entry"] * 100
    pnl_net_pct = (pnl_net / CAPITAL_PER_TRADE) * 100 if CAPITAL_PER_TRADE > 0 else pnl
    emoji     = "🎯" if reason == "TP" else ("🛑" if reason == "SL" else "🔔")
    sep       = "=" * 50
    print(f"\n{sep}")
    print(f"  {emoji} [{reason}] {p['ticker']}")
    print(f"  Buy  : {p['entry']:.2f}  @ {p.get('entry_time', '?')} ET  (shares={shares})")
    print(f"  Exit : {price:.2f}  @ {exit_time} ET")
    print(f"  PnL  : {pnl:+.2f}%  |  Net $ : {pnl_net:+.2f}  (gross {pnl_gross:+.2f} − fees {fees:.2f})")
    print(f"  TP   : {p['tp']:.2f}  |  SL : {p['sl']:.2f}")
    print(sep)
    ibkr_tag = (
        f"IBKR ✓ qty={ibkr_qty}"
        if (IBKR_ENABLED and ibkr_qty > 0)
        else f"IBKR ✗ {p.get('ibkr_status', 'not placed')}"
    )
    print(f"  {ibkr_tag}")
    _tg_send(
        f"{emoji} <b>[{reason}] {p['ticker']}</b>\n"
        f"  Buy  : {p['entry']:.2f} @ {p.get('entry_time', '?')} ET\n"
        f"  Exit : {price:.2f} @ {exit_time} ET\n"
        f"  PnL  : {pnl:+.2f}%  (net ${pnl_net:+.2f} on {shares} sh, fees ${fees:.2f})\n"
        f"  TP={p['tp']:.2f}  SL={p['sl']:.2f}\n"
        f"  {ibkr_tag}"
    )
    return {
        **p,
        "exit": price, "exit_time": exit_time,
        "pnl_pct": round(pnl, 3),
        "pnl_net_pct": round(pnl_net_pct, 3),
        "pnl_gross_usd": round(pnl_gross, 2),
        "pnl_net_usd": round(pnl_net, 2),
        "fees_usd": round(fees, 2),
        "reason": reason,
    }



def save_state(open_pos: dict, today: str):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps({"date": today, "positions": open_pos}, indent=2))


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


LOG_COLUMNS = [
    "date", "ticker", "entry_time", "entry", "exit_time", "exit",
    "tp", "sl", "tp_pct", "sl_pct",
    "pred_gain", "pred_dd", "up_prob",
    "pnl_pct", "pnl_net_pct", "pnl_gross_usd", "pnl_net_usd", "fees_usd",
    "shares", "quote_px",
    "reason", "ibkr_qty", "ibkr_placed", "ibkr_status", "agent_reason",
]


def save_log(results: list[dict]):
    if not results:
        return
    LOG_PATH.parent.mkdir(exist_ok=True)
    df_new = pd.DataFrame(results).reindex(columns=LOG_COLUMNS)
    write_header = not LOG_PATH.exists()
    df_new.to_csv(LOG_PATH, mode="a", header=write_header, index=False)
    print(f"\nLog appended → result v2/live_log.csv  (+{len(df_new)} trades)")



def _apply_scores(df: pd.DataFrame, models: dict) -> pd.DataFrame:
    df = _ensure_features(df.copy())
    X = df[FEATURES].fillna(0)
    clf = models["clf"]
    up_idx = int(np.where(clf.classes_ == 1)[0][0]) if 1 in clf.classes_ else -1
    df["up_prob"]       = clf.predict_proba(X)[:, up_idx]
    df["pred_gain_pct"] = models["reg_gain"].predict(X) if models.get("reg_gain") else float("nan")
    df["pred_dd_pct"]   = models["reg_dd"].predict(X)   if models.get("reg_dd")   else float("nan")
    df["pred_mfe_pct"]  = models["reg_mfe"].predict(X)  if models.get("reg_mfe")  else float("nan")
    df["pred_mag_pct"]  = models["reg_mag"].predict(X)  if models.get("reg_mag")  else float("nan")
    if models.get("ranker") is not None:
        df["rank_score"] = models["ranker"].predict(X)
        df["score"]      = df["rank_score"]
    else:
        df["rank_score"] = float("nan")
        df["score"]      = df["pred_gain_pct"].fillna(0) * df["up_prob"]
    return df



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
        if pct < MIN_GAINER_PCT:
            continue
        score = pct * np.log1p(vol)
        rows.append({"ticker": s.ticker, "pct": pct, "price": c, "volume": int(vol), "score": score})
    if not rows:
        return []
    rows.sort(key=lambda x: x["score"], reverse=True)
    candidates = [r["ticker"] for r in rows[:TOP_N_WATCH * 2]]
    day_pct = {r["ticker"]: r["pct"] for r in rows}
    df = build_features(client, candidates, today)
    if df.empty:
        return []
    df = _apply_scores(df, models)
    df = df[df["open5_pct"].fillna(0) >= MIN_OPEN5_PCT]
    if df.empty:
        return []
    df["day_pct"]    = df["ticker"].map(day_pct).fillna(0.0)
    df["base_score"] = df["score"]
    df["score"]      = df["base_score"] * np.power(1.0 + df["day_pct"].clip(lower=0) / 100.0,
                                                   GAINER_BIAS_POW)
    return df.sort_values("score", ascending=False).head(TOP_N_WATCH).to_dict("records")



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
    wins      = sum(1 for r in results if r.get("pnl_pct", 0) > 0)
    win_rate  = wins / len(results) * 100
    ibkr_placed_n = sum(1 for r in results if r.get("ibkr_placed"))
    print(f"  {'Ticker':<7} {'Reason':<6} {'BuyTime':>9} {'Buy':>8} {'ExitTime':>9} {'Exit':>8} {'PnL%':>7} {'IBKR':>14}")
    print(f"  {'-'*78}")
    lines = [f"📋 <b>Daily Report</b> — {date_str} [{mode}]\n"]
    for r in results:
        emoji = "🎯" if r["reason"] == "TP" else ("🛑" if r["reason"] == "SL" else "🔔")
        bt    = r.get("entry_time", "?")
        xt    = r.get("exit_time",  "?")
        if r.get("ibkr_placed"):
            ibkr_cell = f"✓ qty={int(r.get('ibkr_qty', 0) or 0)}"
            ibkr_tg   = f"IBKR ✓ qty={int(r.get('ibkr_qty', 0) or 0)}"
        else:
            raw = str(r.get("ibkr_status", "—"))
            ibkr_cell = "✗ " + (raw.split(" ")[0] if raw else "—")[:12]
            ibkr_tg   = f"IBKR ✗ {raw}"
        print(f"  {r['ticker']:<7} {r['reason']:<6} {bt:>9} {r['entry']:>8.2f}"
              f" {xt:>9} {r['exit']:>8.2f} {r['pnl_pct']:>+6.2f}% {ibkr_cell:>14}")
        lines.append(f"{emoji} {r['ticker']}  [{r['reason']}]  buy={bt}@{r['entry']:.2f}  "
                     f"exit={xt}@{r['exit']:.2f}  pnl={r['pnl_pct']:+.2f}%  {ibkr_tg}")
    print(f"  {'-'*78}")
    print(f"  Trades : {len(results)}  (TP={tp_count}  SL={sl_count}  EOD={eod_count})")
    print(f"  IBKR   : {ibkr_placed_n}/{len(results)} placed on broker")
    print(f"  Win %  : {win_rate:.0f}%")
    print(f"  Total  : {total_pnl:+.2f}%")
    print(f"{sep}\n")
    lines += [
        f"\nTrades: {len(results)}  (TP={tp_count}  SL={sl_count}  EOD={eod_count})",
        f"IBKR placed: {ibkr_placed_n}/{len(results)}",
        f"Win rate: {win_rate:.0f}%",
        f"<b>Total PnL: {total_pnl:+.2f}%</b>",
    ]
    _tg_send("\n".join(lines))



def run_all_day(client: MassiveClient, watchlist: list[dict],
                today: datetime,
                halal: set | None = None, models: dict | None = None) -> list[dict]:
    CLOSE_TIME = today.replace(hour=15, minute=55, second=0, microsecond=0)
    today_str  = today.strftime("%Y-%m-%d")
    print(f"\n  ── run_all_day start ──")
    print(f"  Now: {now_et().strftime('%H:%M:%S')} ET  |  Force-close: {CLOSE_TIME.strftime('%H:%M')} ET")
    print(f"  Stop new entries after: {STOP_ENTRY_HOUR:02d}:{STOP_ENTRY_MINUTE:02d} ET")
    print(f"  Watchlist: {len(watchlist)} ranked picks")

    saved    = load_state(today_str)
    open_pos : dict[str, dict] = dict(saved)
    traded   : set[str]        = set(saved.keys())
    results  : list[dict]      = []
    pos_lock : threading.Lock  = threading.Lock()
    if saved:
        print(f"  Resumed {len(saved)} existing positions: {list(saved.keys())}")

    def _try_enter(row: dict) -> bool:
        ticker = row["ticker"]
        up_prob = float(row.get("up_prob", 0.0) or 0.0)
        pg_val  = row.get("pred_gain_pct")
        pred_gain = float(pg_val) if pg_val is not None and not (isinstance(pg_val, float) and np.isnan(pg_val)) else float("nan")
        gate = float((models or {}).get("up_prob_gate", MIN_UP_PROB))
        if up_prob < gate or np.isnan(pred_gain) or pred_gain < MIN_PRED_GAIN:
            print(f"  {ticker}: gate fail (up={up_prob:.3f} < {gate:.3f}, "
                  f"pred_gain={pred_gain:+.2f}%) — skip")
            return False

        agent = agent_decide(ticker, row, row.get("headline", "") or "")
        for v in agent.get("votes", []):
            mark = "✓" if v["action"] == "TRADE" else "✗"
            print(f"    [{v['agent']:<6}] {mark} {v['reason']}")
        print(f"  {ticker}: committee → {agent['action']}")
        if agent["action"] == "SKIP":
            return False

        with pos_lock:
            if ticker in traded or ticker in open_pos:
                return False
            traded.add(ticker)
        print(f"\n  Confirming {ticker} …")
        confirmed, entry_price, entry_time = confirm_entry(client, ticker, today)
        if not confirmed or entry_price == 0:
            print(f"  {ticker}: no confirmation — skip")
            return False

        fill_px = entry_price * (1.0 + SLIPPAGE_BPS / 10_000.0)
        qty     = int(CAPITAL_PER_TRADE // fill_px) if fill_px > 0 else 0
        if qty < 1:
            print(f"  {ticker}: ${CAPITAL_PER_TRADE:,.0f} cannot cover 1 share "
                  f"@ {fill_px:.2f} (qty={qty}) — skip")
            return False
        print(f"  {ticker}: ${CAPITAL_PER_TRADE:,.0f} covers {qty} share(s) "
              f"@ {fill_px:.2f} (notional ${qty * fill_px:,.2f})")

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
            ibkr_qty, ibkr_fill_px = ibkr.place_entry(ticker, req_qty, entry_price)
            if ibkr_qty <= 0:
                ibkr_status = "not filled"
                return False
            pos["ibkr_qty"] = ibkr_qty
            if ibkr_fill_px > 0:
                pos["entry"]  = ibkr_fill_px
                pos["tp"]     = round(ibkr_fill_px * (1 + pos["tp_pct"] / 100), 2)
                pos["sl"]     = round(ibkr_fill_px * (1 + pos["sl_pct"] / 100), 2)
                entry_price   = ibkr_fill_px
            ibkr_status = f"filled qty={ibkr_qty} @ {ibkr_fill_px:.2f}  cash=${ibkr_cash:.2f}"

        pos["ibkr_status"] = ibkr_status
        pos["ibkr_placed"] = bool(IBKR_ENABLED and int(pos.get("ibkr_qty", 0) or 0) > 0)
        pos["agent_reason"] = agent.get("reason", "")

        with pos_lock:
            open_pos[ticker] = pos
            save_state(open_pos, today_str)

        g  = f"{pos['pred_gain']:+.1f}%" if not np.isnan(pos["pred_gain"]) else "n/a"
        dd = f"{pos['pred_dd']:+.1f}%"   if not np.isnan(pos["pred_dd"])   else "n/a"
        tg(
            f"✅ <b>[ENTRY] {ticker}</b>\n"
            f"  Buy   : {entry_price:.2f} @ {entry_time} ET\n"
            f"  TP    : {pos['tp']:.2f} ({pos['tp_pct']:+.1f}%)\n"
            f"  SL    : {pos['sl']:.2f} ({pos['sl_pct']:+.1f}%)\n"
            f"  Model : gain={g}  dd={dd}  up={pos['up_prob']*100:.0f}%\n"
            f"  IBKR  : {ibkr_status}"
        )
        return True

    STOP_ENTRY_TIME = today.replace(hour=STOP_ENTRY_HOUR, minute=STOP_ENTRY_MINUTE,
                                    second=0, microsecond=0)

    def _can_open_new() -> bool:
        return now_et() < STOP_ENTRY_TIME

    def _enter_from(rows: list[dict]) -> int:
        """Try to open positions from a ranked pick list. Returns count opened."""
        opened = 0
        for row in rows:
            if len(open_pos) >= MAX_POSITIONS:
                break
            if not _can_open_new():
                print(f"  Past entry cutoff ({STOP_ENTRY_TIME.strftime('%H:%M')} ET) — stopping entries.")
                break
            if _try_enter(row):
                opened += 1
        return opened

    print("\n  Entering initial positions …")
    _enter_from(watchlist)

    while not open_pos and _can_open_new():
        print(f"\n  No positions opened — refreshing picked list "
              f"in {PICK_REFRESH_DELAY_S}s …")
        time.sleep(PICK_REFRESH_DELAY_S)
        if not _can_open_new():
            break
        fresh = _live_watchlist(client, halal, traded, models, today) if (halal and models) else []
        if not fresh:
            fresh = [r for r in watchlist if r["ticker"] not in traded]
        if not fresh:
            print("  No fresh candidates available this round.")
            continue
        print(f"  Refreshed picked list: {len(fresh)} candidate(s) — retrying entries.")
        watchlist = fresh
        _enter_from(fresh)

    watch_tickers = {r["ticker"] for r in watchlist} | set(open_pos.keys())
    print(f"  Monitoring {len(open_pos)} position(s) via WebSocket "
          f"(subscribed to {len(watch_tickers)} tickers)\n")

    trade_q:        queue.Queue     = queue.Queue()
    reentry_active: threading.Event = threading.Event()
    reentry_thread: list[threading.Thread] = []
    ws_alive:       threading.Event = threading.Event()

    def _ws_handler(msgs):
        ws_alive.set()
        for m in msgs:
            sym = getattr(m, "symbol", None)
            px  = getattr(m, "price",  None)
            if sym and px is not None and sym in open_pos:
                trade_q.put((sym, float(px)))

    api_key = os.getenv("MASSIVE_API_KEY", "").strip()

    def _ws_runner():
        while True:
            try:
                ws = WebSocketClient(api_key=api_key,
                                     subscriptions=[f"T.{t}" for t in watch_tickers])
                ws.run(handle_msg=_ws_handler)
            except Exception as e:
                print(f"  WS error: {e} — reconnecting in {WS_RECONNECT_DELAY_S}s")
            else:
                print(f"  WS disconnected — reconnecting in {WS_RECONNECT_DELAY_S}s")
            ws_alive.clear()
            if now_et() >= CLOSE_TIME:
                return
            time.sleep(WS_RECONNECT_DELAY_S)

    threading.Thread(target=_ws_runner, daemon=True).start()
    print(f"  WS subscribed to {len(watch_tickers)} tickers")

    def _do_reentry():
        try:
            slots = MAX_POSITIONS - len(open_pos)
            if slots <= 0:
                return
            print(f"\n  Slot freed — fetching live snapshot …")
            fresh = _live_watchlist(client, halal, traded, models, today) if halal and models else []
            if not fresh:
                fresh = [r for r in watchlist if r["ticker"] not in traded]
            print(f"  {len(fresh)} fresh candidates — trying top {slots} …")
            deadline = time.time() + REENTRY_TIME_BUDGET_S
            filled = 0
            for row in fresh:
                if filled >= slots or time.time() > deadline:
                    break
                if _try_enter(row):
                    filled += 1
            if filled == 0:
                print("  No new entries confirmed.")
        finally:
            reentry_active.clear()

    def _resolve_exit(sym: str, price: float) -> tuple[str, float] | None:
        """Returns (reason, exit_price) if exit conditions met, else None. Caller holds pos_lock."""
        if sym not in open_pos:
            return None
        p = open_pos[sym]
        if (time.time() - p.get("entry_ts", time.time())) >= HOLD_MINUTES * 60:
            return ("HOLD", price)
        if price >= p["tp"]:
            return ("TP", p["tp"])
        if price <= p["sl"]:
            return ("SL", p["sl"])
        return None

    last_status   = time.time()
    last_backstop = time.time()
    print(f"\n  ── entering main monitor loop @ {now_et().strftime('%H:%M:%S')} ET ──")
    print(f"  Heartbeat every 60 s; REST backstop every {REST_BACKSTOP_S} s; loop tick every 1 s.\n")

    while True:
        try:
            sym, price = trade_q.get(timeout=1.0)
            tick_event = True
        except queue.Empty:
            tick_event = False
            sym, price = "", 0.0

        if not tick_event:
            if now_et() >= CLOSE_TIME:
                break
            if open_pos and (time.time() - last_backstop) >= REST_BACKSTOP_S:
                last_backstop = time.time()
                for tk in list(open_pos.keys()):
                    try:
                        px, _ = client.price(tk)
                    except Exception:
                        continue
                    if px and px > 0:
                        trade_q.put((tk, float(px)))
                if not ws_alive.is_set():
                    print("  WS heartbeat missed — relying on REST backstop.")
                ws_alive.clear()
            if time.time() - last_status >= 60 and open_pos:
                et_now = now_et()
                print(f"\n  ── {et_now.strftime('%H:%M:%S')} ET (heartbeat) ─────────────────────")
                print(f"  {'Ticker':<8} {'BuyTime':>9} {'Entry':>8} {'TP':>8} {'SL':>8}  Holding")
                for tk, p in open_pos.items():
                    bt = p.get("entry_time", "?")
                    print(f"  {tk:<8} {bt:>9} {p['entry']:>8.2f} {p['tp']:>8.2f} {p['sl']:>8.2f}  …")
                last_status = time.time()
            continue

        with pos_lock:
            outcome = _resolve_exit(sym, price)
            if outcome is None:
                if sym in open_pos:
                    p   = open_pos[sym]
                    pnl = (price - p["entry"]) / p["entry"] * 100
                    to_tp = (p["tp"] - price) / price * 100
                    to_sl = (price - p["sl"]) / price * 100
                    print(f"  {sym:<8} px={price:.2f}  pnl={pnl:>+.2f}%  →TP={to_tp:>+.2f}%  →SL={to_sl:>+.2f}%")
                continue
            reason, exit_price = outcome
            result = _log_exit(open_pos[sym], exit_price, reason)
            results.append(result)
            del open_pos[sym]
            save_state(open_pos, today_str)

        if not reentry_active.is_set() and _can_open_new():
            reentry_active.set()
            t = threading.Thread(target=_do_reentry, daemon=True)
            reentry_thread.append(t)
            t.start()

        last_status = time.time()
        if now_et() >= CLOSE_TIME:
            break

    for t in reentry_thread:
        if t.is_alive():
            t.join(timeout=5.0)

    if open_pos:
        print(f"\n  15:55 ET — force-closing {len(open_pos)} remaining position(s).")
        with pos_lock:
            for ticker, p in list(open_pos.items()):
                try:
                    price, _ = client.price(ticker)
                    price = price or p["entry"]
                except Exception:
                    price = p["entry"]
                results.append(_log_exit(p, price, "EOD"))
                del open_pos[ticker]

    try:
        _send_daily_report(results, today_str, "LIVE")
    except Exception as e:
        print(f"  [daily report] send failed: {e}")
        try:
            _tg_send(f"⚠️ Daily report send failed: {e}\nTrades today: {len(results)}")
        except Exception:
            pass
    clear_state()
    return results



# Full-day NYSE market holidays (observed dates). Half-days (early closes) are
# intentionally omitted — the market IS open then, so the live engine can run.
NYSE_HOLIDAYS = {
    # 2025
    "2025-01-01": "New Year's Day",      "2025-01-20": "MLK Jr. Day",
    "2025-02-17": "Presidents' Day",     "2025-04-18": "Good Friday",
    "2025-05-26": "Memorial Day",        "2025-06-19": "Juneteenth",
    "2025-07-04": "Independence Day",    "2025-09-01": "Labor Day",
    "2025-11-27": "Thanksgiving",        "2025-12-25": "Christmas Day",
    # 2026
    "2026-01-01": "New Year's Day",      "2026-01-19": "MLK Jr. Day",
    "2026-02-16": "Presidents' Day",     "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",        "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (observed)", "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving",        "2026-12-25": "Christmas Day",
}


def market_closed_reason(today_et: datetime) -> str | None:
    """Return a human reason if the US market is closed today, else None.

    Uses pandas_market_calendars (NYSE) when installed for exact, future-proof
    coverage; otherwise falls back to a weekend check + the hardcoded
    NYSE_HOLIDAYS table (2025–2026). The live DECISION engine needs today's
    intraday bars, which don't exist on a closed day, so we short-circuit with a
    clear message instead of a 'no bars' dump.
    """
    if today_et.weekday() >= 5:
        return f"{today_et.strftime('%A')} — weekend, market closed"
    date_str = today_et.strftime("%Y-%m-%d")
    try:
        import pandas_market_calendars as mcal
        sched = mcal.get_calendar("NYSE").schedule(start_date=date_str, end_date=date_str)
        if sched.empty:
            return "NYSE holiday — market closed"
        return None
    except ImportError:
        pass
    if date_str in NYSE_HOLIDAYS:
        return f"{NYSE_HOLIDAYS[date_str]} — market holiday"
    return None


def wait_for_market_open(today_et: datetime, extra_minutes: int = 5):
    """Block until OPEN_HOUR_ET:OPEN_MINUTE_ET + extra_minutes on today_et's date.
    Extra minutes ensures the first-5-minute open features have data."""
    target = today_et.replace(
        hour=OPEN_HOUR_ET, minute=OPEN_MINUTE_ET, second=0, microsecond=0
    ) + timedelta(minutes=extra_minutes)
    now = now_et()
    if now >= target:
        return
    wait_s = (target - now).total_seconds()
    print(f"  Market open wait — sleeping until {target.strftime('%H:%M:%S')} ET "
          f"({wait_s/60:.1f} min)…")
    while True:
        remaining = (target - now_et()).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 30))
    print(f"  Market open reached at {now_et().strftime('%H:%M:%S')} ET — proceeding.")


def _step(n: int, total: int, msg: str):
    print(f"\n{'─'*60}\n  ▶ STEP {n}/{total}  {msg}\n{'─'*60}")


def run_live(today_et: datetime, models: dict, ranked: pd.DataFrame | None = None):
    del ranked
    print(f"\n{'='*60}")
    print(f"  MAMDOUH — DECISION (live execution)")
    print(f"  Start: {now_et().strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"{'='*60}")

    TOTAL_STEPS = 8

    closed = market_closed_reason(today_et)
    if closed:
        print(f"\n  ⛔ Market CLOSED today: {closed}.")
        print(f"     The live engine needs today's intraday bars, which don't exist "
              f"when the market is closed.")
        print(f"     → Run on a trading day (Mon–Fri after 9:35 ET), or use "
              f"`backtest` mode to test on historical data.")
        return

    _step(1, TOTAL_STEPS, "Wait for market open (≥ 9:35 ET)")
    wait_for_market_open(today_et)

    _step(2, TOTAL_STEPS, "Initialize REST client + load halal universe")
    client    = MassiveClient()
    halal     = load_halal()
    today_str = today_et.strftime("%Y-%m-%d")
    print(f"  Halal universe: {len(halal)} tickers")
    print(f"  Models loaded: {list(models.keys())}")
    print(f"  Agent committee: {'ENABLED' if AGENT_ENABLED else 'disabled'}  "
          f"(model={ANTHROPIC_MODEL}, unanimous={AGENT_REQUIRE_ALL})")

    _step(3, TOTAL_STEPS, "Check for resumable positions from saved state")
    saved = load_state(today_str)
    if saved:
        print(f"  Resuming {len(saved)} open position(s) — skipping scan.")
        for t, p in saved.items():
            print(f"    {t:<8}  entry={p['entry']:.2f}  TP={p['tp']:.2f}  SL={p['sl']:.2f}")
        print("\n  Jumping to monitoring loop (skipping steps 4–7).")
        results = run_all_day(client, [], today_et, halal=halal, models=models)
        save_log(results)
        return
    print("  No saved state — running full pipeline.")

    _step(4, TOTAL_STEPS, "Fetch full-market snapshot and rank by daily move")
    try:
        snaps = client.snapshot_all()
        print(f"  Snapshot returned {len(snaps)} tickers; filtering …")
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
            rows.append({"ticker": s.ticker, "pct": round(pct, 2), "price": round(c, 2),
                         "volume": int(vol), "score": score})
        rows.sort(key=lambda x: x["score"], reverse=True)
        candidates = [r["ticker"] for r in rows[:TOP_N_WATCH * 2]]
        print(f"  {len(rows)} halal tickers passed filters → top {len(candidates)} candidates")
        if rows[:5]:
            print(f"  Preview: " + ", ".join(f"{r['ticker']}({r['pct']:+.1f}%)" for r in rows[:5]))
    except Exception as e:
        print(f"  Snapshot failed ({e}), falling back to halal list.")
        candidates = list(halal)[:40]

    if not candidates:
        print("\nNo candidates found. Exiting.")
        return

    _step(5, TOTAL_STEPS, f"Build live features for {len(candidates)} candidates")
    df = build_features(client, candidates, today_et)
    if df.empty:
        print("  No intraday bars returned for any candidate.")
        print("  Likely the market is closed/half-day or it's too early (before "
              "~9:35 ET), so today's bars don't exist yet. Try again during RTH, "
              "or use `backtest` mode. Exiting.")
        return
    print(f"  Features built for {len(df)} tickers")

    _step(6, TOTAL_STEPS, "Score candidates with trained models")
    df = _apply_scores(df, models)
    ranked_live = df.sort_values("score", ascending=False).head(TOP_N_WATCH)
    print(f"  Top {len(ranked_live)} ranked candidates:")
    print(f"  {'#':<3} {'Ticker':<7} {'Score':>7} {'UpProb':>7} {'PredGain%':>10} "
          f"{'PredMFE%':>10} {'PredDD%':>9}")
    for i, (_, r) in enumerate(ranked_live.iterrows(), 1):
        print(f"  {i:<3} {r['ticker']:<7} {r['score']:>7.3f} {r['up_prob']*100:>6.1f}% "
              f"{r['pred_gain_pct']:>+9.2f}%  {r.get('pred_mfe_pct', 0):>+9.2f}%  "
              f"{r['pred_dd_pct']:>+8.2f}%")
        if i >= 10:
            break

    _step(7, TOTAL_STEPS, "Hand off to run_all_day (entry + monitoring)")
    try:
        results = run_all_day(client, ranked_live.to_dict("records"), today_et,
                              halal=halal, models=models)
    except Exception as e:
        print(f"  run_all_day raised: {e!r} — sending crash report.")
        _tg_send(f"❌ <b>Trading session crashed</b>\n{type(e).__name__}: {e}")
        raise

    _step(8, TOTAL_STEPS, "Save log + done")
    save_log(results)
    print(f"\n  Session ended at {now_et().strftime('%H:%M:%S')} ET — {len(results)} trades.")



def _train_models_on(train_df: pd.DataFrame) -> dict:
    """Train classifier + regressors + ranker on the given slice. Mirrors
    `_load_or_train`'s training section but skips disk caching, so backtests
    don't overwrite the live `models.pkl`.
    """
    if train_df.empty or len(train_df) < 200:
        raise RuntimeError(f"Not enough training rows for backtest ({len(train_df)}).")
    assert_no_leakage()
    df = _ensure_features(_prepare_targets(train_df))
    models = {
        "clf":      _train_classifier(df),
        "reg_gain": _train_regressor(df, "target_gain", "gain%"),
        "reg_dd":   _train_regressor(df, "target_dd",   "drawdown% (MAE all-day)"),
        "reg_mfe":  _train_regressor(df, "target_mfe",  "max-favorable% (MFE all-day)"),
        "reg_mag":  _train_regressor(df, "target_mag",  "move-magnitude% (sizing)"),
        "ranker":   _train_ranker(df),
    }
    models["up_prob_gate"] = _derive_up_prob_gate(models["clf"], df)
    return models


def run_backtest(date_arg: str | None, today_et: datetime,
                 split: str | None = None, max_positions: int | None = None,
                 cost_bps: float = 8.0):
    """Expanding walk-forward backtest (no data leakage).

    Starts at `split` (last 20% of dates by default) and, for each test date,
    retrains on ALL data strictly before it (every BT_RETRAIN_EVERY days) so no
    future information ever reaches the model. Each day it scores all tickers,
    gates by the auto-derived up_prob gate + MIN_PRED_GAIN, takes the top
    `max_positions` by ranker `score`, SIZES them by predicted move-magnitude
    (pred_mag), and realizes `target_gain` (post-open %) minus a realistic
    round-trip cost (`cost_bps`/side + volume-surge slippage). Reports gross vs
    NET, win-rate, precision@N, Sharpe and max-drawdown — net is the verdict.
    """
    session_log = start_session_log(today_et, "BACKTEST")
    print(f"\n{'='*60}")
    print(f"  MAMDOUH BACKTEST  —  {today_et.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            dates = [r[0] for r in con.execute(
                "SELECT DISTINCT date FROM features ORDER BY date"
            ).fetchall()]
            if not dates:
                raise RuntimeError("features table is empty. Run data pipeline first.")
            all_df = con.execute(
                "SELECT * FROM features WHERE label IS NOT NULL AND post_open_close IS NOT NULL"
            ).fetchdf()
        finally:
            con.close()

        if all_df.empty:
            raise RuntimeError("No labeled feature rows available for backtest.")
        all_df = _prepare_targets(all_df)

        if split is None:
            split = dates[max(1, int(len(dates) * 0.8))]
        train_df = all_df[all_df["date"] <  split].copy()
        test_df  = all_df[all_df["date"] >= split].copy()
        if date_arg:
            test_df = test_df[test_df["date"] <= date_arg]

        test_dates = sorted(test_df["date"].unique())
        if not test_dates:
            raise RuntimeError(f"No test dates after split={split}.")
        print(f"  Initial train: {train_df['date'].nunique()} dates ({len(train_df):,} rows), grows each retrain")
        print(f"  Test : {len(test_dates)} dates from {test_dates[0]} → {test_dates[-1]}")

        max_pos = max_positions or MAX_POSITIONS
        print(f"  Walk-forward : expanding window, retrain every {BT_RETRAIN_EVERY} test days")
        print(f"  Costs        : {cost_bps:.1f} bps/side + up to {BT_SLIP_CAP_BPS:.0f} bps surge-slippage; "
              f"sizing×[{BT_SIZE_LO},{BT_SIZE_HI}] on pred_mag")

        def _cost_pct(surge: float) -> float:
            slip = min(BT_SLIP_BPS_PER_SURGE * max(surge, 0.0), BT_SLIP_CAP_BPS)
            return (cost_bps + slip) / 100.0 * 2.0

        trades: list[dict] = []
        per_day: list[dict] = []
        models, last_train = None, -10**9
        equity = 1.0
        wins = losses = 0

        for k, d in enumerate(test_dates):
            if models is None or (k - last_train) >= BT_RETRAIN_EVERY:
                tr = all_df[all_df["date"] < d]
                try:
                    models = _train_models_on(tr)
                except RuntimeError as e:
                    print(f"  [skip {d}] {e}")
                    continue
                last_train = k

            day = _apply_scores(all_df[all_df["date"] == d].copy(), models)
            if day.empty:
                continue
            gate = float(models.get("up_prob_gate", MIN_UP_PROB))
            gated = day[
                (day["up_prob"] >= gate)
                & (day["pred_gain_pct"].notna())
                & (day["pred_gain_pct"] >= MIN_PRED_GAIN)
            ].sort_values("score", ascending=False).head(max_pos)
            if gated.empty:
                per_day.append({"date": d, "n_trades": 0, "gross_pct": 0.0,
                                "net_pct": 0.0, "equity": equity, "precision": 0.0})
                continue

            mag = gated["pred_mag_pct"].fillna(0.0).to_numpy()
            w = np.clip(mag / (mag.mean() + 1e-9), BT_SIZE_LO, BT_SIZE_HI) if mag.mean() > 0 \
                else np.ones(len(gated))
            w = w / w.mean()

            actual_top = set(day.nlargest(max_pos, "target_gain")["ticker"]) \
                if day["target_gain"].notna().any() else set()
            g_sum = n_sum = wsum = 0.0
            for wi, (_, r) in zip(w, gated.iterrows()):
                gross = float(r["target_gain"]) if pd.notna(r.get("target_gain")) else 0.0
                net   = gross - _cost_pct(float(r.get("pm_vol_surge", 0.0) or 0.0))
                trades.append({
                    "date": d, "ticker": r["ticker"], "weight": round(float(wi), 3),
                    "up_prob": float(r["up_prob"]), "pred_gain_pct": float(r["pred_gain_pct"]),
                    "pred_mag_pct": float(r.get("pred_mag_pct", 0.0) or 0.0),
                    "gross_pct": gross, "net_pct": net,
                })
                g_sum += wi * gross; n_sum += wi * net; wsum += wi
                if net > 0: wins += 1
                else:       losses += 1

            day_gross = g_sum / wsum if wsum else 0.0
            day_net   = n_sum / wsum if wsum else 0.0
            equity   *= (1.0 + day_net / 100.0)
            prec = (len(set(gated["ticker"]) & actual_top) / max_pos) if actual_top else float("nan")
            per_day.append({"date": d, "n_trades": len(gated), "gross_pct": day_gross,
                            "net_pct": day_net, "equity": equity, "precision": prec})

        n_t = len(trades)
        pdf = pd.DataFrame(per_day)
        traded = pdf[pdf["n_trades"] > 0] if not pdf.empty else pdf
        net_series = traded["net_pct"].to_numpy() / 100.0 if not traded.empty else np.array([0.0])
        eq_curve = (1 + pd.Series(net_series)).cumprod()
        max_dd = float(((eq_curve - eq_curve.cummax()) / eq_curve.cummax()).min() * 100) \
            if len(eq_curve) else 0.0
        sharpe = float(np.mean(net_series) / (np.std(net_series) + 1e-9) * np.sqrt(252))
        win_rate   = (wins / n_t) if n_t else 0.0
        avg_net    = float(traded["net_pct"].mean()) if not traded.empty else 0.0
        avg_gross  = float(traded["gross_pct"].mean()) if not traded.empty else 0.0
        precision  = float(traded["precision"].mean(skipna=True)) if not traded.empty else float("nan")
        win_days   = float((traded["net_pct"] > 0).mean() * 100) if not traded.empty else 0.0

        print(f"\n{'='*60}")
        print(f"  BACKTEST RESULTS  (expanding walk-forward, net of costs)")
        print(f"{'='*60}")
        print(f"  Trades         : {n_t}  ({wins}W / {losses}L)   over {len(traded)} traded days")
        print(f"  Win rate       : {win_rate*100:.1f}%   |  winning days: {win_days:.1f}%")
        print(f"  Precision@{max_pos}    : {precision:.3f}  (picks that were in the day's actual top {max_pos})")
        print(f"  Avg/day GROSS  : {avg_gross:+.3f}%")
        print(f"  Avg/day NET    : {avg_net:+.3f}%   <-- the real number")
        print(f"  Equity (net ×) : {equity:.4f}  ({(equity-1)*100:+.2f}%)")
        print(f"  Sharpe (ann.)  : {sharpe:.2f}   |  Max drawdown: {max_dd:.1f}%")
        print(f"  NOTE: if NET ≈ 0 or negative, the gross edge is eaten by costs — "
              f"reported honestly, not hidden.")

        out_dir = BASE_DIR / "result v2"
        out_dir.mkdir(exist_ok=True)
        ts = today_et.strftime("%Y%m%d_%H%M%S")
        if trades:
            pd.DataFrame(trades).to_csv(out_dir / f"backtest_trades_{ts}.csv", index=False)
        if per_day:
            pdf.to_csv(out_dir / f"backtest_daily_{ts}.csv", index=False)
        print(f"  Saved        : result v2/backtest_trades_{ts}.csv")

        return {"trades": trades, "per_day": per_day, "n_trades": n_t,
                "win_rate": win_rate, "avg_pct": avg_net, "avg_gross_pct": avg_gross,
                "precision": precision, "sharpe": sharpe, "max_dd": max_dd,
                "equity": equity}
    finally:
        session_log.close()



def _model_zoo():
    """Candidate (classifier, regressor) factories for the model search. Only
    families whose libraries are installed are included. `scale=True` families
    are fed StandardScaler-normalized inputs."""
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.ensemble import (RandomForestRegressor, ExtraTreesClassifier,
                                  ExtraTreesRegressor, HistGradientBoostingClassifier,
                                  HistGradientBoostingRegressor,
                                  GradientBoostingClassifier, GradientBoostingRegressor)
    zoo = {
        "LogReg/Ridge":   (lambda: LogisticRegression(max_iter=1000, class_weight="balanced"),
                           lambda: Ridge(alpha=1.0), True),
        "RandomForest":   (lambda: RandomForestClassifier(n_estimators=300, max_depth=7,
                              min_samples_leaf=8, class_weight="balanced", n_jobs=-1, random_state=42),
                           lambda: RandomForestRegressor(n_estimators=300, max_depth=8,
                              min_samples_leaf=10, n_jobs=-1, random_state=42), False),
        "ExtraTrees":     (lambda: ExtraTreesClassifier(n_estimators=300, max_depth=10,
                              min_samples_leaf=10, class_weight="balanced", n_jobs=-1, random_state=42),
                           lambda: ExtraTreesRegressor(n_estimators=300, max_depth=10,
                              min_samples_leaf=10, n_jobs=-1, random_state=42), False),
        "HistGBM":        (lambda: HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05,
                              max_iter=300, random_state=42),
                           lambda: HistGradientBoostingRegressor(max_depth=4, learning_rate=0.05,
                              max_iter=300, random_state=42), False),
        "sklearn-GBoost": (lambda: GradientBoostingClassifier(n_estimators=200, max_depth=3,
                              learning_rate=0.05, subsample=0.85, random_state=42),
                           lambda: GradientBoostingRegressor(n_estimators=200, max_depth=3,
                              learning_rate=0.05, subsample=0.85, random_state=42), False),
    }
    try:
        from xgboost import XGBClassifier, XGBRegressor
        zoo["XGBoost"] = (lambda: XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05,
                              subsample=0.85, colsample_bytree=0.85, tree_method="hist",
                              n_jobs=-1, random_state=42, eval_metric="logloss", verbosity=0),
                          lambda: XGBRegressor(n_estimators=300, max_depth=5, learning_rate=0.05,
                              subsample=0.85, colsample_bytree=0.85, tree_method="hist",
                              n_jobs=-1, random_state=42, verbosity=0), False)
    except ImportError:
        pass
    if _HAS_LGB:
        zoo["LightGBM"] = (lambda: lgb.LGBMClassifier(n_estimators=400, learning_rate=0.04,
                              num_leaves=31, subsample=0.85, colsample_bytree=0.85,
                              class_weight="balanced", n_jobs=-1, random_state=42, verbose=-1),
                           lambda: lgb.LGBMRegressor(n_estimators=400, learning_rate=0.04,
                              num_leaves=31, subsample=0.85, colsample_bytree=0.85,
                              n_jobs=-1, random_state=42, verbose=-1), False)
    if _HAS_CAT:
        zoo["CatBoost"] = (lambda: CatBoostClassifier(iterations=400, depth=6, learning_rate=0.04,
                              auto_class_weights="Balanced", random_seed=42, verbose=0,
                              allow_writing_files=False),
                           lambda: CatBoostRegressor(iterations=400, depth=6, learning_rate=0.04,
                              random_seed=42, verbose=0, allow_writing_files=False), False)
    return zoo


def run_model_search(date_arg: str | None, today_et: datetime,
                     split: str | None = None, cost_bps: float = 8.0):
    """Benchmark several ML learners under the SAME leakage-free, cost-aware
    backtest and rank them. Each family trains a direction classifier + a gain
    regressor + a magnitude regressor on PRE-MARKET features (train dates only),
    then on each test day gates by up_prob, picks the top MAX_POSITIONS by
    predicted gain, sizes by predicted magnitude, and realizes target_gain minus
    realistic costs. Ranked by NET avg return/day (tie-break Sharpe).
    """
    from sklearn.preprocessing import StandardScaler
    session_log = start_session_log(today_et, "SEARCH")
    print(f"\n{'='*64}\n  MAMDOUH — MODEL SEARCH (leakage-free, cost-aware)\n{'='*64}")
    try:
        assert_no_leakage()
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            dates = [r[0] for r in con.execute(
                "SELECT DISTINCT date FROM features ORDER BY date").fetchall()]
            all_df = con.execute(
                "SELECT * FROM features WHERE label IS NOT NULL AND post_open_close IS NOT NULL"
            ).fetchdf()
        finally:
            con.close()
        if all_df.empty:
            raise RuntimeError("No labeled feature rows for model search.")
        all_df = _ensure_features(_prepare_targets(all_df))

        if split is None:
            split = dates[max(1, int(len(dates) * 0.8))]
        tr = all_df[all_df["date"] < split].copy()
        te = all_df[all_df["date"] >= split].copy()
        if date_arg:
            te = te[te["date"] <= date_arg]
        if tr.empty or te.empty:
            raise RuntimeError(f"Bad split={split}: train={len(tr)} test={len(te)}.")
        print(f"  Train < {split}: {tr['date'].nunique()} dates ({len(tr):,} rows)  |  "
              f"Test: {te['date'].nunique()} dates ({len(te):,} rows)")

        max_pos = MAX_POSITIONS
        gate_q  = GATE_PERCENTILE / 100.0
        def _cost(surge):
            slip = min(BT_SLIP_BPS_PER_SURGE * max(surge, 0.0), BT_SLIP_CAP_BPS)
            return (cost_bps + slip) / 100.0 * 2.0

        Xtr_raw = tr[FEATURES].fillna(0.0).to_numpy()
        Xte_raw = te[FEATURES].fillna(0.0).to_numpy()
        scaler  = StandardScaler().fit(Xtr_raw)
        Xtr_s, Xte_s = scaler.transform(Xtr_raw), scaler.transform(Xte_raw)
        ytr_dir  = tr["label"].to_numpy()
        ytr_gain = tr["target_gain"].fillna(0.0).to_numpy()
        ytr_mag  = tr["target_mag"].fillna(0.0).to_numpy()

        rows = []
        for name, (mk_clf, mk_reg, scale) in _model_zoo().items():
            print(f"  · training {name} …")
            try:
                A, B = (Xtr_s, Xte_s) if scale else (Xtr_raw, Xte_raw)
                clf = mk_clf(); clf.fit(A, ytr_dir)
                rg  = mk_reg(); rg.fit(A, ytr_gain)
                rm  = mk_reg(); rm.fit(A, ytr_mag)
                up_idx = int(np.where(clf.classes_ == 1)[0][0]) if 1 in getattr(clf, "classes_", [1]) else -1
                pe = te[["date", "ticker", "target_gain", "pm_vol_surge"]].copy()
                pe["up_prob"]  = clf.predict_proba(B)[:, up_idx]
                pe["pg"]       = rg.predict(B)
                pe["pm"]       = np.maximum(rm.predict(B), 0.0)

                net_days, hit_days, prec_days = [], [], []
                for d, g in pe.groupby("date"):
                    thr = g["up_prob"].quantile(gate_q)
                    q = g[g["up_prob"] >= thr]
                    sel = (q if len(q) else g).nlargest(max_pos, "pg")
                    if sel.empty:
                        continue
                    w = np.clip(sel["pm"] / (sel["pm"].mean() + 1e-9),
                                BT_SIZE_LO, BT_SIZE_HI).to_numpy()
                    w = w / w.mean()
                    gross = sel["target_gain"].fillna(0.0).to_numpy()
                    net   = gross - sel["pm_vol_surge"].fillna(0.0).map(_cost).to_numpy()
                    net_days.append(float(np.average(net, weights=w)))
                    hit_days.append(float((gross > 0).mean()))
                    actual_top = set(g.nlargest(max_pos, "target_gain")["ticker"])
                    prec_days.append(len(set(sel["ticker"]) & actual_top) / max_pos)

                r = np.array(net_days) / 100.0
                eq = (1 + pd.Series(r)).cumprod()
                rows.append({
                    "model": name,
                    "net_avg_%/d": float(np.mean(net_days)) if net_days else 0.0,
                    "sharpe":      float(np.mean(r)/(np.std(r)+1e-9)*np.sqrt(252)) if len(r) else 0.0,
                    "net_total_%": float((eq.iloc[-1]-1)*100) if len(eq) else 0.0,
                    "hit":         float(np.mean(hit_days)) if hit_days else 0.0,
                    "prec@N":      float(np.mean(prec_days)) if prec_days else 0.0,
                    "days":        len(net_days),
                })
            except Exception as e:
                print(f"    ! {name} failed: {type(e).__name__}: {e}")

        if not rows:
            raise RuntimeError("No model produced results.")
        lb = pd.DataFrame(rows).sort_values(["net_avg_%/d", "sharpe"], ascending=False).reset_index(drop=True)
        print(f"\n{'='*64}\n  LEADERBOARD — ranked by NET avg return/day (after costs)\n{'='*64}")
        print(lb.to_string(index=False,
              formatters={"net_avg_%/d": "{:+.3f}".format, "sharpe": "{:.2f}".format,
                          "net_total_%": "{:+.1f}".format, "hit": "{:.3f}".format,
                          "prec@N": "{:.3f}".format}))
        best = lb.iloc[0]
        print(f"\n  WINNER: {best['model']}  "
              f"(net {best['net_avg_%/d']:+.3f}%/day, Sharpe {best['sharpe']:.2f}, "
              f"prec@{max_pos} {best['prec@N']:.3f})")
        print(f"  NOTE: all families gated/sized/charged identically — this is the model effect only.")
        out = BASE_DIR / "result v2" / f"model_search_{today_et.strftime('%Y%m%d_%H%M%S')}.csv"
        out.parent.mkdir(exist_ok=True); lb.to_csv(out, index=False)
        print(f"  Saved: {out.name}")
        return lb
    finally:
        session_log.close()


# =====================================================================
#  ONE-TRADE-PER-DAY COMPARE LAB  (`--compare`)
#
#  Implements the "compare everything until the best single daily trade
#  is found" spec:
#    Stage A  walk-forward model pass (leak-free, expanding window)
#    Stage B  sweeps on a VALIDATION slice of the test window:
#               entry times -> exit logic -> candidate ranking -> filters
#    Stage C  final leaderboard of all required variants, judged on an
#             untouched HOLDOUT slice (net of costs, 2x-slippage stress)
#    Stage D  honesty verdict + success-condition checklist + reports
#
#  Every trade is simulated bar-by-bar on 1-minute data with a
#  conservative fill convention (if TP and SL are touched inside the
#  same bar, SL wins; gaps through a level fill at the bar open).
# =====================================================================

CMP_VAL_FRACTION  = 0.60      # test window: first 60% = validation (sweeps), last 40% = holdout
CMP_MIN_TRADES    = 15        # below this a config is "insufficient evidence"
CMP_KNN_K         = 50        # neighbours for historical similar-setup TP/SL
CMP_ENTRY_CUTOFF  = STOP_ENTRY_HOUR * 60 + STOP_ENTRY_MINUTE   # no entries after 11:00
CMP_EOD_MIN       = CLOSE_HOUR_ET * 60 + CLOSE_MINUTE_ET       # forced flat 15:55
CMP_OPEN_MIN      = OPEN_HOUR_ET * 60 + OPEN_MINUTE_ET         # 9:30 = 570
CMP_TIME_STOP_MIN = 120
CMP_MAX_DD_OK     = -15.0     # success-checklist drawdown tolerance (%)
CMP_PM_DOLLAR_MIN = 300_000.0 # base liquidity floor (premarket $ volume)

# feature set legal for entries BEFORE 9:35 (open5_* would leak there)
PM_FEATURES = [f for f in FEATURES
               if not f.startswith("open5_") and f != "mkt_open5_pct"]

CMP_ENTRIES = ["0930", "0931", "0935", "0940", "0945",
               "orb15", "vwap_reclaim", "first_pullback", "first_green"]
CMP_EXITS   = ["model_dyn", "knn_dyn", "vol_dyn", "fixed_3_2", "fixed_1_1",
               "structure", "vwap_inval", "model_trail", "model_be", "model_time"]
CMP_RANKS   = ["ev", "up_prob", "pred_ev", "mfe_mae", "ranker",
               "pm_pct", "pm_vol_surge", "open5_pct", "catalyst", "rel_strength"]

CMP_OPT_FILTERS = ["no_neg_news", "has_news", "no_risk_news", "mkt_ok",
                   "gap_cap", "range_cap", "dollar_hi", "open5_green", "above_o5vwap"]

_RISK_NEWS_KW = re.compile(
    r"\b(offering|dilut\w*|warrant|reverse split|going concern|delist\w*"
    r"|SEC (probe|investigat\w*|subpoena|charge)|fraud|class action|lawsuit"
    r"|complete response letter|CRL|FDA (reject\w*|declin\w*)|clinical hold"
    r"|trading halt)\b", re.IGNORECASE)


class _CmpConfig:
    """One evaluable configuration = entry + exit + rank + filters + gating."""
    def __init__(self, name, entry="0935", exit_key="model_dyn", rank="ev",
                 filters=(), n_trades=1, selective=True, use_model_gate=True,
                 veto=False, rule_only=False):
        self.name, self.entry, self.exit_key, self.rank = name, entry, exit_key, rank
        self.filters   = tuple(filters)
        self.n_trades  = n_trades
        self.selective = selective          # False = forced mode
        self.use_model_gate = use_model_gate
        self.veto      = veto
        self.rule_only = rule_only

    def describe(self):
        return (f"entry={self.entry} exit={self.exit_key} rank={self.rank} "
                f"n={self.n_trades} {'selective' if self.selective else 'FORCED'}"
                f"{' +veto' if self.veto else ''}"
                f"{' [rule-only]' if self.rule_only else ''} "
                f"filters={list(self.filters) or 'base'}")


# ---------------------------------------------------------------- stage A
def _cmp_train_bank(train_df: pd.DataFrame, feats: list[str]) -> dict:
    """Train the full model bank on a restricted feature list (used for the
    premarket-only bank that serves 09:30/09:31 entries without open5 leakage)."""
    global FEATURES
    keep, FEATURES = FEATURES, list(feats)
    try:
        bank = _train_models_on(train_df)
    finally:
        FEATURES = keep
    bank["feats"] = list(feats)
    return bank


def _cmp_apply_bank(day_df: pd.DataFrame, bank: dict) -> pd.DataFrame:
    global FEATURES
    keep, FEATURES = FEATURES, list(bank.get("feats", FEATURES))
    try:
        return _apply_scores(day_df, bank)
    finally:
        FEATURES = keep


def _cmp_fit_knn(train_df: pd.DataFrame):
    """Historical similar-setup engine: k-NN in feature space over past
    trades; returns a callable df -> (knn_mfe, knn_dd, knn_p) arrays."""
    from sklearn.neighbors import NearestNeighbors
    from sklearn.preprocessing import StandardScaler
    sub = train_df[train_df["target_mfe"].notna() & train_df["target_dd"].notna()]
    if len(sub) < CMP_KNN_K * 2:
        return None
    Xs = _ensure_features(sub.copy())[FEATURES].fillna(0).to_numpy()
    scaler = StandardScaler().fit(Xs)
    nn = NearestNeighbors(n_neighbors=min(CMP_KNN_K, len(sub))).fit(scaler.transform(Xs))
    mfe = sub["target_mfe"].to_numpy()
    dd  = sub["target_dd"].to_numpy()
    lab = sub["label"].fillna(0).to_numpy().astype(float)

    def _predict(df: pd.DataFrame):
        Xq = _ensure_features(df.copy())[FEATURES].fillna(0).to_numpy()
        idx = nn.kneighbors(scaler.transform(Xq), return_distance=False)
        return (np.median(mfe[idx], axis=1),
                np.median(dd[idx],  axis=1),
                np.mean(lab[idx],   axis=1))
    return _predict


def _cmp_scored_frame(all_df: pd.DataFrame, test_dates: list[str],
                      retrain_every: int) -> pd.DataFrame:
    """Walk-forward scoring pass. For every test-day candidate row, attach
    model scores from BOTH banks (full-feature for >=9:35 entries and
    premarket-only for earlier entries), k-NN TP/SL stats and the
    auto-derived probability gates — trained strictly on prior dates."""
    out, banks, last_k = [], None, -10**9
    for k, d in enumerate(test_dates):
        if banks is None or (k - last_k) >= retrain_every:
            tr = all_df[all_df["date"] < d]
            try:
                full = _cmp_train_bank(tr, FEATURES)
                pmb  = _cmp_train_bank(tr, PM_FEATURES)
            except RuntimeError as e:
                print(f"  [compare skip {d}] {e}")
                continue
            knn = _cmp_fit_knn(_ensure_features(_prepare_targets(tr.copy())))
            banks, last_k = {"full": full, "pm": pmb, "knn": knn}, k
        day = all_df[all_df["date"] == d].copy()
        if day.empty:
            continue
        day = _cmp_apply_bank(day, banks["full"])
        pm  = _cmp_apply_bank(all_df[all_df["date"] == d].copy(), banks["pm"])
        pm  = pm.set_index("ticker")
        for src, dst in (("up_prob", "up_prob_pm"), ("pred_gain_pct", "pred_gain_pm"),
                         ("pred_dd_pct", "pred_dd_pm"), ("pred_mfe_pct", "pred_mfe_pm"),
                         ("score", "score_pm")):
            day[dst] = day["ticker"].map(pm[src])
        day["gate"]    = float(banks["full"].get("up_prob_gate", MIN_UP_PROB))
        day["gate_pm"] = float(banks["pm"].get("up_prob_gate",  MIN_UP_PROB))
        if banks["knn"] is not None:
            km, kd, kp = banks["knn"](day)
            day["knn_mfe"], day["knn_dd"], day["knn_p"] = km, kd, kp
        else:
            day["knn_mfe"] = day["knn_dd"] = day["knn_p"] = np.nan
        out.append(day)
    if not out:
        raise RuntimeError("Walk-forward scoring produced no rows.")
    return pd.concat(out, ignore_index=True)


def _cmp_risk_news_flags(con, dates: list[str]) -> dict[tuple[str, str], int]:
    """Dilution / offering / SEC-risk / FDA-rejection flag per (date, ticker),
    from headlines in the same pre-open window the sentiment features use.
    Silently returns {} when the market_news table is absent."""
    flags: dict[tuple[str, str], int] = {}
    for d in dates:
        base = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
        lo = (base - timedelta(days=1)).replace(hour=16, minute=0)
        hi = base.replace(hour=OPEN_HOUR_ET, minute=OPEN_MINUTE_ET + NEWS_CUTOFF_MIN)
        try:
            rows = con.execute(
                "SELECT ticker, title FROM market_news "
                "WHERE published_utc >= ? AND published_utc < ?",
                [lo.astimezone(timezone.utc).replace(tzinfo=None),
                 hi.astimezone(timezone.utc).replace(tzinfo=None)]).fetchall()
        except Exception:
            return {}
        for t, title in rows:
            if title and _RISK_NEWS_KW.search(title):
                flags[(d, t)] = 1
    return flags


# ---------------------------------------------------------------- bars
def _cmp_load_day_bars(con, date_str: str, tickers: list[str]) -> dict[str, dict]:
    """Regular-session 1-min bars per ticker as numpy arrays with running
    VWAP and the 9:30-9:44 opening range precomputed."""
    tick_sql = _ticker_sql_list(tickers)
    if not tick_sql:
        return {}
    s_ns = utc_ns(date_str, OPEN_HOUR_ET, OPEN_MINUTE_ET)
    e_ns = utc_ns(date_str, 16, 0)
    df = con.execute(f"""
        SELECT ticker, window_start, open, high, low, close, volume
        FROM minute_bars
        WHERE window_start >= {s_ns} AND window_start < {e_ns}
          AND ticker IN ({tick_sql})
        ORDER BY ticker, window_start
    """).fetchdf()
    out: dict[str, dict] = {}
    if df.empty:
        return out
    ts = pd.to_datetime(df["window_start"], unit="ns", utc=True).dt.tz_convert("America/New_York")
    df["m"] = ts.dt.hour * 60 + ts.dt.minute
    for tkr, g in df.groupby("ticker"):
        g = g.sort_values("m")
        m = g["m"].to_numpy()
        o, h, l, c = (g[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        v = np.maximum(g["volume"].to_numpy(float), 0.0)
        cum_pv, cum_v = np.cumsum(c * v), np.cumsum(v)
        vwap = np.where(cum_v > 0, cum_pv / np.maximum(cum_v, 1e-9), c)
        orr = m < CMP_OPEN_MIN + 15
        out[tkr] = {"m": m, "o": o, "h": h, "l": l, "c": c, "vwap": vwap,
                    "or_high": float(h[orr].max()) if orr.any() else float("nan"),
                    "or_low":  float(l[orr].min()) if orr.any() else float("nan")}
    return out


# ---------------------------------------------------------------- entries
def _cmp_entry(bars: dict, entry_key: str):
    """Resolve entry -> (bar index, fill price, minute-of-day) or None.
    Event entries signal on a bar CLOSE and fill at the NEXT bar open
    (no intra-bar lookahead); ORB fills at the breakout level itself."""
    m, o, h, c, vwap = bars["m"], bars["o"], bars["h"], bars["c"], bars["vwap"]
    n = len(m)
    if n == 0:
        return None

    def _at_or_after(minute):
        idx = np.searchsorted(m, minute)
        if idx >= n or m[idx] > CMP_ENTRY_CUTOFF:
            return None
        return int(idx), float(o[idx]), int(m[idx])

    if entry_key.isdigit():
        return _at_or_after(int(entry_key[:2]) * 60 + int(entry_key[2:]))

    if entry_key == "orb15":
        orh = bars["or_high"]
        if not np.isfinite(orh):
            return None
        for i in range(n):
            if m[i] < CMP_OPEN_MIN + 15:
                continue
            if m[i] > CMP_ENTRY_CUTOFF:
                return None
            if h[i] > orh:
                return i, float(max(orh, o[i])), int(m[i])
        return None

    if entry_key == "vwap_reclaim":
        for i in range(1, n - 1):
            if m[i] < CMP_OPEN_MIN + 5:
                continue
            if m[i] > CMP_ENTRY_CUTOFF:
                return None
            if c[i - 1] < vwap[i - 1] and c[i] > vwap[i]:
                return i + 1, float(o[i + 1]), int(m[i + 1])
        return None

    if entry_key == "first_pullback":
        open_px, runmax, pulled = float(o[0]), float(h[0]), False
        for i in range(1, n - 1):
            runmax = max(runmax, float(h[i - 1]))
            if m[i] > CMP_ENTRY_CUTOFF:
                return None
            pushed = runmax > open_px * 1.005
            if pushed and (c[i] <= open_px + 0.5 * (runmax - open_px) or c[i] < vwap[i]):
                pulled = True
            elif pulled and c[i] > h[i - 1]:
                return i + 1, float(o[i + 1]), int(m[i + 1])
        return None

    if entry_key == "first_green":
        for i in range(1, n - 1):
            if m[i] > CMP_ENTRY_CUTOFF:
                return None
            if c[i] > o[i] and c[i] > c[i - 1]:
                return i + 1, float(o[i + 1]), int(m[i + 1])
        return None
    return None


# ---------------------------------------------------------------- exits
def _cmp_row_preds(row: pd.Series, pre935: bool):
    sfx = "_pm" if pre935 else ""
    g = lambda k, kk: float(row.get(k + sfx if kk else k, np.nan))
    return {"up_prob":  float(row.get("up_prob_pm" if pre935 else "up_prob", 0.5)),
            "pred_gain": g("pred_gain" if pre935 else "pred_gain_pct", pre935),
            "pred_dd":   g("pred_dd"   if pre935 else "pred_dd_pct",   pre935),
            "pred_mfe":  g("pred_mfe"  if pre935 else "pred_mfe_pct",  pre935)}


def _cmp_exit_levels(exit_key: str, row: pd.Series, fill: float,
                     bars: dict | None, pre935: bool):
    """-> (tp_price, sl_price, flags). flags: trail_pct, be_frac, time_stop,
    vwap_exit. `bars=None` gives the pre-trade ESTIMATE used for EV/ranking
    (structure falls back to premarket low only)."""
    p = _cmp_row_preds(row, pre935)
    pm_pct = float(row.get("pm_pct", 0.0) or 0.0)
    flags: dict = {}

    if exit_key in ("model_dyn", "model_trail", "model_be", "model_time"):
        tp, sl = calc_tp_sl(fill, p["pred_gain"], p["pred_dd"], p["pred_mfe"],
                            pm_pct, p["up_prob"])
        if exit_key == "model_trail":
            flags["trail_pct"] = max(0.6 * abs(sl / fill - 1) * 100, 0.6)
        elif exit_key == "model_be":
            flags["be_frac"] = 0.6
        elif exit_key == "model_time":
            flags["time_stop"] = CMP_TIME_STOP_MIN
        return tp, sl, flags

    if exit_key == "knn_dyn":
        tp, sl = calc_tp_sl(fill, np.nan, float(row.get("knn_dd", np.nan)),
                            float(row.get("knn_mfe", np.nan)), pm_pct, p["up_prob"])
        return tp, sl, flags

    if exit_key == "vol_dyn":
        rng = max(float(row.get("pm_range_pct", 0.0) or 0.0), 1.0)
        tp_pct = float(np.clip(0.9 * rng, TP_MIN_PCT, TP_MAX_PCT))
        sl_pct = -float(np.clip(0.5 * rng, abs(SL_MIN_PCT), abs(SL_MAX_PCT)))
        return fill * (1 + tp_pct / 100), fill * (1 + sl_pct / 100), flags

    if exit_key == "fixed_3_2":
        return fill * 1.03, fill * 0.98, flags
    if exit_key == "fixed_1_1":
        return fill * (1 + LABEL_TP_BARRIER_PCT / 100), fill * (1 - LABEL_SL_BARRIER_PCT / 100), flags

    if exit_key == "structure":
        lvl = float(row.get("pm_low", np.nan) or np.nan)
        if bars is not None and np.isfinite(bars.get("or_low", np.nan)):
            lvl = np.nanmin([lvl, bars["or_low"]])
        if not np.isfinite(lvl) or lvl >= fill:
            sl = fill * (1 + SL_MIN_PCT / 100)
        else:
            sl = max(lvl * 0.998, fill * (1 + SL_MAX_PCT / 100))
        sl_dist = abs(sl / fill - 1) * 100
        tp = fill * (1 + min(2.0 * sl_dist, TP_MAX_PCT) / 100)
        return tp, sl, flags

    if exit_key == "vwap_inval":
        rng = max(float(row.get("pm_range_pct", 0.0) or 0.0), 1.0)
        tp_pct = float(np.clip(0.9 * rng, TP_MIN_PCT, TP_MAX_PCT))
        flags["vwap_exit"] = True
        return fill * (1 + tp_pct / 100), fill * 0.96, flags

    raise ValueError(f"unknown exit_key {exit_key}")


def _cmp_exit_estimate(exit_key: str, row: pd.Series, pre935: bool):
    """Pre-trade (tp_pct, sl_pct) estimate for EV ranking — no bar data used."""
    ref = float(row.get("pm_close", 0.0) or 0.0) or 1.0
    tp, sl, _ = _cmp_exit_levels(exit_key, row, ref, None, pre935)
    return (tp / ref - 1) * 100, (sl / ref - 1) * 100


def _cmp_sim(bars: dict, i0: int, fill: float, tp: float, sl: float,
             flags: dict) -> dict:
    """Bar-by-bar exit simulation. Conservative conventions:
    both levels inside one bar -> SL; gap through a level -> fill at open;
    trailing/break-even stops only tighten; EOD flat at 15:55."""
    m, o, h, l, c, vwap = (bars[x] for x in ("m", "o", "h", "l", "c", "vwap"))
    n = len(m)
    runmax, pending_vwap = fill, False
    trail = flags.get("trail_pct"); be_frac = flags.get("be_frac")
    tstop = flags.get("time_stop"); vw = flags.get("vwap_exit", False)
    mfe = mae = 0.0

    def _done(px, reason, i):
        return {"exit_price": float(px), "exit_reason": reason,
                "exit_min": int(m[i]), "gross_pct": (px / fill - 1) * 100,
                "mfe_pct": mfe, "mae_pct": mae, "hold_min": int(m[i] - m[i0])}

    for i in range(i0, n):
        if i > i0 and pending_vwap:
            return _done(o[i], "vwap_inval", i)
        if i > i0:
            if o[i] <= sl:
                return _done(o[i], "sl_gap", i)
            if o[i] >= tp:
                return _done(o[i], "tp_gap", i)
        hi = h[i] if i > i0 else max(h[i], fill)
        lo = l[i] if i > i0 else min(l[i], fill)
        mfe = max(mfe, (hi / fill - 1) * 100)
        mae = min(mae, (lo / fill - 1) * 100)
        if lo <= sl and hi >= tp:
            return _done(sl, "sl_ambiguous", i)
        if lo <= sl:
            return _done(sl, "sl", i)
        if hi >= tp:
            return _done(tp, "tp", i)
        runmax = max(runmax, hi)
        if trail is not None:
            sl = max(sl, runmax * (1 - trail / 100))
        if be_frac is not None and runmax >= fill + be_frac * (tp - fill):
            sl = max(sl, fill * 1.0005)
        if vw and i >= i0 + 3 and c[i] < vwap[i]:
            pending_vwap = True
        if tstop is not None and m[i] - m[i0] >= tstop:
            return _done(c[i], "time_stop", i)
        if m[i] >= CMP_EOD_MIN:
            return _done(c[i], "eod", i)
    return _done(c[n - 1], "eod_last_bar", n - 1)


# ---------------------------------------------------------------- filters
def _cmp_base_mask(day: pd.DataFrame) -> pd.Series:
    px = day.get("pm_close", pd.Series(0.0, index=day.index)).fillna(0.0)
    vol = day.get("pm_volume", pd.Series(0.0, index=day.index)).fillna(0.0)
    return ((px >= MIN_PRICE)
            & (day.get("pm_pct", 0).fillna(0) >= MIN_GAINER_PCT)
            & ((px * vol) >= CMP_PM_DOLLAR_MIN))


def _cmp_filter_mask(day: pd.DataFrame, name: str, pre935: bool) -> pd.Series:
    z = lambda col, default=0.0: day.get(col, pd.Series(default, index=day.index)).fillna(default)
    if name == "no_neg_news":  return z("news_sentiment") >= 0.0
    if name == "has_news":     return z("news_count") >= 1
    if name == "no_risk_news": return z("risk_news") == 0
    if name == "mkt_ok":       return z("mkt_pm_pct") >= -0.10
    if name == "gap_cap":      return z("pm_pct") <= 30.0
    if name == "range_cap":    return z("pm_range_pct", 99.0) <= 25.0
    if name == "dollar_hi":    return (z("pm_close") * z("pm_volume")) >= 1_000_000.0
    if name == "open5_green":
        return pd.Series(True, index=day.index) if pre935 else z("open5_pct") > MIN_OPEN5_PCT
    if name == "above_o5vwap":
        return pd.Series(True, index=day.index) if pre935 else z("open5_vwap") >= 0.0
    raise ValueError(f"unknown filter {name}")


def _cmp_veto(row: pd.Series) -> str | None:
    """Deterministic risk veto — offline stand-in for the live Claude agent trio."""
    if float(row.get("risk_news", 0) or 0) == 1:            return "risk_news"
    if float(row.get("news_sentiment", 0) or 0) < -0.15:    return "negative_news"
    if float(row.get("mkt_pm_pct", 0) or 0) < -0.30:        return "market_gap_down"
    if float(row.get("pm_pct", 0) or 0) > 60.0:             return "parabolic_gap"
    return None


def _cmp_zs(s: pd.Series) -> pd.Series:
    s = s.fillna(0.0)
    sd = float(s.std())
    return (s - s.mean()) / sd if sd > 1e-9 else s * 0.0


def _cmp_cost_pct(row: pd.Series, cost_bps: float, mult: float = 1.0) -> float:
    surge = float(row.get("pm_vol_surge", 0.0) or 0.0)
    slip = min(BT_SLIP_BPS_PER_SURGE * max(surge, 0.0), BT_SLIP_CAP_BPS)
    return (cost_bps + slip) / 100.0 * 2.0 * mult


def _cmp_rank_col(day: pd.DataFrame, cfg: "_CmpConfig", pre935: bool,
                  cost_bps: float) -> pd.DataFrame:
    """Attach `ev_est` (always) and `rank_val` (per cfg.rank) to the day frame."""
    day = day.copy()
    p_col = "up_prob_pm" if pre935 else "up_prob"
    p = day.get(p_col, pd.Series(0.5, index=day.index)).fillna(0.5)
    tp_est, sl_est = [], []
    for _, r in day.iterrows():
        t, s = _cmp_exit_estimate(cfg.exit_key, r, pre935)
        tp_est.append(t); sl_est.append(s)
    day["tp_est"], day["sl_est"] = tp_est, sl_est
    cost = day.apply(lambda r: _cmp_cost_pct(r, cost_bps), axis=1)
    day["ev_est"] = p * day["tp_est"] - (1 - p) * day["sl_est"].abs() - cost

    r = cfg.rank
    if   r == "ev":           day["rank_val"] = day["ev_est"]
    elif r == "up_prob":      day["rank_val"] = p
    elif r == "pred_ev":
        g = day.get("pred_gain_pm" if pre935 else "pred_gain_pct",
                    pd.Series(np.nan, index=day.index))
        day["rank_val"] = g.fillna(0) * p
    elif r == "mfe_mae":
        mfe = day.get("pred_mfe_pm" if pre935 else "pred_mfe_pct",
                      pd.Series(np.nan, index=day.index)).fillna(0)
        dd = day.get("pred_dd_pm" if pre935 else "pred_dd_pct",
                     pd.Series(np.nan, index=day.index)).abs().clip(lower=0.5)
        day["rank_val"] = mfe / dd
    elif r == "ranker":
        s = day.get("score_pm" if pre935 else "score",
                    pd.Series(np.nan, index=day.index))
        day["rank_val"] = s.fillna(day["ev_est"])
    elif r == "pm_pct":       day["rank_val"] = day.get("pm_pct", 0).fillna(0)
    elif r == "pm_vol_surge": day["rank_val"] = day.get("pm_vol_surge", 0).fillna(0)
    elif r == "open5_pct":
        day["rank_val"] = (day.get("pm_pct", 0).fillna(0) if pre935
                           else day.get("open5_pct", 0).fillna(0))
    elif r == "catalyst":
        day["rank_val"] = (day.get("news_sentiment", 0).fillna(0)
                           * np.log1p(day.get("news_count", 0).fillna(0))
                           + 0.5 * day.get("has_earnings", 0).fillna(0)
                           + 0.5 * day.get("has_fda", 0).fillna(0))
    elif r == "rel_strength": day["rank_val"] = day.get("mkt_rel_pm_pct", 0).fillna(0)
    elif r == "rule":
        o5 = day.get("open5_pct", 0).fillna(0) * (0.0 if pre935 else 1.0)
        day["rank_val"] = (_cmp_zs(day.get("pm_pct", 0)) +
                           _cmp_zs(day.get("pm_vol_surge", 0)) + _cmp_zs(o5))
    elif r == "model_rule":
        o5 = day.get("open5_pct", 0).fillna(0) * (0.0 if pre935 else 1.0)
        rule = (_cmp_zs(day.get("pm_pct", 0)) + _cmp_zs(day.get("pm_vol_surge", 0))
                + _cmp_zs(o5))
        day["rank_val"] = 0.5 * _cmp_zs(day["ev_est"]) + 0.5 * _cmp_zs(rule)
    else:
        raise ValueError(f"unknown rank {r}")
    return day


# ---------------------------------------------------------------- evaluation
def _cmp_evaluate(configs: list["_CmpConfig"], dates: list[str],
                  scored: pd.DataFrame, con, cost_bps: float,
                  sim_memo: dict, bars_cache: dict,
                  daily_log: dict | None = None) -> dict[str, list[dict]]:
    """Evaluate all configs in ONE pass over the dates. Selection is done on
    the scored frame; only selected tickers get bar simulations (memoised)."""
    results: dict[str, list[dict]] = {c.name: [] for c in configs}
    by_date = dict(tuple(scored.groupby("date")))
    for d in dates:
        day0 = by_date.get(d)
        if day0 is None or day0.empty:
            continue
        need: set[str] = set()
        picks: dict[str, list[tuple[pd.Series, str | None]]] = {}
        for cfg in configs:
            pre935 = cfg.entry.isdigit() and int(cfg.entry) < 935
            day = day0[_cmp_base_mask(day0)]
            failed: dict[str, str] = {}
            for f in cfg.filters:
                keep = _cmp_filter_mask(day, f, pre935)
                for t in day.loc[~keep, "ticker"]:
                    failed.setdefault(t, f)
                day = day[keep]
            if day.empty:
                picks[cfg.name] = []
                if daily_log is not None and cfg.name in daily_log:
                    daily_log[cfg.name].append({"date": d, "selected": None,
                                                "reason": "no candidate passed filters"})
                continue
            day = _cmp_rank_col(day, cfg, pre935, cost_bps).sort_values(
                "rank_val", ascending=False)
            gate_col = "gate_pm" if pre935 else "gate"
            p_col = "up_prob_pm" if pre935 else "up_prob"
            sel: list[tuple[pd.Series, str | None]] = []
            rejected: list[dict] = []
            for _, r in day.iterrows():
                why = None
                if cfg.veto:
                    why = _cmp_veto(r) and f"veto:{_cmp_veto(r)}"
                if why is None and cfg.selective:
                    if float(r["ev_est"]) <= 0:
                        why = f"EV<=0 ({r['ev_est']:+.2f}%)"
                    elif cfg.use_model_gate and not cfg.rule_only and \
                            float(r.get(p_col, 0.5)) < float(r.get(gate_col, MIN_UP_PROB)):
                        why = f"p={r.get(p_col, 0.5):.3f} < gate {r.get(gate_col, 0):.3f}"
                    elif cfg.rule_only and float(r["rank_val"]) <= 0:
                        why = "rule score <= 0"
                if why is None and len(sel) < cfg.n_trades:
                    sel.append((r, None))
                elif len(rejected) < 5:
                    rejected.append({"ticker": r["ticker"],
                                     "rank_val": round(float(r["rank_val"]), 3),
                                     "reason": why or "ranked below selection"})
            picks[cfg.name] = sel
            need.update(r["ticker"] for r, _ in sel)
            if daily_log is not None and cfg.name in daily_log:
                daily_log[cfg.name].append({
                    "date": d, "rank_key": cfg.rank,
                    "selected": [r["ticker"] for r, _ in sel] or None,
                    "rejected_top": rejected,
                    "filtered_out": [{"ticker": t, "filter": f}
                                     for t, f in list(failed.items())[:5]]})

        if d not in bars_cache:
            bars_cache.clear()                       # keep memory flat: one day at a time
            bars_cache[d] = _cmp_load_day_bars(con, d, sorted(need))
        else:
            missing = sorted(need - set(bars_cache[d]))
            if missing:
                bars_cache[d].update(_cmp_load_day_bars(con, d, missing))
        day_bars = bars_cache[d]

        for cfg in configs:
            pre935 = cfg.entry.isdigit() and int(cfg.entry) < 935
            for r, _ in picks.get(cfg.name, []):
                tkr = r["ticker"]
                key = (d, tkr, cfg.entry, cfg.exit_key)
                if key not in sim_memo:
                    bars = day_bars.get(tkr)
                    ent = _cmp_entry(bars, cfg.entry) if bars else None
                    if ent is None:
                        sim_memo[key] = None
                    else:
                        i0, fill, e_min = ent
                        tp, sl, flags = _cmp_exit_levels(cfg.exit_key, r, fill, bars, pre935)
                        sim = _cmp_sim(bars, i0, fill, tp, sl, flags)
                        sim.update({"fill": fill, "tp": tp, "sl": sl, "entry_min": e_min})
                        sim_memo[key] = sim
                sim = sim_memo[key]
                if sim is None:
                    results[cfg.name].append({"date": d, "ticker": tkr, "no_signal": True})
                    continue
                c1 = _cmp_cost_pct(r, cost_bps, 1.0)
                tr = {"date": d, "ticker": tkr, "no_signal": False,
                      "entry": cfg.entry, "exit_key": cfg.exit_key,
                      "entry_min": sim["entry_min"], "fill": round(sim["fill"], 4),
                      "tp": round(sim["tp"], 4), "sl": round(sim["sl"], 4),
                      "tp_est_pct": round(float(r.get("tp_est", np.nan)), 3),
                      "ev_est": round(float(r.get("ev_est", np.nan)), 3),
                      "up_prob": round(float(r.get("up_prob_pm" if pre935 else "up_prob", np.nan)), 4),
                      "pred_mfe": round(float(r.get("pred_mfe_pm" if pre935 else "pred_mfe_pct", np.nan) or np.nan), 3),
                      "exit_reason": sim["exit_reason"], "hold_min": sim["hold_min"],
                      "gross_pct": round(sim["gross_pct"], 4),
                      "net_pct": round(sim["gross_pct"] - c1, 4),
                      "net2x_pct": round(sim["gross_pct"] - _cmp_cost_pct(r, cost_bps, 2.0), 4),
                      "mkt_pm_pct": float(r.get("mkt_pm_pct", 0.0) or 0.0)}
                results[cfg.name].append(tr)
                if daily_log is not None and cfg.name in daily_log:
                    daily_log[cfg.name][-1]["outcome"] = {
                        k: tr[k] for k in ("ticker", "entry_min", "fill", "tp", "sl",
                                           "exit_reason", "net_pct")}
    return results


# ---------------------------------------------------------------- metrics
def _cmp_metrics(trades: list[dict], dates: list[str]) -> dict:
    total_days = len(dates)
    real = [t for t in trades if not t.get("no_signal")]
    days_traded = len({t["date"] for t in real})
    base = {"total_days": total_days, "days_traded": days_traded,
            "skipped_days": total_days - days_traded, "n_trades": len(real)}
    if not real:
        base.update({k: 0.0 for k in ("win_rate", "avg_win", "avg_loss", "profit_factor",
                                      "exp_per_trade", "exp_per_day", "exp_per_day_2x",
                                      "net_return_pct", "max_dd", "worst_day", "worst_week",
                                      "sharpe", "sortino")})
        base.update({"longest_losing_streak": 0, "monthly": {}, "regime": {}})
        return base
    df = pd.DataFrame(real)
    daily = df.groupby("date").agg(net=("net_pct", "mean"),
                                   net2x=("net2x_pct", "mean"),
                                   mkt=("mkt_pm_pct", "first"))
    daily = daily.reindex(dates, fill_value=0.0)
    wins = df[df["net_pct"] > 0]["net_pct"]; losses = df[df["net_pct"] <= 0]["net_pct"]
    eq = (1 + daily["net"] / 100).cumprod()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    streak = cur = 0
    for x in df.sort_values("date")["net_pct"]:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    d_idx = pd.to_datetime(daily.index)
    weekly = daily["net"].groupby(d_idx.to_period("W")).sum()
    monthly = daily["net"].groupby(d_idx.to_period("M")).sum()
    nz = daily["net"][daily["net"] != 0]
    dn = nz[nz < 0]
    regime = {}
    for name, mask in (("risk_on", daily["mkt"] > 0.15),
                       ("flat", daily["mkt"].abs() <= 0.15),
                       ("risk_off", daily["mkt"] < -0.15)):
        sub = daily.loc[mask & (daily["net"] != 0), "net"]
        regime[name] = {"days": int(mask.sum()), "exp_per_day": round(float(sub.mean()), 3) if len(sub) else 0.0}
    base.update({
        "win_rate": round(len(wins) / len(df) * 100, 1),
        "avg_win": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "profit_factor": round(float(wins.sum() / max(-losses.sum(), 1e-9)), 2) if len(losses) else float("inf"),
        "exp_per_trade": round(float(df["net_pct"].mean()), 3),
        "exp_per_day": round(float(daily["net"].mean()), 3),
        "exp_per_day_2x": round(float(daily["net2x"].mean()), 3),
        "net_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "max_dd": round(float(dd), 2),
        "longest_losing_streak": int(streak),
        "worst_day": round(float(daily["net"].min()), 3),
        "worst_week": round(float(weekly.min()), 3) if len(weekly) else 0.0,
        "sharpe": round(float(np.clip(nz.mean() / (nz.std() + 1e-9) * np.sqrt(252), -99.9, 99.9)), 2) if len(nz) > 1 else 0.0,
        "sortino": round(float(np.clip(nz.mean() / (dn.std() + 1e-9) * np.sqrt(252), -99.9, 99.9)), 2) if len(dn) > 1 else (99.9 if len(nz) and nz.mean() > 0 else 0.0),
        "monthly": {str(k): round(float(v), 2) for k, v in monthly.items()},
        "regime": regime,
    })
    return base


def _cmp_pick_best(cands: list[tuple[str, dict]]) -> str:
    """Best by net expectancy/day with a minimum-evidence floor; tie-breaks:
    2x-slippage expectancy, then max drawdown."""
    ok = [(n, m) for n, m in cands if m["n_trades"] >= CMP_MIN_TRADES]
    pool = ok or cands
    pool = sorted(pool, key=lambda x: (x[1]["exp_per_day"], x[1]["exp_per_day_2x"],
                                       x[1]["max_dd"]), reverse=True)
    return pool[0][0]


# ---------------------------------------------------------------- orchestration
def _cmp_print_table(rows: list[tuple[str, dict]], title: str):
    print(f"\n  {title}")
    hdr = (f"  {'config':<26}{'trades':>7}{'win%':>7}{'exp/tr':>8}{'exp/day':>9}"
           f"{'2xslip':>8}{'PF':>6}{'maxDD':>8}{'streak':>7}{'sharpe':>8}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for name, m in rows:
        pf = m["profit_factor"]
        print(f"  {name:<26}{m['n_trades']:>7}{m['win_rate']:>7.1f}"
              f"{m['exp_per_trade']:>8.3f}{m['exp_per_day']:>9.3f}"
              f"{m['exp_per_day_2x']:>8.3f}{(f'{pf:.2f}' if np.isfinite(pf) else 'inf'):>6}"
              f"{m['max_dd']:>8.2f}{m['longest_losing_streak']:>7}{m['sharpe']:>8.2f}")


def run_compare(date_arg: str | None, today_et: datetime,
                split: str | None = None, cost_bps: float = 8.0,
                quick: bool = False):
    """ONE-TRADE-PER-DAY COMPARE LAB — see block header. Sweeps are selected
    on a validation slice; the leaderboard verdict comes from an untouched
    holdout slice, both net of costs and stress-tested at 2x slippage."""
    session_log = start_session_log(today_et, "COMPARE")
    print(f"\n{'='*64}\n  MAMDOUH — ONE-TRADE-PER-DAY COMPARE LAB\n{'='*64}")
    try:
        assert_no_leakage()
        con = duckdb.connect(str(DB_PATH), read_only=True)
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM features ORDER BY date").fetchall()]
        all_df = con.execute(
            "SELECT * FROM features WHERE label IS NOT NULL AND post_open_close IS NOT NULL"
        ).fetchdf()
        if all_df.empty:
            raise RuntimeError("features table empty — run the data pipeline first.")
        all_df = _ensure_features(_prepare_targets(all_df))

        if split is None:
            split = dates[max(1, int(len(dates) * 0.8))]
        test_dates = [d for d in dates if d >= split]
        if date_arg:
            test_dates = [d for d in test_dates if d <= date_arg]
        if len(test_dates) < 20:
            raise RuntimeError(f"Only {len(test_dates)} test dates after split={split} — need >= 20.")
        cut = max(5, int(len(test_dates) * CMP_VAL_FRACTION))
        val_dates, hold_dates = test_dates[:cut], test_dates[cut:]
        retrain_every = max(BT_RETRAIN_EVERY, 20) if quick else BT_RETRAIN_EVERY
        print(f"  Test window : {test_dates[0]} → {test_dates[-1]}  ({len(test_dates)} days)")
        print(f"  Validation  : {len(val_dates)} days (sweep selection)")
        print(f"  Holdout     : {len(hold_dates)} days (final verdict — untouched by selection)")
        print(f"  Costs       : {cost_bps:.1f} bps/side + surge slippage; stress = 2x")
        print(f"  Fill rules  : conservative (same-bar TP&SL -> SL; gaps fill at open)")

        print("\n  [Stage A] Walk-forward scoring pass (full + premarket-only banks, kNN)…")
        scored = _cmp_scored_frame(all_df, test_dates, retrain_every)
        rn = _cmp_risk_news_flags(con, test_dates)
        scored["risk_news"] = [rn.get((d, t), 0) for d, t in zip(scored["date"], scored["ticker"])]
        print(f"  scored rows : {len(scored):,} over {scored['date'].nunique()} days"
              f"  (risk-news flags: {int(scored['risk_news'].sum())})")

        sim_memo: dict = {}
        bars_cache: dict = {}
        entries = ["0930", "0935", "0945", "orb15", "first_green"] if quick else CMP_ENTRIES
        exits   = ["model_dyn", "fixed_3_2", "vol_dyn", "structure"] if quick else CMP_EXITS
        ranks   = ["ev", "up_prob", "pm_pct", "rule"] if quick else CMP_RANKS

        def _ev(cfgs, log=None):
            return _cmp_evaluate(cfgs, test_dates, scored, con, cost_bps,
                                 sim_memo, bars_cache, daily_log=log)

        def _val(tr):  return _cmp_metrics(tr, val_dates)
        def _hold(tr): return _cmp_metrics(tr, hold_dates)
        def _v(tr, ds): return [t for t in tr if t["date"] in set(ds)]

        # ---- Stage B1: entry-time sweep -----------------------------------
        print(f"\n  [Stage B] Sweeps on VALIDATION ({len(val_dates)} days) — run FORCED "
              f"(no gates) to isolate each dimension; gating itself is compared on the leaderboard")
        cfgs = [_CmpConfig(f"entry:{e}", entry=e, selective=False) for e in entries]
        res = _ev(cfgs)
        rows = [(c.name, _val(_v(res[c.name], val_dates))) for c in cfgs]
        _cmp_print_table(rows, "ENTRY-TIME SWEEP (validation, forced 1/day)")
        best_entry = _cmp_pick_best(rows).split(":", 1)[1]

        # ---- Stage B2: exit-logic sweep -----------------------------------
        cfgs = [_CmpConfig(f"exit:{x}", entry=best_entry, exit_key=x,
                           selective=False) for x in exits]
        res = _ev(cfgs)
        rows = [(c.name, _val(_v(res[c.name], val_dates))) for c in cfgs]
        _cmp_print_table(rows, f"EXIT-LOGIC SWEEP (validation, forced, entry={best_entry})")
        best_exit = _cmp_pick_best(rows).split(":", 1)[1]

        # ---- Stage B3: ranking sweep --------------------------------------
        cfgs = [_CmpConfig(f"rank:{r}", entry=best_entry, exit_key=best_exit, rank=r,
                           selective=False) for r in ranks]
        res = _ev(cfgs)
        rows = [(c.name, _val(_v(res[c.name], val_dates))) for c in cfgs]
        _cmp_print_table(rows, f"CANDIDATE-RANKING SWEEP (validation, forced, {best_entry}/{best_exit})")
        best_rank = _cmp_pick_best(rows).split(":", 1)[1]

        # ---- Stage B4: filter ablation (greedy add) -----------------------
        base_cfg = dict(entry=best_entry, exit_key=best_exit, rank=best_rank)
        kept: list[str] = []
        cur_cfg = _CmpConfig("filt:base", selective=False, **base_cfg)
        cur = _val(_v(_ev([cur_cfg])[cur_cfg.name], val_dates))
        print(f"\n  FILTER ABLATION (validation) — base exp/day {cur['exp_per_day']:+.3f}%")
        for f in CMP_OPT_FILTERS:
            cfg = _CmpConfig(f"filt:+{f}", filters=kept + [f], selective=False, **base_cfg)
            m = _val(_v(_ev([cfg])[cfg.name], val_dates))
            gain = m["exp_per_day"] - cur["exp_per_day"]
            enough = m["n_trades"] >= CMP_MIN_TRADES
            take = gain > 0.005 and enough
            print(f"    +{f:<14} exp/day {m['exp_per_day']:+.3f}% "
                  f"({gain:+.3f})  trades={m['n_trades']}  -> {'KEEP' if take else 'drop'}")
            if take:
                kept.append(f); cur = m
        print(f"  kept filters : {kept or ['(base only)']}")

        # ---- Stage C: final leaderboard on HOLDOUT ------------------------
        B = dict(entry=best_entry, exit_key=best_exit, filters=kept)
        variants = [
            _CmpConfig("1/day FORCED",        rank=best_rank, selective=False, **B),
            _CmpConfig("1/day selective",     rank=best_rank, **B),
            _CmpConfig("2/day selective",     rank=best_rank, n_trades=2, **B),
            _CmpConfig("3/day selective",     rank=best_rank, n_trades=3, **B),
            _CmpConfig("fixed TP/SL baseline", entry=best_entry, exit_key="fixed_3_2",
                       rank=best_rank, filters=kept),
            _CmpConfig("dynamic TP/SL",       entry=best_entry, exit_key=(
                best_exit if best_exit not in ("fixed_3_2", "fixed_1_1") else "model_dyn"),
                       rank=best_rank, filters=kept),
            _CmpConfig("model-only",          rank="up_prob", filters=(), entry=best_entry,
                       exit_key=best_exit),
            _CmpConfig("rule-only",           entry=best_entry, exit_key="fixed_3_2",
                       rank="rule", filters=kept, use_model_gate=False, rule_only=True),
            _CmpConfig("model+rule",          rank="model_rule", **B),
            _CmpConfig("model+rule+veto",     rank="model_rule", veto=True, **B),
        ]
        daily_log = {"1/day FORCED": [], "1/day selective": []}
        res = _ev(variants, log=daily_log)
        hold_rows = [(c.name, _hold(_v(res[c.name], hold_dates))) for c in variants]
        no_trade = _cmp_metrics([], hold_dates); no_trade["win_rate"] = 0.0
        hold_rows.append(("no-trade baseline", no_trade))
        _cmp_print_table(hold_rows, f"LEADERBOARD — HOLDOUT ({len(hold_dates)} days), NET of costs")
        full_rows = [(c.name, _cmp_metrics(res[c.name], test_dates)) for c in variants]
        _cmp_print_table(full_rows, "reference: full test window (val+holdout)")

        Hm = dict(hold_rows)
        forced, sel = Hm["1/day FORCED"], Hm["1/day selective"]
        fixed, dyn = Hm["fixed TP/SL baseline"], Hm["dynamic TP/SL"]
        winner = _cmp_pick_best([r for r in hold_rows if r[0] != "no-trade baseline"])

        # ---- Stage D: honesty verdict + success checklist -----------------
        print(f"\n{'='*64}\n  VERDICT (holdout, net of all costs)\n{'='*64}")
        print(f"  Best combo found : entry={best_entry}  exit={best_exit}  "
              f"rank={best_rank}  filters={kept or 'base'}")
        print(f"  Best variant     : {winner}  "
              f"(exp/day {Hm[winner]['exp_per_day']:+.3f}%, win {Hm[winner]['win_rate']:.1f}%)")
        if forced["exp_per_trade"] <= 0 or forced["n_trades"] < CMP_MIN_TRADES:
            print("\n  Forced daily trading does not currently have a proven edge.")
            print("  Next candidates to research (in order of expected impact):")
            for s in ("point-in-time premarket universe breadth (more/better candidates)",
                      "richer features (float, short interest, premarket VWAP, tape/spread)",
                      "entry timing beyond the tested set (e.g. 1-min ORB, halts/resumes)",
                      "exit logic: regime-conditional TP/SL, partial exits",
                      "candidate ranking: catalyst-split models, TP-before-SL model per barrier",
                      "data sources: real NBBO spreads, halts, SSR flags, borrow data"):
                print(f"    - {s}")
        if sel["exp_per_day"] > forced["exp_per_day"]:
            tail_note = ("" if sel["exp_per_day"] > 0
                         else " (both remain unprofitable here)")
            print(f"\n  Selective 1/day beats forced 1/day "
                  f"({sel['exp_per_day']:+.3f}% vs {forced['exp_per_day']:+.3f}% per day) — "
                  f"skipping no-edge days is the better system{tail_note}. Reported honestly.")
        if dyn["exp_per_trade"] > fixed["exp_per_trade"]:
            print(f"  Dynamic TP/SL beats the fixed baseline "
                  f"({dyn['exp_per_trade']:+.3f}% vs {fixed['exp_per_trade']:+.3f}% per trade).")
        else:
            print(f"  Dynamic TP/SL does NOT beat the fixed baseline "
                  f"({dyn['exp_per_trade']:+.3f}% vs {fixed['exp_per_trade']:+.3f}% per trade) — "
                  f"rejected honestly.")

        halves = np.array_split(np.array(hold_dates), 2)
        sub_ok = all(_cmp_metrics(_v(res[winner], list(hh)), list(hh))["exp_per_day"] > 0
                     for hh in halves if len(hh))
        wm = Hm[winner]
        checks = [
            ("Positive net expectancy",            wm["exp_per_trade"] > 0),
            ("Out-of-sample (holdout) positive",   wm["exp_per_day"] > 0),
            ("Robust across sub-periods",          sub_ok),
            (f"Drawdown within {CMP_MAX_DD_OK}%",  wm["max_dd"] >= CMP_MAX_DD_OK),
            ("Survives 2x slippage",               wm["exp_per_day_2x"] > 0),
            ("Enough trades for evidence",         wm["n_trades"] >= CMP_MIN_TRADES),
            ("Realistic fills (conservative)",     True),
            ("No leakage (firewall + walk-fwd)",   True),
            ("Paper trading confirms backtest",    None),
        ]
        print("\n  SUCCESS CONDITIONS")
        for name, ok in checks:
            mark = "…" if ok is None else ("✓" if ok else "✗")
            note = "  (pending — run paper mode)" if ok is None else ""
            print(f"    [{mark}] {name}{note}")
        passed = all(ok for _, ok in checks if ok is not None)
        print(f"\n  {'SYSTEM MEETS the offline success conditions — proceed to paper trading.' if passed else 'System does NOT yet meet the success conditions — the checklist above is the bottleneck list. Keep researching; do not trade this live.'}")

        # ---- reports -------------------------------------------------------
        out_dir = BASE_DIR / "result v2" / f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{**{"variant": n}, **{k: v for k, v in m.items()
                                            if not isinstance(v, dict)}}
                      for n, m in hold_rows]).to_csv(out_dir / "leaderboard_holdout.csv", index=False)
        pd.DataFrame([{**{"variant": n}, **{k: v for k, v in m.items()
                                            if not isinstance(v, dict)}}
                      for n, m in full_rows]).to_csv(out_dir / "leaderboard_fulltest.csv", index=False)
        for nm, log in daily_log.items():
            fn = out_dir / f"daily_selection_{nm.replace('/', '-').replace(' ', '_')}.jsonl"
            with open(fn, "w") as fh:
                for rec in log:
                    fh.write(json.dumps(rec, default=str) + "\n")
        for nm in ("1/day FORCED", "1/day selective"):
            pd.DataFrame([t for t in res[nm] if not t.get("no_signal")]
                         ).to_csv(out_dir / f"trades_{nm.replace('/', '-').replace(' ', '_')}.csv",
                                  index=False)
        with open(out_dir / "summary.md", "w") as fh:
            fh.write(_cmp_summary_md(best_entry, best_exit, best_rank, kept,
                                     hold_rows, full_rows, winner, checks,
                                     val_dates, hold_dates, cost_bps))
        print(f"\n  Saved: {out_dir.relative_to(BASE_DIR)}/ "
              f"(leaderboards, per-trade CSVs, daily selection JSONL, summary.md)")
        con.close()
        return {"best": {"entry": best_entry, "exit": best_exit, "rank": best_rank,
                         "filters": kept}, "holdout": dict(hold_rows), "winner": winner}
    finally:
        session_log.close()


def _cmp_summary_md(entry, exit_key, rank, filters, hold_rows, full_rows,
                    winner, checks, val_dates, hold_dates, cost_bps) -> str:
    def tbl(rows):
        keys = ["n_trades", "win_rate", "exp_per_trade", "exp_per_day",
                "exp_per_day_2x", "profit_factor", "max_dd",
                "longest_losing_streak", "worst_day", "worst_week",
                "sharpe", "sortino", "net_return_pct", "days_traded", "skipped_days"]
        out = ["| variant | " + " | ".join(keys) + " |",
               "|" + "---|" * (len(keys) + 1)]
        for n, m in rows:
            out.append("| " + n + " | " + " | ".join(str(m.get(k, "")) for k in keys) + " |")
        return "\n".join(out)

    monthly = "\n".join(f"- **{n}**: " + ", ".join(f"{k}: {v:+.2f}%" for k, v in m["monthly"].items())
                        for n, m in hold_rows if m.get("monthly"))
    regime = "\n".join(f"- **{n}**: " + ", ".join(f"{k}: {v['exp_per_day']:+.3f}%/day ({v['days']}d)"
                                                  for k, v in m["regime"].items())
                       for n, m in hold_rows if m.get("regime"))
    checklist = "\n".join(f"- [{'x' if ok else (' ' if ok is False else '~')}] {n}"
                          for n, ok in checks)
    return f"""# One-trade-per-day compare — summary

**Best combo (selected on validation {val_dates[0]}→{val_dates[-1]}):**
entry `{entry}` · exit `{exit_key}` · rank `{rank}` · filters `{filters or 'base'}` · costs {cost_bps} bps/side + surge slippage.

**Winner on holdout ({hold_dates[0]}→{hold_dates[-1]}):** `{winner}`

## Leaderboard — holdout (net of costs)
{tbl(hold_rows)}

## Reference — full test window
{tbl(full_rows)}

## Monthly net (holdout)
{monthly or '- n/a'}

## Regime split (SPY premarket)
{regime or '- n/a'}

## Success conditions
{checklist}

## Method notes (honesty)
- Sweeps were chosen ONLY on the validation slice; the leaderboard and verdict use the untouched holdout slice.
- All model scores are walk-forward (expanding window, retrained on strictly prior dates); the leakage firewall runs on every training call.
- Entries before 09:35 use a premarket-only model bank (no open5 features).
- Fills are conservative: same-bar TP+SL resolves as SL; gaps through a level fill at the bar open; forced flat 15:55 ET.
- The "agent-veto" variant uses a deterministic risk-rule proxy offline (risk-news / negative sentiment / SPY gap-down / parabolic gap); the live Claude agents (Anthropic API) remain the online version.
- Not testable from 1-min bars and therefore NOT claimed: real NBBO spread filter, premarket-VWAP filter (bars-only proxy), halt behaviour. Historical top-gainer universes carry survivorship/point-in-time risk — treat absolute levels with caution and confirm in paper mode.
"""


def run_train(rebuild: bool, retrain: bool, date_arg: str | None, today_et: datetime,
              verbose: bool = True):
    """MAMDOUH train: data pipeline + model training. No live execution."""
    session_log = start_session_log(today_et, "TRAIN")
    print(f"\n{'='*60}")
    print(f"  MAMDOUH TRAIN  —  {today_et.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    try:
        run_data_pipeline(rebuild=rebuild, date_arg=date_arg, verbose=verbose)
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
    rebuild   = "--rebuild"  in sys.argv
    retrain   = "--retrain"  in sys.argv
    do_train  = "--train"    in sys.argv
    do_live   = "--live"     in sys.argv
    do_bt     = "--backtest" in sys.argv
    do_search = "--search"   in sys.argv
    do_cmp    = "--compare"  in sys.argv
    verbose   = "--quiet"    not in sys.argv
    date_arg  = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    split_arg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--split=")), None)

    today_et = now_et().replace(hour=0, minute=0, second=0, microsecond=0)

    if do_cmp:
        cost_arg = next((a.split("=", 1)[1] for a in sys.argv
                         if a.startswith("--cost-bps=")), None)
        run_compare(date_arg, today_et, split=split_arg,
                    cost_bps=float(cost_arg) if cost_arg else 8.0,
                    quick="--quick" in sys.argv)
        return
    if do_search:
        run_model_search(date_arg, today_et, split=split_arg)
        return
    if do_bt:
        run_backtest(date_arg, today_et, split=split_arg)
        return
    if do_train and not do_live:
        run_train(rebuild, retrain, date_arg, today_et, verbose=verbose)
        return
    if do_live and not do_train:
        run_live_only(date_arg, today_et)
        return

    session_log = start_session_log(today_et, "LIVE")
    print(f"\n{'='*60}")
    print(f"  MAMDOUH  —  {today_et.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    try:
        run_data_pipeline(rebuild=rebuild, date_arg=date_arg, verbose=verbose)
        models, ranked, _ = run_training(retrain=retrain, date_arg=date_arg)
        run_live(today_et, models, ranked)
    finally:
        session_log.close()


if __name__ == "__main__":
    main()
