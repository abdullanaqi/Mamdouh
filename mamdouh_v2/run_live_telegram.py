"""Simulated live runner using Massive websocket prices.

The script selects top candidates from `candidates_scored`, waits for live
Massive websocket ticks, opens simulated positions, then monitors dynamic
TP/SL and the 15:55 time exit. It does NOT place real broker orders.

Every open/close is checkpointed to live_positions.json, so if the script
crashes or is stopped it can be restarted the same day: open trades are
resumed with their original entry/TP/SL, and tickers already traded today
are not re-opened.

Candidate sources (--source):
    auto     (default) DB picks if today's date is scored, else live gainers
    db       model-scored picks from candidates_scored (needs pipeline run)
    gainers  today's top halal gainers, live from the Massive snapshot API

Usage:
    python run_live_telegram.py 2026-07-01 --no-wait --top-n 1
    python run_live_telegram.py --top-n 5 --capital 10000
    python run_live_telegram.py --source gainers --top-n 5 --min-volume 250000

Environment:
    MASSIVE_API_KEY          Required for websocket auth.
    TELEGRAM_TOKEN           Optional; enables Telegram alerts.
    TELEGRAM_CHAT_IDS        Optional comma-separated chat IDs.
    MASSIVE_STOCKS_WS_URL    Optional; default wss://socket.massive.com/stocks
    LIVE_DRY_RUN             Always treated as true by this simulation runner.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from dotenv import load_dotenv

import config
import halal_gainers

_log = config.setup_logging(__name__)

NY_TZ = ZoneInfo("America/New_York")
DEFAULT_WS_URL = "wss://socket.massive.com/stocks"
DEFAULT_REST_URL = "https://api.massive.com"
                                                                            
                                                                         
STATE_PATH = os.path.join(config.BASE_DIR, "live_positions.json")


@dataclass
class Candidate:
    ticker: str
    rank_in_day: int
    model_entry_price: float
    vol_proxy: float
    ranking_score: float | None
    risk_score: float | None
    risk_percentile: float | None


@dataclass
class Position:
    ticker: str
    qty: int
    entry_price: float
    tp_price: float
    sl_price: float
    opened_at: datetime
    last_price: float
    high_price: float
    low_price: float
    closed: bool = False
    exit_price: float | None = None
    exit_reason: str | None = None
    closed_at: datetime | None = None

    @property
    def pnl(self) -> float:
        mark = self.exit_price if self.exit_price is not None else self.last_price
        return (mark - self.entry_price) * self.qty

    @property
    def pnl_pct(self) -> float:
        mark = self.exit_price if self.exit_price is not None else self.last_price
        return (mark - self.entry_price) / self.entry_price


def _position_to_dict(pos: Position) -> dict[str, Any]:
    return {
        "ticker": pos.ticker, "qty": pos.qty, "entry_price": pos.entry_price,
        "tp_price": pos.tp_price, "sl_price": pos.sl_price,
        "opened_at": pos.opened_at.isoformat(),
        "last_price": pos.last_price, "high_price": pos.high_price, "low_price": pos.low_price,
        "closed": pos.closed, "exit_price": pos.exit_price, "exit_reason": pos.exit_reason,
        "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
    }


def _position_from_dict(d: dict[str, Any]) -> Position:
    return Position(
        ticker=str(d["ticker"]), qty=int(d["qty"]), entry_price=float(d["entry_price"]),
        tp_price=float(d["tp_price"]), sl_price=float(d["sl_price"]),
        opened_at=datetime.fromisoformat(d["opened_at"]),
        last_price=float(d["last_price"]), high_price=float(d["high_price"]),
        low_price=float(d["low_price"]),
        closed=bool(d.get("closed", False)),
        exit_price=None if d.get("exit_price") is None else float(d["exit_price"]),
        exit_reason=d.get("exit_reason"),
        closed_at=datetime.fromisoformat(d["closed_at"]) if d.get("closed_at") else None,
    )


def _save_state(trade_date: date, positions: dict[str, Position]) -> None:
    state = {
        "trade_date": trade_date.isoformat(),
        "saved_at": datetime.now(NY_TZ).isoformat(),
        "positions": [_position_to_dict(p) for p in positions.values()],
    }
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)                                            


def _load_state(trade_date: date) -> dict[str, Position]:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("trade_date") != trade_date.isoformat():
            return {}                                              
        return {p["ticker"]: _position_from_dict(p) for p in state.get("positions", [])}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        _log.warning(f"Could not restore live state from {STATE_PATH}: {exc}")
        return {}


def _is_true(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def _send_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_TOKEN")
    chat_ids = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chat_ids:
        _log.info(message)
        return

    for chat_id in chat_ids:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as exc:
            _log.warning(f"Telegram send failed for chat_id={chat_id}: {exc}")


def _massive_api_key() -> str:
    api_key = os.getenv("MASSIVE_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY in .env")
    return api_key


def _massive_get_json(path: str, params: dict[str, str | int | float]) -> dict[str, Any]:
    api_key = _massive_api_key()
    base = os.getenv("MASSIVE_REST_URL", DEFAULT_REST_URL).rstrip("/")
    query = urllib.parse.urlencode(params)
    url = f"{base}{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _bar_time_ny(bar: dict[str, Any]) -> datetime | None:
    ts_ms = bar.get("t")
    if ts_ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts_ms) / 1000, tz=ZoneInfo("UTC")).astimezone(NY_TZ)
    except (TypeError, ValueError, OSError):
        return None


def _print_direct_historical_api(candidates: list[Candidate], trade_date: date, bars_to_print: int) -> None:
    _log.info(f"Historical API direct check for {trade_date} via Massive REST aggregates")
    for candidate in candidates:
        path = f"/v2/aggs/ticker/{candidate.ticker}/range/1/minute/{trade_date}/{trade_date}"
        try:
            payload = _massive_get_json(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        except Exception as exc:
            _log.warning(f"HIST API {candidate.ticker}: request failed: {exc}")
            continue

        bars = payload.get("results") or []
        if not bars:
            _log.warning(f"HIST API {candidate.ticker}: no minute bars returned")
            continue

        entry_bar = None
        for bar in bars:
            ts = _bar_time_ny(bar)
            if ts and ts.time().replace(second=0, microsecond=0) == time.fromisoformat(config.ENTRY_TIME):
                entry_bar = bar
                break

        last = bars[-1]
        high = max(float(b.get("h", 0) or 0) for b in bars)
        low = min(float(b.get("l", 0) or 0) for b in bars if b.get("l") is not None)
        entry_open = float(entry_bar["o"]) if entry_bar and entry_bar.get("o") is not None else None
        last_close = float(last["c"]) if last.get("c") is not None else None
        _log.info(
            f"HIST API {candidate.ticker}: bars={len(bars)} "
            f"entry_0935_open={_fmt_money(entry_open) if entry_open is not None else 'n/a'} "
            f"last_close={_fmt_money(last_close) if last_close is not None else 'n/a'} "
            f"day_range={_fmt_money(low)}-{_fmt_money(high)}"
        )

        for bar in bars[-bars_to_print:]:
            ts = _bar_time_ny(bar)
            _log.info(
                f"HIST BAR {candidate.ticker} "
                f"{ts:%Y-%m-%d %H:%M:%S %Z} "
                f"o={bar.get('o')} h={bar.get('h')} l={bar.get('l')} c={bar.get('c')} v={bar.get('v')}"
                if ts else
                f"HIST BAR {candidate.ticker} {bar}"
            )


def _latest_scored_date() -> date:
    try:
        con = duckdb.connect(config.OUTPUT_DB_PATH, read_only=True)
    except duckdb.IOException as exc:
        raise SystemExit(f"Could not open {config.OUTPUT_DB_PATH}. Close other DuckDB/Python sessions first: {exc}") from exc
    try:
        row = con.execute(
            "SELECT MAX(trade_date) FROM candidates_scored WHERE ranking_score IS NOT NULL"
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        raise SystemExit("No scored rows found. Run main.py through model stages first.")
    return row[0]


def _parse_trade_date(value: str | None) -> date:
    if not value:
        return _latest_scored_date()
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass

    for sep in ("-", "/"):
        parts = value.split(sep)
        if len(parts) == 2:
            month, day = (int(p) for p in parts)
            return date(datetime.now(NY_TZ).year, month, day)
    raise SystemExit(f"Invalid trade date '{value}'. Use YYYY-MM-DD or M-D, e.g. 2026-07-07 or 7-7.")


def _load_candidates(trade_date: date, top_n: int, risk_filter: bool) -> list[Candidate]:
    filter_sql = "(risk_score IS NULL OR risk_percentile < 0.75)" if risk_filter else "TRUE"
    try:
        con = duckdb.connect(config.OUTPUT_DB_PATH, read_only=True)
    except duckdb.IOException as exc:
        raise SystemExit(f"Could not open {config.OUTPUT_DB_PATH}. Close other DuckDB/Python sessions first: {exc}") from exc
    try:
        df = con.execute(
            f"""
            WITH ranked AS (
                SELECT
                    ticker,
                    ROW_NUMBER() OVER (ORDER BY ranking_score DESC) AS rank_in_day,
                    entry_price_0935,
                    {config.dynamic_vol_proxy_sql()} AS dynamic_vol_proxy,
                    ranking_score,
                    risk_score,
                    risk_percentile
                FROM candidates_scored
                WHERE trade_date = ?
                  AND ranking_score IS NOT NULL
                  AND {filter_sql}
            )
            SELECT * FROM ranked
            WHERE rank_in_day <= ?
            ORDER BY rank_in_day
            """,
            [trade_date, top_n],
        ).fetchdf()
    finally:
        con.close()

    if df.empty:
        raise SystemExit(f"No candidates found for {trade_date} with risk_filter={risk_filter}")

    out: list[Candidate] = []
    for _, r in df.iterrows():
        out.append(
            Candidate(
                ticker=str(r["ticker"]),
                rank_in_day=int(r["rank_in_day"]),
                model_entry_price=float(r["entry_price_0935"]),
                vol_proxy=float(r["dynamic_vol_proxy"]),
                ranking_score=None if pd.isna(r["ranking_score"]) else float(r["ranking_score"]),
                risk_score=None if pd.isna(r["risk_score"]) else float(r["risk_score"]),
                risk_percentile=None if pd.isna(r["risk_percentile"]) else float(r["risk_percentile"]),
            )
        )
    return out


def _opening_ranges_today(
    ticker: str, trade_date: date
) -> tuple[float | None, float | None, float | None, float | None]:
    """(high_0930_to_before_entry, low_0930_to_before_entry, premarket_high,
    premarket_low) from today's minute bars via REST; Nones fall back to the
    vol floor inside config.dynamic_vol_proxy."""
    path = f"/v2/aggs/ticker/{ticker}/range/1/minute/{trade_date}/{trade_date}"
    try:
        payload = _massive_get_json(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
    except Exception as exc:
        _log.warning(f"GAINERS {ticker}: minute-bar fetch failed ({exc}); using vol floor")
        return None, None, None, None

    open_t = time(9, 30)
    entry_t = time.fromisoformat(config.ENTRY_TIME)
    or_h = or_l = pm_h = pm_l = None
    for bar in payload.get("results") or []:
        ts = _bar_time_ny(bar)
        if ts is None or bar.get("h") is None or bar.get("l") is None:
            continue
        h, l = float(bar["h"]), float(bar["l"])
        t = ts.time()
        if open_t <= t < entry_t:
            or_h = h if or_h is None else max(or_h, h)
            or_l = l if or_l is None else min(or_l, l)
        elif t < open_t:
            pm_h = h if pm_h is None else max(pm_h, h)
            pm_l = l if pm_l is None else min(pm_l, l)
    return or_h, or_l, pm_h, pm_l


def _load_candidates_from_gainers(top_n: int, min_volume: float) -> list[Candidate]:
    with open(config.HALAL_TICKERS_PATH) as f:
        tickers = json.load(f)
    _log.info(f"Fetching live snapshots for {len(tickers)} halal tickers...")
    rows = halal_gainers.fetch_snapshots(tickers)
    eligible = [
        r for r in rows
        if r["last"] and r["last"] >= config.MIN_ENTRY_PRICE and r["day_v"] >= min_volume
    ]
    eligible.sort(key=lambda r: r["pct"], reverse=True)
    if not eligible:
        raise SystemExit(
            f"No live gainers passed filters "
            f"(price >= {config.MIN_ENTRY_PRICE}, volume >= {min_volume:,.0f})"
        )

    today = datetime.now(NY_TZ).date()
    out: list[Candidate] = []
    for rank, r in enumerate(eligible[:top_n], 1):
        or_h, or_l, pm_h, pm_l = _opening_ranges_today(r["ticker"], today)
        vol = config.dynamic_vol_proxy(float(r["last"]), or_h, or_l, pm_h, pm_l)
        _log.info(
            f"GAINER #{rank} {r['ticker']}: chg={r['pct']:+.2f}% last={_fmt_money(float(r['last']))} "
            f"vol_proxy={vol:.2%} volume={r['day_v']:,.0f}"
        )
        out.append(
            Candidate(
                ticker=str(r["ticker"]),
                rank_in_day=rank,
                model_entry_price=float(r["last"]),
                vol_proxy=vol,
                ranking_score=None,
                risk_score=None,
                risk_percentile=None,
            )
        )
    return out


def _position_from_tick(candidate: Candidate, price: float, capital_per_trade: float) -> Position | None:
    if price <= 0:
        return None
    qty = int(capital_per_trade // price)
    if qty <= 0:
        return None
    tp_price = price * (1 + config.DYNAMIC_TP_VOL_MULT * candidate.vol_proxy)
    sl_price = price * (1 - config.DYNAMIC_SL_VOL_MULT * candidate.vol_proxy)
    now = datetime.now(NY_TZ)
    return Position(
        ticker=candidate.ticker,
        qty=qty,
        entry_price=price,
        tp_price=tp_price,
        sl_price=sl_price,
        opened_at=now,
        last_price=price,
        high_price=price,
        low_price=price,
    )


def _extract_events(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _event_symbol(event: dict[str, Any]) -> str | None:
    for key in ("sym", "symbol", "ticker", "T"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value.upper()
    return None


def _event_price(event: dict[str, Any]) -> float | None:
    bid = event.get("bp")
    ask = event.get("ap")
    if bid is not None and ask is not None:
        try:
            bid_f, ask_f = float(bid), float(ask)
            if bid_f > 0 and ask_f > 0:
                return (bid_f + ask_f) / 2
        except (TypeError, ValueError):
            pass

    for key in ("p", "price", "c", "close", "ap"):
        value = event.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _compact_live_event(event: dict[str, Any], ticker: str, price: float) -> str:
    ev = event.get("ev", "?")
    if ev == "Q":
        return (
            f"LIVE DATA ev=Q ticker={ticker} mid={_fmt_money(price)} "
            f"bid={event.get('bp')} ask={event.get('ap')} bid_size={event.get('bs')} ask_size={event.get('as')}"
        )
    if ev == "AM":
        return (
            f"LIVE DATA ev=AM ticker={ticker} close={_fmt_money(price)} "
            f"o={event.get('o')} h={event.get('h')} l={event.get('l')} v={event.get('v')}"
        )
    return f"LIVE DATA ev={ev} ticker={ticker} price={_fmt_money(price)} size={event.get('s')} raw_t={event.get('t')}"


def _market_exit_time(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time.fromisoformat(config.EXIT_TIME), tzinfo=NY_TZ)


def _open_position(candidate: Candidate, price: float, capital_per_trade: float) -> Position | None:
    pos = _position_from_tick(candidate, price, capital_per_trade)
    if pos is None:
        _send_telegram(f"SKIP {candidate.ticker}: capital too small for price {_fmt_money(price)}")
        return None
    _send_telegram(
        "SIM OPEN "
        f"{pos.ticker} qty={pos.qty} entry={_fmt_money(pos.entry_price)} "
        f"tp={_fmt_money(pos.tp_price)} sl={_fmt_money(pos.sl_price)} "
        f"vol={candidate.vol_proxy:.2%}"
    )
    return pos


def _close_position(pos: Position, price: float, reason: str) -> None:
    if pos.closed:
        return
    pos.closed = True
    pos.exit_price = price
    pos.exit_reason = reason
    pos.closed_at = datetime.now(NY_TZ)
    _send_telegram(
        "SIM CLOSE "
        f"{pos.ticker} reason={reason} exit={_fmt_money(price)} "
        f"pnl={_fmt_money(pos.pnl)} ({pos.pnl_pct:+.2%})"
    )


def _monitor_line(positions: dict[str, Position], unopened: set[str], message_count: int) -> str:
    open_positions = [p for p in positions.values() if not p.closed]
    total_pnl = sum(p.pnl for p in open_positions)
    if not open_positions:
        waiting = ", ".join(sorted(unopened)) if unopened else "none"
        return f"MONITOR messages={message_count} open=0 waiting={waiting}"

    parts = []
    for p in open_positions:
        to_tp = (p.tp_price - p.last_price) / p.last_price if p.last_price else 0.0
        to_sl = (p.last_price - p.sl_price) / p.last_price if p.last_price else 0.0
        parts.append(
            f"{p.ticker} last={_fmt_money(p.last_price)} pnl={_fmt_money(p.pnl)} "
            f"({p.pnl_pct:+.2%}) tp_left={to_tp:.2%} sl_room={to_sl:.2%}"
        )
    return f"MONITOR messages={message_count} open={len(open_positions)} total_pnl={_fmt_money(total_pnl)} | " + " | ".join(parts)


async def _run_massive_ws(
    candidates: list[Candidate],
    capital: float,
    trade_date: date,
    no_wait: bool,
    status_seconds: int,
    print_live_data: bool,
    live_print_limit: int,
) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit(
            "Missing websocket dependency. Install it with: "
            ".venv\\Scripts\\python.exe -m pip install websockets"
        ) from exc

    api_key = _massive_api_key()

    ws_url = os.getenv("MASSIVE_STOCKS_WS_URL", DEFAULT_WS_URL)
    candidate_by_ticker = {c.ticker: c for c in candidates}

    positions = _load_state(trade_date)
    if positions:
        n_open = sum(not p.closed for p in positions.values())
        _send_telegram(
            f"SIM RESUME {trade_date}: restored {n_open} open / "
            f"{len(positions) - n_open} closed position(s) from {os.path.basename(STATE_PATH)}"
        )
        for p in positions.values():
            if not p.closed:
                _log.info(
                    f"RESUMED {p.ticker} qty={p.qty} entry={_fmt_money(p.entry_price)} "
                    f"tp={_fmt_money(p.tp_price)} sl={_fmt_money(p.sl_price)}"
                )

                                                                        
                                                                          
    tickers = [c.ticker for c in candidates]
    tickers += [t for t in positions if t not in candidate_by_ticker]
    unopened = set(candidate_by_ticker) - set(positions)
    capital_per_trade = capital / max(len(candidates), 1)
                                                                              
                                                                             
                                                   
    exit_time = _market_exit_time(max(trade_date, datetime.now(NY_TZ).date()))
    _log.info(f"Time exit scheduled at {exit_time:%Y-%m-%d %H:%M %Z}")

    _send_telegram(
        f"SIM LIVE START {trade_date} tickers={', '.join(tickers)} "
        f"capital={_fmt_money(capital)} dry_run=true"
    )

    if not no_wait:
        entry_time = datetime.combine(trade_date, time.fromisoformat(config.ENTRY_TIME), tzinfo=NY_TZ)
        while datetime.now(NY_TZ) < entry_time:
            await asyncio.sleep(1)

    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({"action": "auth", "params": api_key}))
        subscribe = ",".join(
            [f"T.{ticker}" for ticker in tickers]
            + [f"Q.{ticker}" for ticker in tickers]
            + [f"AM.{ticker}" for ticker in tickers]
        )
        await ws.send(json.dumps({"action": "subscribe", "params": subscribe}))
        _log.info(f"Subscribed to {subscribe}")
        _log.info("Waiting for first live tick/quote/aggregate to open simulated positions...")

        last_heartbeat = datetime.now(NY_TZ)
        message_count = 0
        live_printed_by_ticker = {ticker: 0 for ticker in tickers}
        while True:
            now = datetime.now(NY_TZ)
            if now >= exit_time:
                for pos in positions.values():
                    if not pos.closed:
                        _close_position(pos, pos.last_price, "time")
                _save_state(trade_date, positions)
                break
            if positions and all(p.closed for p in positions.values()) and not unopened:
                break

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                hb_now = datetime.now(NY_TZ)
                if (hb_now - last_heartbeat).total_seconds() >= status_seconds:
                    _log.info(_monitor_line(positions, unopened, message_count))
                    last_heartbeat = hb_now
                continue

            for event in _extract_events(raw):
                message_count += 1
                if event.get("ev") == "status":
                    _log.info(f"Massive status: {event.get('status')} {event.get('message', '')}".strip())
                    continue

                ticker = _event_symbol(event)
                price = _event_price(event)
                if (ticker not in candidate_by_ticker and ticker not in positions) or price is None:
                    continue

                if print_live_data and live_printed_by_ticker[ticker] < live_print_limit:
                    _log.info(_compact_live_event(event, ticker, price))
                    live_printed_by_ticker[ticker] += 1

                if ticker in unopened:
                    pos = _open_position(candidate_by_ticker[ticker], price, capital_per_trade)
                    unopened.remove(ticker)
                    if pos is not None:
                        positions[ticker] = pos
                        _save_state(trade_date, positions)
                    continue

                pos = positions.get(ticker)
                if pos is None or pos.closed:
                    continue

                pos.last_price = price
                pos.high_price = max(pos.high_price, price)
                pos.low_price = min(pos.low_price, price)
                if price <= pos.sl_price:
                    _close_position(pos, price, "sl")
                    _save_state(trade_date, positions)
                elif price >= pos.tp_price:
                    _close_position(pos, price, "tp")
                    _save_state(trade_date, positions)

                status_now = datetime.now(NY_TZ)
                if (status_now - last_heartbeat).total_seconds() >= status_seconds:
                    _log.info(_monitor_line(positions, unopened, message_count))
                    last_heartbeat = status_now

    total_pnl = sum(pos.pnl for pos in positions.values())
    _save_state(trade_date, positions)
    _send_telegram(f"SIM LIVE DONE positions={len(positions)} total_pnl={_fmt_money(total_pnl)}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("trade_date", nargs="?", help="Trade date YYYY-MM-DD or M-D; default latest scored date")
    p.add_argument("--source", choices=["auto", "db", "gainers"], default="auto",
                   help="Candidate source: db=model picks from candidates_scored, gainers=today's live halal gainers, auto=db if today is scored else gainers")
    p.add_argument("--min-volume", type=float, default=float(os.getenv("LIVE_MIN_VOLUME", "100000")),
                   help="Gainers mode: minimum day share volume")
    p.add_argument("--top-n", type=int, default=int(os.getenv("LIVE_TOP_N", "5")))
    p.add_argument("--capital", type=float, default=float(os.getenv("LIVE_INITIAL_CAPITAL", "10000")))
    p.add_argument("--risk-filter", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--no-wait", action="store_true", help="Open on first tick immediately instead of waiting for 09:35 NY")
    p.add_argument("--status-seconds", type=int, default=int(os.getenv("LIVE_STATUS_SECONDS", "30")))
    p.add_argument("--historical-direct", action=argparse.BooleanOptionalAction, default=True, help="Print direct Massive REST historical minute bars before websocket start")
    p.add_argument("--historical-bars", type=int, default=3, help="How many latest historical bars per ticker to print")
    p.add_argument("--print-live-data", action=argparse.BooleanOptionalAction, default=True, help="Print compact decoded websocket events")
    p.add_argument("--live-print-limit", type=int, default=20, help="Max live websocket events to print per ticker")
    return p.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    live_now = datetime.now(NY_TZ)

    source = args.source
    if source == "auto":
        if args.trade_date:
            source = "db"
        else:
            try:
                source = "db" if _latest_scored_date() == live_now.date() else "gainers"
            except SystemExit:
                source = "gainers"
            if source == "gainers":
                _log.info(
                    "Source auto: candidates_scored has no picks for today's NY date -> "
                    "using live halal gainers from the snapshot API"
                )

    if source == "gainers":
        trade_date = live_now.date()
        candidates = _load_candidates_from_gainers(args.top_n, args.min_volume)
    else:
        trade_date = _parse_trade_date(args.trade_date)
        candidates = _load_candidates(trade_date, args.top_n, args.risk_filter)

    _log.info(
        f"Simulation only: real orders are disabled. "
        f"Candidate source={source}; candidate date={trade_date}; "
        f"live stream date/time NY={live_now:%Y-%m-%d %H:%M:%S}."
    )
    if trade_date != live_now.date():
        _log.warning(
            f"Candidate date {trade_date} is not today's NY date {live_now.date()}. "
            "The websocket prices are live now, but picks come from the saved database date."
        )
    _log.info(f"Loaded {len(candidates)} candidates.")
    for c in candidates:
        _log.info(
            f"#{c.rank_in_day} {c.ticker}: model_entry={_fmt_money(c.model_entry_price)} "
            f"vol={c.vol_proxy:.2%} ranking={c.ranking_score}"
        )

    if args.historical_direct:
        _print_direct_historical_api(candidates, trade_date, args.historical_bars)

    return asyncio.run(
        _run_massive_ws(
            candidates,
            args.capital,
            trade_date,
            args.no_wait,
            args.status_seconds,
            args.print_live_data,
            args.live_print_limit,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
