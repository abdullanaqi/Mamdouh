"""Dynamic TP/SL engine.

For each candidate the engine estimates, from TRAINING data only:
  - expected favorable move   (quantile regression on realized MFE)
  - expected adverse move     (quantile regression on realized |MAE|)
and converts them into explainable TP/SL levels:
  TP = q_tp of predicted MFE distribution   (default q=0.40: hit often)
  SL = q_sl of predicted |MAE| distribution (default q=0.75: rarely noise-stopped)
both floored/capped by ATR multiples so a bad model cannot pick absurd levels.

Win probability and EV are then estimated EMPIRICALLY by replaying the
chosen levels through historical first-touch simulation on the training
window -- never by trusting the model's own optimism.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from src.data.feature_builder import FEATURE_COLUMNS


class DynamicExitModel:
    def __init__(self, q_tp: float = 0.40, q_sl: float = 0.75,
                 atr_tp_bounds=(0.5, 3.0), atr_sl_bounds=(0.4, 2.0)):
        self.q_tp, self.q_sl = q_tp, q_sl
        self.atr_tp_bounds, self.atr_sl_bounds = atr_tp_bounds, atr_sl_bounds
        self.mfe_model: HistGradientBoostingRegressor | None = None
        self.mae_model: HistGradientBoostingRegressor | None = None
        self.feature_cols = [c for c in FEATURE_COLUMNS]

    def _X(self, df: pd.DataFrame) -> np.ndarray:
        # Always project onto the FULL feature space so fit/predict agree;
        # HistGradientBoosting handles NaN columns natively.
        return df.reindex(columns=self.feature_cols).astype(float).to_numpy()

    def fit(self, train: pd.DataFrame) -> "DynamicExitModel":
        """train must contain feature columns + labels mfe_eod, mae_eod."""
        need = train.dropna(subset=["mfe_eod", "mae_eod"])
        if len(need) < 200:
            # not enough data for a model -> pure ATR fallback
            self.mfe_model = self.mae_model = None
            return self
        X = self._X(need)
        # sklearn >= 1.9 crashes when a training column is entirely NaN
        # (binning finds 0 distinct values). Such columns carry no signal;
        # fill with a constant so they simply produce no splits.
        all_nan = np.isnan(X).all(axis=0)
        X[:, all_nan] = 0.0
        self.mfe_model = HistGradientBoostingRegressor(
            loss="quantile", quantile=self.q_tp, max_depth=3, max_iter=150, random_state=0)
        self.mfe_model.fit(X, need["mfe_eod"].clip(lower=0).to_numpy())
        self.mae_model = HistGradientBoostingRegressor(
            loss="quantile", quantile=self.q_sl, max_depth=3, max_iter=150, random_state=0)
        self.mae_model.fit(X, need["mae_eod"].abs().to_numpy())
        return self

    def levels(self, feat_row: pd.Series) -> tuple[float, float, str]:
        """Returns (tp_pct, sl_pct, explanation)."""
        atr = float(feat_row.get("atr_pct_prev", np.nan))
        if np.isnan(atr) or atr <= 0:
            atr = 0.02
        tp_lo, tp_hi = self.atr_tp_bounds[0] * atr, self.atr_tp_bounds[1] * atr
        sl_lo, sl_hi = self.atr_sl_bounds[0] * atr, self.atr_sl_bounds[1] * atr
        if self.mfe_model is None:
            tp, sl = 1.5 * atr, 1.0 * atr
            why = f"ATR fallback: TP=1.5*ATR({atr:.3f}), SL=1.0*ATR (insufficient training data for quantile model)"
        else:
            x = feat_row.reindex(self.feature_cols).astype(float).to_numpy().reshape(1, -1)
            tp = float(np.clip(self.mfe_model.predict(x)[0], tp_lo, tp_hi))
            sl = float(np.clip(self.mae_model.predict(x)[0], sl_lo, sl_hi))
            why = (f"TP=q{int(self.q_tp*100)} of predicted MFE ({tp:.3%}), "
                   f"SL=q{int(self.q_sl*100)} of predicted |MAE| ({sl:.3%}), "
                   f"bounded to [{self.atr_tp_bounds[0]}-{self.atr_tp_bounds[1]}]x / "
                   f"[{self.atr_sl_bounds[0]}-{self.atr_sl_bounds[1]}]x ATR={atr:.3%}")
        tp = max(tp, 0.004)
        sl = max(sl, 0.004)
        return tp, sl, why


def empirical_first_touch(train_labels: pd.DataFrame, tp_pct: float, sl_pct: float,
                          costs_ret_haircut: float) -> dict:
    """Estimate P(win) and EV for given levels by replaying MFE/MAE paths of
    similar historical candidates. Conservative tie rule: if a path's |MAE|
    reached SL at any point and MFE also reached TP, count it as a LOSS
    (order within the day is unknown from summary labels)."""
    df = train_labels.dropna(subset=["mfe_eod", "mae_eod", "ret_eod"])
    if len(df) < 50:
        return {"p_win": float("nan"), "ev": float("nan"), "n": len(df)}
    hit_tp = df["mfe_eod"] >= tp_pct
    hit_sl = df["mae_eod"].abs() >= sl_pct
    win = hit_tp & ~hit_sl
    loss = hit_sl
    neither = ~hit_tp & ~hit_sl
    p_win = float(win.mean())
    ev = (win.mean() * tp_pct
          + loss.mean() * (-sl_pct)
          + (df.loc[neither, "ret_eod"].mean() if neither.any() else 0.0) * neither.mean()
          - costs_ret_haircut)
    return {"p_win": p_win, "ev": float(ev), "n": int(len(df)),
            "p_loss": float(loss.mean()), "p_timeout": float(neither.mean())}
