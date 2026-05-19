"""Shared fixtures: synthetic 5-min and daily bars for deterministic tests."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

NY = ZoneInfo("America/New_York")


def _session_bars(d: date, *, pre_vol: int = 50_000, body_dir: int = 1, gap: float = 0.03) -> pd.DataFrame:
    """Build a deterministic single-day 5-min bar set: 04:00 -> 16:00."""
    rng = np.random.default_rng(int(d.strftime("%Y%m%d")))
    prior_close = 100.0
    pre_price = prior_close * (1 + gap)
    bars: list[dict[str, object]] = []
    cur = datetime.combine(d, time(4, 0), tzinfo=NY)
    end = datetime.combine(d, time(16, 0), tzinfo=NY)
    price = pre_price
    while cur < end:
        # Premarket 04:00 - 09:30: build pre-market volume
        if cur.time() < time(9, 30):
            vol = pre_vol
            o = price
            c = price + rng.normal(0, 0.02)
            h = max(o, c) + 0.05
            l = min(o, c) - 0.05
            price = c
        elif cur.time() == time(9, 30):
            # opening 5-min candle: tight body in body_dir direction
            o = price
            c = price + body_dir * 0.50
            h = max(o, c) + 0.05
            l = min(o, c) - 0.05
            vol = 800_000  # massive open volume -> rvol >> 1
            price = c
        else:
            o = price
            c = price + body_dir * 0.05 + rng.normal(0, 0.02)
            h = max(o, c) + 0.05
            l = min(o, c) - 0.05
            vol = 40_000
            price = c
        bars.append(
            {
                "date": cur.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "volume": vol,
            }
        )
        cur += timedelta(minutes=5)
    return pd.DataFrame(bars)


@pytest.fixture
def synthetic_intraday() -> pd.DataFrame:
    """Three trading days of 5-min bars for a fictional ticker."""
    days = [date(2024, 1, 22), date(2024, 1, 23), date(2024, 1, 24)]
    frames = []
    for i, d in enumerate(days):
        frames.append(_session_bars(d, pre_vol=80_000, body_dir=1 if i == 2 else 0, gap=0.05 if i == 2 else 0.0))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def synthetic_daily() -> pd.DataFrame:
    """30 daily bars approximating a $100 stock with ~1.5% ATR."""
    base = date(2024, 1, 1)
    rows: list[dict[str, object]] = []
    price = 100.0
    rng = np.random.default_rng(7)
    for i in range(30):
        d = base + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        o = price
        c = price + rng.normal(0, 0.5)
        h = max(o, c) + 1.0
        l = min(o, c) - 1.0
        rows.append({"date": d.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": 30_000_000})
        price = c
    return pd.DataFrame(rows)
