"""Quantile LightGBM + split-conformal (CQR) calibration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd

from energy_forecasting.features import FEATURES, QUANTILES

QCOLS = [f"q{q:.2f}" for q in QUANTILES]
LGB_PARAMS = dict(
    n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=50,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
    random_state=42, n_jobs=4, verbose=-1,
)  # fmt: skip

# (low, high) quantile columns per nominal central interval
INTERVALS = {0.8: ("q0.10", "q0.90"), 0.9: ("q0.05", "q0.95")}


@dataclass
class QuantileGBM:
    params: dict = field(default_factory=lambda: dict(LGB_PARAMS))
    models: dict[str, lgb.LGBMRegressor] = field(default_factory=dict)

    def fit(self, df: pd.DataFrame) -> QuantileGBM:
        d = df.dropna(subset=["price"])
        for q, col in zip(QUANTILES, QCOLS, strict=True):
            m = lgb.LGBMRegressor(objective="quantile", alpha=float(q), **self.params)
            m.fit(d[FEATURES], d["price"])
            self.models[col] = m
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame({c: m.predict(df[FEATURES]) for c, m in self.models.items()}, index=df.index)
        out[:] = np.sort(out.to_numpy(), axis=1)  # rearrangement: no quantile crossing
        return out


def cqr_offset(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, level: float) -> float:
    """Conformalised quantile regression correction (Romano et al. 2019)."""
    scores = np.maximum(lo - y, y - hi)
    n = len(scores)
    k = min(n, math.ceil((n + 1) * level))
    return float(np.sort(scores)[k - 1])


def apply_cqr(pred: pd.DataFrame, offsets: dict[float, float]) -> pd.DataFrame:
    """Return conformal bounds as columns c{level}_lo / c{level}_hi."""
    out = pd.DataFrame(index=pred.index)
    for level, (lo, hi) in INTERVALS.items():
        out[f"c{level:.1f}_lo"] = pred[lo] - offsets[level]
        out[f"c{level:.1f}_hi"] = pred[hi] + offsets[level]
    return out
