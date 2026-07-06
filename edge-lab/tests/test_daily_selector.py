import pandas as pd

from src.selector.daily_trade_selector import select_for_date
from src.utils.time_utils import ts_et
from tests.conftest import flat_bars, panel_row

D = "2025-06-02"


def _cand(ticker, ev, score, **kw):
    base = {"ticker": ticker, "date": pd.Timestamp(D),
            "decision_ts": ts_et(D, "10:00"), "last_price": 50.0,
            "tp_pct": 0.03, "sl_pct": 0.015, "ev": ev, "p_win": 0.6,
            "score": score, "med_dollar_vol_20d_prev": 5e7,
            "spread_proxy_bps_prev": 10.0, "atr_pct_prev": 0.03,
            "ev_evidence_n": 300, "suspect_split": False,
            "gap_pct": 0.04, "rvol": 3.0, "dist_vwap": 0.01,
            "exit_reasoning": "test levels"}
    base.update(kw)
    return base


class FakeStrategy:
    def __init__(self, cands):
        self.cands = cands

    def candidates_for_day(self, view):
        return list(self.cands)


def _loader(d):
    return flat_bars(D, "AAA", "09:30", "10:30")


PANEL = panel_row(D, "AAA")


def test_selects_best_positive_ev_and_reports_rejections():
    s = FakeStrategy([_cand("AAA", 0.012, 2.0), _cand("BBB", 0.006, 1.5),
                      _cand("CCC", -0.002, 1.0)])
    pick = select_for_date(s, D, PANEL, minute_loader=_loader, write=False)
    assert not pick.forced and not pick.no_candidates
    assert pick.selected["ticker"] == "AAA"
    assert len(pick.rejected) == 2
    md = pick.to_markdown()
    assert "FORCED" not in md
    assert "Rejected alternatives" in md and "BBB" in md


def test_forced_warning_when_no_positive_ev():
    s = FakeStrategy([_cand("AAA", -0.004, 1.2), _cand("BBB", -0.01, 0.9)])
    pick = select_for_date(s, D, PANEL, minute_loader=_loader, write=False)
    assert pick.forced
    md = pick.to_markdown()
    assert "FORCED TRADE WARNING" in md
    assert "least-bad candidate" in md


def test_gate_failure_forces_even_with_positive_ev():
    thin = _cand("ZZZ", 0.02, 3.0, med_dollar_vol_20d_prev=1e5)  # illiquid
    pick = select_for_date(FakeStrategy([thin]), D, PANEL,
                           minute_loader=_loader, write=False)
    assert pick.forced  # positive EV but failed liquidity gate -> forced path


def test_no_candidates_is_a_valid_output():
    pick = select_for_date(FakeStrategy([]), D, PANEL,
                           minute_loader=_loader, write=False)
    assert pick.no_candidates
    assert "No candidates" in pick.to_markdown()


def test_exactly_one_selection():
    s = FakeStrategy([_cand(t, 0.01, sc) for t, sc in
                      [("AAA", 2.0), ("BBB", 1.9), ("CCC", 1.8), ("DDD", 1.7), ("EEE", 1.6)]])
    pick = select_for_date(s, D, PANEL, minute_loader=_loader, write=False)
    assert pick.selected["ticker"] == "AAA"
    assert len(pick.rejected) == 3  # capped at 3 reported alternatives
