"""DailyOrchestrator integration tests on synthetic bars."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from halal_gap.backtest.engine import DailyBars
from halal_gap.catalyst.models import ClassifierOutput, ClassifierResult
from halal_gap.execution import ledger
from halal_gap.execution.broker import PaperBroker
from halal_gap.execution.kill_switch import KillSwitch
from halal_gap.execution.runner import DailyOrchestrator
from halal_gap.universe.builder import UniverseRow

NY = ZoneInfo("America/New_York")


def _intraday_for(d: date, *, gap: float = 0.04, body_dir: int = 1, open_vol: int = 900_000) -> pd.DataFrame:
    rng = np.random.default_rng(int(d.strftime("%Y%m%d")))
    pre = 100.0 * (1 + gap)
    rows: list[dict] = []
    cur = datetime.combine(d, time(4, 0), tzinfo=NY)
    end = datetime.combine(d, time(16, 0), tzinfo=NY)
    price = pre
    while cur < end:
        if cur.time() < time(9, 30):
            o, c = price, price + rng.normal(0, 0.05)
            vol = 120_000
        elif cur.time() == time(9, 30):
            o = price
            c = price + body_dir * (0.4 + abs(rng.normal(0, 0.1)))
            vol = open_vol
        else:
            o = price
            c = price + body_dir * 0.05 + rng.normal(0, 0.08)
            vol = 50_000
        h, l = max(o, c) + 0.05, min(o, c) - 0.05
        rows.append(
            {"date": cur.strftime("%Y-%m-%d %H:%M:%S"),
             "open": round(o, 4), "high": round(h, 4),
             "low": round(l, 4), "close": round(c, 4), "volume": vol}
        )
        price = c
        cur += timedelta(minutes=5)
    return pd.DataFrame(rows)


def _daily(d: date) -> pd.DataFrame:
    rows = []
    cur = d - timedelta(days=60)
    while cur <= d:
        if cur.weekday() < 5:
            rows.append(
                {"date": cur.isoformat(),
                 "open": 99.5, "high": 101.5, "low": 98.5,
                 "close": 100.0, "volume": 50_000_000}
            )
        cur += timedelta(days=1)
    return pd.DataFrame(rows)


@pytest.fixture
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ledger, "_trades_path", lambda: tmp_path / "paper.parquet")
    monkeypatch.setattr(ledger, "_equity_path", lambda: tmp_path / "paper_equity.parquet")
    return tmp_path


def _build_universe_bars(symbols: list[str], d: date) -> tuple[list[UniverseRow], dict]:
    """Build a multi-day intraday history so the scanner has lookback data."""
    sessions: list[date] = []
    cur = d - timedelta(days=14)
    while cur <= d:
        if cur.weekday() < 5:
            sessions.append(cur)
        cur += timedelta(days=1)
    bars: dict[str, DailyBars] = {}
    for s in symbols:
        intras = [
            _intraday_for(
                sess,
                gap=0.04 if sess == d else 0.0,
                body_dir=1 if sess == d else 0,
                open_vol=900_000 if sess == d else 100_000,
            )
            for sess in sessions
        ]
        bars[s] = DailyBars(intraday=pd.concat(intras, ignore_index=True), daily=_daily(d))
    rows = [
        UniverseRow(
            symbol=s, as_of=d, prior_close=100.0,
            dollar_volume_20d=120_000_000.0, atr_pct=0.015,
            sector="Technology", industry="Software",
        )
        for s in symbols
    ]
    return rows, bars


def test_runner_clean_day_produces_trades(isolated_ledger: Path) -> None:
    d = date(2024, 1, 22)
    universe, bars = _build_universe_bars(["AAA", "BBB"], d)
    orch = DailyOrchestrator(broker=PaperBroker(starting_nav=100_000.0), persist=True)
    report = orch.run(d, universe, bars)
    assert report.kill.tripped is False
    assert len(report.candidates) >= 1
    assert len(report.accepted_setups) <= 5
    assert len(report.trade_results) <= 5


def test_runner_kill_switch_blocks_entries(isolated_ledger: Path) -> None:
    """Pre-seed an equity log that triggers consec losing days; new entries must be blocked."""
    base = pd.Timestamp("2024-01-15")
    losses = pd.DataFrame(
        {
            "date": [base + pd.Timedelta(days=i) for i in range(4)],
            "equity": [100_000, 99_000, 98_000, 97_000],
        }
    )
    eq_path = isolated_ledger / "paper_equity.parquet"
    losses.to_parquet(eq_path, index=False)

    d = date(2024, 1, 22)
    universe, bars = _build_universe_bars(["AAA"], d)
    orch = DailyOrchestrator(broker=PaperBroker(starting_nav=97_000.0))
    report = orch.run(d, universe, bars)
    assert report.kill.tripped is True
    assert report.accepted_setups == []
    assert report.trade_results == []
    # NAV remains unchanged because no trades ran.
    assert report.nav == pytest.approx(97_000.0)


def test_runner_catalyst_gate_filters(isolated_ledger: Path) -> None:
    d = date(2024, 1, 22)
    universe, bars = _build_universe_bars(["AAA"], d)
    orch = DailyOrchestrator(
        broker=PaperBroker(starting_nav=100_000.0),
        use_catalyst_gate=True,
        persist=False,
    )
    # Empty catalysts map -> the gate must reject everything.
    report = orch.run(d, universe, bars, catalysts={})
    assert report.trade_results == []
    assert any("catalyst_missing" in v for v in report.reasons_rejected.values())


def test_runner_catalyst_gate_accepts_bullish(isolated_ledger: Path) -> None:
    d = date(2024, 1, 22)
    universe, bars = _build_universe_bars(["AAA"], d)
    bull = ClassifierResult(
        symbol="AAA",
        as_of=datetime(2024, 1, 22, 9, 25, tzinfo=NY),
        prompt_version="v1", item_count=2,
        output=ClassifierOutput(
            direction="bullish", strength=4, confidence=0.8,
            catalyst_type="earnings", summary="t",
        ),
    )
    orch = DailyOrchestrator(
        broker=PaperBroker(starting_nav=100_000.0),
        use_catalyst_gate=True,
        persist=False,
    )
    report = orch.run(d, universe, bars, catalysts={"AAA": bull})
    # Should not have rejected for missing catalyst
    assert "catalyst_missing" not in report.reasons_rejected.get("AAA", "")


def test_runner_ml_gate_threshold(isolated_ledger: Path) -> None:
    """Threshold = 1.01 (impossible) -> ML gate rejects everything."""
    d = date(2024, 1, 22)
    universe, bars = _build_universe_bars(["AAA"], d)

    class AlwaysHighModel:
        @property
        def feature_cols(self):
            from halal_gap.features import feature_columns
            return feature_columns()
        def predict_proba(self, X):
            return np.zeros(len(X)) + 0.4   # below 0.55 default

    orch = DailyOrchestrator(
        broker=PaperBroker(starting_nav=100_000.0),
        scoring_model=AlwaysHighModel(),  # type: ignore[arg-type]
        persist=False,
    )
    report = orch.run(d, universe, bars)
    assert report.trade_results == []
    assert any("ml_below_threshold" in v for v in report.reasons_rejected.values())
