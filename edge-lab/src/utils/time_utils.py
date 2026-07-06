from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import CFG

ET = ZoneInfo(CFG.session.tz)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def ts_et(d: date | str, hhmm: str) -> pd.Timestamp:
    """Timezone-aware ET timestamp for a given date + 'HH:MM'."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    t = parse_hhmm(hhmm)
    return pd.Timestamp(datetime(d.year, d.month, d.day, t.hour, t.minute), tz=ET)


def rth_open(d) -> pd.Timestamp:
    return ts_et(d, CFG.session.rth_open)


def rth_close(d) -> pd.Timestamp:
    return ts_et(d, CFG.session.rth_close)


def premarket_open(d) -> pd.Timestamp:
    return ts_et(d, CFG.session.premarket_open)


def is_rth(ts: pd.Timestamp) -> bool:
    t = ts.tz_convert(ET).time()
    return parse_hhmm(CFG.session.rth_open) <= t < parse_hhmm(CFG.session.rth_close)


def minute_of_session(ts: pd.Timestamp) -> int:
    """Minutes since RTH open (0 at 09:30)."""
    o = rth_open(ts.tz_convert(ET).date())
    return int((ts - o).total_seconds() // 60)


# Static intraday cumulative-volume curve (assumption, documented in
# README_TRUTH.md). Used only to normalize relative volume; contains no
# future information about any specific day.
_CURVE_POINTS = [(0, 0.00), (5, 0.030), (15, 0.075), (30, 0.13), (60, 0.20),
                 (120, 0.32), (180, 0.43), (240, 0.53), (300, 0.65),
                 (330, 0.72), (360, 0.82), (375, 0.89), (390, 1.00)]


def expected_cum_vol_frac(minutes_since_open: int) -> float:
    m = max(0, min(390, minutes_since_open))
    for (m0, f0), (m1, f1) in zip(_CURVE_POINTS[:-1], _CURVE_POINTS[1:]):
        if m0 <= m <= m1:
            if m1 == m0:
                return f1
            return f0 + (f1 - f0) * (m - m0) / (m1 - m0)
    return 1.0
