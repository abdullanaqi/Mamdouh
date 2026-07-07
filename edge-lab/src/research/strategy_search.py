"""Strategy definitions + search grids.

A Strategy must implement:
    fit(train_dates, daily_panel, minute_loader) -> self
    candidates_for_day(view) -> list[dict]   # scored, pre-decision only

Candidate dict keys: ticker, decision_ts, score, ev, p_win, tp_pct,
sl_pct (+ feature values for reporting).

Rules for every strategy here:
- Candidate gates and scores use ONLY PointInTimeView data.
- fit() may only see train dates; internal tuning must use an internal
  tail-of-train validation split, never the caller's test window.

Caching: feature/label rows for a given (date, decision_time) are
deterministic, so they are memoized per process. This assumes minute data
for a date is immutable within a run (true everywhere except the leakage
perturbation tests, which bypass this module on purpose). For full-market
real-data runs, persist rows to data/features/ instead of relying on the
in-memory cache -- see NEXT_STEPS.md.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from src.config import CFG
from src.data import feature_cache
from src.data.feature_builder import PointInTimeView, compute_features
from src.data.labeler import label_candidate
from src.data.universe_builder import universe_for_date
from src.models.dynamic_exit_model import DynamicExitModel, empirical_first_touch
from src.models.train_expected_value_model import ExpectedValueModel
from src.models.train_probability_model import WinProbabilityModel
from src.models.train_ranker import rank_score
from src.utils.logging import get_logger
from src.utils.time_utils import ts_et

log = get_logger(__name__)

_FEATURE_CACHE: dict = {}   # (date_str, hhmm) -> list[dict] features, ALL universe tickers
_ROW_CACHE: dict = {}       # (date_str, hhmm) -> list[dict] features+labels, ALL universe tickers


_TRAIN_FRAME_MEMO: dict = {}  # (gate_key, times, dates_sig) -> DataFrame
_EXIT_MODEL_MEMO: dict = {}   # same key -> fitted DynamicExitModel
_MEMO_MAX = 6


def clear_caches() -> None:
    _FEATURE_CACHE.clear()
    _ROW_CACHE.clear()
    _TRAIN_FRAME_MEMO.clear()
    _EXIT_MODEL_MEMO.clear()
    feature_cache.clear_memory()


def _memo_put(memo: dict, key, val) -> None:
    memo[key] = val
    while len(memo) > _MEMO_MAX:
        del memo[next(iter(memo))]


def _dates_sig(dates) -> tuple:
    dates = list(dates)
    return (len(dates), str(dates[0]), str(dates[-1])) if dates else (0,)


def cost_haircut(last_price: float, sl_pct: float) -> float:
    """Round-trip slippage + fees expressed as a return fraction, using the
    same sizing the simulator will use."""
    from src.backtest.execution_simulator import size_position
    slip = 2 * CFG.costs.one_way_bps(last_price) / 10_000.0 + CFG.costs.stop_extra_slippage_bps / 10_000.0
    shares = size_position(last_price, max(sl_pct, 1e-4), bar_dollar_vol=float("inf"))
    fee_frac = CFG.costs.fee_round_trip_usd / (last_price * shares) if shares > 0 else 0.02
    return slip + fee_frac


def _all_features(view: PointInTimeView, daily_panel: pd.DataFrame, hhmm: str) -> list[dict]:
    key = (str(view.date.date()), hhmm)
    if key in _FEATURE_CACHE:
        return _FEATURE_CACHE[key]
    disk = feature_cache.load_features(view.date, hhmm)
    if disk is not None:
        return disk  # LRU-bounded in feature_cache; skip unbounded dict
    uni = universe_for_date(daily_panel, view.date)
    tickers = set(uni["ticker"]) & set(view.tickers())
    dts = ts_et(view.date.date(), hhmm)
    out = []
    for t in tickers:
        f = compute_features(view, t, dts)
        if f is not None:
            out.append(f)
    _FEATURE_CACHE[key] = out
    return out


def generate_candidates(view: PointInTimeView, daily_panel: pd.DataFrame,
                        decision_times: list[str], gate) -> list[dict]:
    """Universe -> features -> gate. `gate(features)->bool`."""
    out = []
    for hhmm in decision_times:
        out.extend(f for f in _all_features(view, daily_panel, hhmm) if gate(f))
    return out


def _rows_for_day(view: PointInTimeView, daily_panel: pd.DataFrame, hhmm: str) -> list[dict]:
    key = (str(view.date.date()), hhmm)
    if key in _ROW_CACHE:
        return _ROW_CACHE[key]
    disk = feature_cache.load_rows(view.date, hhmm)
    if disk is not None:
        return disk
    rows = []
    for f in _all_features(view, daily_panel, hhmm):
        atr = f.get("atr_pct_prev")
        if atr is None or (isinstance(atr, float) and math.isnan(atr)) or atr <= 0:
            atr = 0.02
        lab = label_candidate(view, f["ticker"], f["decision_ts"],
                              ref_tp_pct=1.5 * atr, ref_sl_pct=1.0 * atr)
        if lab is not None:
            rows.append({**f, **lab})
    _ROW_CACHE[key] = rows
    return rows


def build_training_frame(dates, daily_panel, decision_times, gate,
                         minute_loader) -> pd.DataFrame:
    """Features + labels for all gated candidates over `dates`. Labels use
    a per-candidate ATR reference exit (1.5x / 1.0x ATR)."""
    rows = []
    for d in dates:
        if feature_cache.has_rows(d, decision_times):
            for hhmm in decision_times:
                rows.extend(r for r in feature_cache.load_rows(d, hhmm) if gate(r))
            continue
        mdf = minute_loader(d)
        if mdf is None or len(mdf) == 0:
            continue
        view = PointInTimeView.build(d, mdf, daily_panel)
        for hhmm in decision_times:
            rows.extend(r for r in _rows_for_day(view, daily_panel, hhmm) if gate(r))
    df = pd.DataFrame(rows)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
    return df


# --------------------------- rule strategy ---------------------------

class GapRvolStrategy:
    """Gap-up continuation with relative-volume confirmation, optional
    opening-range breakout requirement. TP/SL from the dynamic exit model;
    EV/P(win) estimated empirically on training labels at those levels."""

    PARAM_GRID = {
        "decision_time": ["09:45", "10:00", "10:30"],
        "gap_min": [0.01, 0.02, 0.04],
        "gap_max": [0.15, 0.30],
        "rvol_min": [1.5, 2.5, 4.0],
        "require_orb": [False, True],
        "require_above_vwap": [True],
    }

    def __init__(self, **params):
        self.p = {**{k: v[0] for k, v in self.PARAM_GRID.items()}, **params}
        self.exit_model = DynamicExitModel()
        self.train_labels: pd.DataFrame | None = None
        self.stability = 0.5
        self.n_trials_burned = 0

    def _gate(self, f: dict) -> bool:
        p = self.p
        g = f.get("gap_pct")
        if g is None or (isinstance(g, float) and math.isnan(g)):
            return False
        ok = (p["gap_min"] <= g <= p["gap_max"]
              and (f.get("rvol") or 0) >= p["rvol_min"]
              and f.get("ret_since_open", -1) > 0)
        if p["require_orb"]:
            ok = ok and f.get("orb_breakout") == 1.0
        if p["require_above_vwap"]:
            dv = f.get("dist_vwap")
            ok = ok and isinstance(dv, float) and not math.isnan(dv) and dv > 0
        return bool(ok)

    def _loose_gate(self, f: dict) -> bool:
        g = f.get("gap_pct")
        return (g is not None and not (isinstance(g, float) and math.isnan(g))
                and 0.005 <= g <= 0.50 and (f.get("rvol") or 0) >= 1.0)

    def _gate_mask(self, df: pd.DataFrame) -> pd.Series:
        """Vectorized equivalent of _gate for DataFrames. Must stay in
        lock-step with _gate (tested in tests/test_gate_mask.py)."""
        p = self.p

        def col(name, default=np.nan):
            if name in df.columns:
                return pd.to_numeric(df[name], errors="coerce")
            return pd.Series(default, index=df.index, dtype=float)

        g = col("gap_pct")
        m = (g.ge(p["gap_min"]) & g.le(p["gap_max"])
             & col("rvol").fillna(0).ge(p["rvol_min"])
             & col("ret_since_open", -1.0).fillna(-1.0).gt(0))
        if p["require_orb"]:
            m &= col("orb_breakout").eq(1.0)
        if p["require_above_vwap"]:
            m &= col("dist_vwap").gt(0)
        return m.fillna(False)

    def fit(self, train_dates, daily_panel, minute_loader) -> "GapRvolStrategy":
        self.daily_panel = daily_panel
        # The loose-gated frame and the exit model depend only on
        # (decision_time, train dates) -- identical for every grid config
        # sharing them. Memoize both; refitting is deterministic
        # (random_state=0), so this changes nothing but wall clock.
        memo_key = ("gaprvol_loose", self.p["decision_time"],
                    _dates_sig(train_dates))
        train = _TRAIN_FRAME_MEMO.get(memo_key)
        if train is None:
            train = build_training_frame(train_dates, daily_panel,
                                         [self.p["decision_time"]],
                                         self._loose_gate, minute_loader)
            _memo_put(_TRAIN_FRAME_MEMO, memo_key, train)
        self.train_labels = train
        cached_exit = _EXIT_MODEL_MEMO.get(memo_key)
        if cached_exit is not None:
            self.exit_model = cached_exit
        else:
            self.exit_model.fit(train)
            _memo_put(_EXIT_MODEL_MEMO, memo_key, self.exit_model)
        if len(train) >= 60:
            strict = train[self._gate_mask(train)]
            if len(strict) >= 20:
                by_m = strict.groupby(strict["date"].dt.to_period("M"))["net_ret_ref"].mean()
                self.stability = float((by_m > 0).mean()) if len(by_m) else 0.5
        return self

    def candidates_for_day(self, view: PointInTimeView) -> list[dict]:
        feats = generate_candidates(view, self.daily_panel,
                                    [self.p["decision_time"]], self._gate)
        out = []
        for f in feats:
            row = pd.Series(f)
            tp, sl, why = self.exit_model.levels(row)
            hc = cost_haircut(f["last_price"], sl)
            est = empirical_first_touch(self.train_labels if self.train_labels is not None else pd.DataFrame(),
                                        tp, sl, hc)
            ev, p_win = est["ev"], est["p_win"]
            if isinstance(ev, float) and math.isnan(ev):
                continue  # no evidence -> no candidate
            score = rank_score(f, ev, p_win, stability=self.stability,
                               overfit_penalty=min(self.n_trials_burned / 200.0, 1.0))
            out.append({**f, "tp_pct": tp, "sl_pct": sl, "exit_reasoning": why,
                        "ev": ev, "p_win": p_win, "score": score,
                        "ev_evidence_n": est.get("n", 0)})
        return out

    def describe(self) -> str:
        return f"GapRvol({self.p})"


# ---------------------------- ML strategy ----------------------------

class MLRankingStrategy:
    """Loose gate + learned EV/P(win). The models never see val/test."""

    def __init__(self, decision_times=("09:45", "10:30"), rvol_min: float = 1.2):
        self.decision_times = list(decision_times)
        self.rvol_min = rvol_min
        self.ev_model = ExpectedValueModel()
        self.p_model = WinProbabilityModel()
        self.exit_model = DynamicExitModel()
        self.train_labels: pd.DataFrame | None = None
        self.stability = 0.5
        self.n_trials_burned = 0

    def _gate(self, f: dict) -> bool:
        rv = f.get("rvol")
        return (isinstance(rv, float) and not math.isnan(rv) and rv >= self.rvol_min
                and f.get("ret_since_open", 0) > -0.05)

    def fit(self, train_dates, daily_panel, minute_loader) -> "MLRankingStrategy":
        self.daily_panel = daily_panel
        train = build_training_frame(train_dates, daily_panel,
                                     self.decision_times, self._gate, minute_loader)
        self.train_labels = train
        self.ev_model.fit(train)
        self.p_model.fit(train)
        self.exit_model.fit(train)
        return self

    def candidates_for_day(self, view: PointInTimeView) -> list[dict]:
        feats = generate_candidates(view, self.daily_panel,
                                    self.decision_times, self._gate)
        if not feats:
            return []
        fdf = pd.DataFrame(feats)
        evs = self.ev_model.predict(fdf)
        ps = self.p_model.predict_proba(fdf)
        out = []
        for i, f in enumerate(feats):
            ev, p = float(evs[i]), float(ps[i])
            if math.isnan(ev):
                continue
            tp, sl, why = self.exit_model.levels(pd.Series(f))
            score = rank_score(f, ev, p if not math.isnan(p) else 0.5,
                               stability=self.stability,
                               overfit_penalty=min(self.n_trials_burned / 200.0, 1.0))
            out.append({**f, "tp_pct": tp, "sl_pct": sl, "exit_reasoning": why,
                        "ev": ev, "p_win": p, "score": score,
                        "ev_evidence_n": len(self.train_labels) if self.train_labels is not None else 0})
        return out

    def describe(self) -> str:
        return f"MLRanking(times={self.decision_times}, rvol>={self.rvol_min})"


class ExitVariantWrapper:
    """Same entries as `inner`, different exit rule. Used to compare
    dynamic exits against fixed ATR / trailing / breakeven variants."""

    def __init__(self, inner, tp_atr=None, sl_atr=None, trail_pct=None,
                 breakeven_trigger_pct=None, name="variant"):
        self.inner = inner
        self.tp_atr, self.sl_atr = tp_atr, sl_atr
        self.trail_pct, self.be = trail_pct, breakeven_trigger_pct
        self.name = name

    def fit(self, *a, **k):
        self.inner.fit(*a, **k)
        return self

    def candidates_for_day(self, view):
        cands = self.inner.candidates_for_day(view)
        for c in cands:
            atr = c.get("atr_pct_prev")
            if atr is None or (isinstance(atr, float) and math.isnan(atr)) or atr <= 0:
                atr = 0.02
            if self.tp_atr is not None:
                c["tp_pct"] = max(self.tp_atr * atr, 0.004)
            if self.sl_atr is not None:
                c["sl_pct"] = max(self.sl_atr * atr, 0.004)
            if self.trail_pct is not None:
                c["trail_pct"] = self.trail_pct
            if self.be is not None:
                c["breakeven_trigger_pct"] = self.be
        return cands


def grid(strategy_cls) -> list[dict]:
    g = strategy_cls.PARAM_GRID
    keys = list(g)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(g[k] for k in keys))]
