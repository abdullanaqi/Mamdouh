"""Fold checkpoints: exact restore on matching signatures, rejection on
any mismatch (different grid, different windows)."""
from types import SimpleNamespace

import pandas as pd
import pytest

from src.research import walk_forward as wfm


@pytest.fixture(autouse=True)
def tmp_ckpt_dir(tmp_path, monkeypatch):
    stub = SimpleNamespace(paths=SimpleNamespace(walk_forward=tmp_path))
    monkeypatch.setattr(wfm, "CFG", stub)


def _fold():
    days = pd.date_range("2025-01-02", periods=30, freq="B")
    return wfm.Fold(train=list(days[:20]), val=list(days[20:25]),
                    test=list(days[25:]))


def test_ckpt_round_trip_and_mismatch_rejection():
    fold = _fold()
    sig = wfm._fold_sig(fold)
    extra = ("GapRvolStrategy", False, (("a", 1),))
    trades = pd.DataFrame({"ticker": ["X"], "net_ret": [0.01]})
    payload = {"fold_log": {"fold": 0, "chosen": {"a": 1}}, "test_trades": trades}
    wfm._ckpt_save("tag", 0, sig, extra, payload)

    got = wfm._ckpt_load("tag", 0, sig, extra)
    assert got is not None
    assert got["fold_log"]["chosen"] == {"a": 1}
    pd.testing.assert_frame_equal(got["test_trades"], trades)

    assert wfm._ckpt_load("tag", 0, sig, ("Other", False, ())) is None
    other_sig = wfm._fold_sig(wfm.Fold(train=fold.train[:-1], val=fold.val,
                                       test=fold.test))
    assert wfm._ckpt_load("tag", 0, other_sig, extra) is None
    assert wfm._ckpt_load("tag", 1, sig, extra) is None      # missing fold
    assert wfm._ckpt_load(None, 0, sig, extra) is None       # tag disabled
