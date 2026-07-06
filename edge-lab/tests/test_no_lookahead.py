import pandas as pd
import pytest

from src.data.feature_builder import PointInTimeView, compute_features
from src.data.leakage_checks import _perturb_future, future_invariance_check
from src.utils.time_utils import ts_et
from src.utils.validation import DataValidationError, assert_no_rows_at_or_after
from tests.conftest import flat_bars, panel_row

D = "2025-06-02"


def _day():
    pm = flat_bars(D, "AAA", "09:00", "09:25", px=99.0, v=50_000)
    rth = flat_bars(D, "AAA", "09:30", "15:59", px=100.0, v=300_000)
    rth.loc[rth.index[:30], "close"] = 100.5  # some structure
    return pd.concat([pm, rth], ignore_index=True)


def test_features_invariant_to_future():
    day = _day()
    panel = panel_row(D, "AAA")
    for hhmm in ("09:45", "11:00", "14:30"):
        leaks = future_invariance_check(day, panel, D, "AAA", ts_et(D, hhmm))
        assert leaks == [], f"leaking features at {hhmm}: {leaks}"


def test_perturbation_actually_changes_future():
    """Negative control: a deliberately leaky value MUST differ, proving the
    invariance test has teeth."""
    day = _day()
    panel = panel_row(D, "AAA")
    dts = ts_et(D, "11:00")
    v1 = PointInTimeView.build(D, day, panel)
    v2 = PointInTimeView.build(D, _perturb_future(day, dts), panel)
    leaky_1 = v1._full_day_unsafe("AAA")["high"].max()
    leaky_2 = v2._full_day_unsafe("AAA")["high"].max()
    assert leaky_1 != leaky_2


def test_view_never_returns_future_rows():
    day = _day()
    panel = panel_row(D, "AAA")
    v = PointInTimeView.build(D, day, panel)
    dts = ts_et(D, "10:15")
    got = v.minutes_before("AAA", dts)
    assert (got["ts"] < dts).all()


def test_validation_gate_raises_on_future_rows():
    day = _day()
    with pytest.raises(DataValidationError):
        assert_no_rows_at_or_after(day, ts_et(D, "10:00"))


def test_first_k_minute_returns_nan_before_observable():
    day = _day()
    panel = panel_row(D, "AAA")
    v = PointInTimeView.build(D, day, panel)
    f = compute_features(v, "AAA", ts_et(D, "09:33"))
    assert pd.isna(f["ret_first_5m"]) and pd.isna(f["ret_first_10m"])
