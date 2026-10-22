"""Rolling-origin backtest: monthly refits on an expanding window, CQR from past folds."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from energy_forecasting.features import QUANTILES, empirical_quantile_baseline, naive_forecasts
from energy_forecasting.models import INTERVALS, QCOLS, QuantileGBM, apply_cqr, cqr_offset

log = logging.getLogger(__name__)


def pinball(y: np.ndarray, q_pred: np.ndarray, quantiles: np.ndarray) -> np.ndarray:
    """Per-observation, per-quantile pinball loss; shapes (n,), (n,k), (k,) -> (n,k)."""
    diff = y[:, None] - q_pred
    return np.maximum(quantiles[None, :] * diff, (quantiles[None, :] - 1) * diff)


def crps_from_quantiles(y: np.ndarray, q_pred: np.ndarray, quantiles: np.ndarray) -> np.ndarray:
    """CRPS approximated as 2 * mean pinball over an evenly spaced quantile grid."""
    return 2 * pinball(y, q_pred, quantiles).mean(axis=1)


def run_backtest(
    feats: pd.DataFrame,
    raw: pd.DataFrame,
    first_fold: str = "2024-07-01",
    eval_start: str = "2025-01-01",
    calib_months: int = 3,
) -> pd.DataFrame:
    """Return per-hour predictions for all folds from `first_fold` to the end of the data.

    Fold m: fit on all rows with delivery date < m (their targets are known at m's first
    origin), predict month m. Conformal offsets for month m use only out-of-sample scores
    of the previous `calib_months` folds.
    """
    d = feats.dropna(subset=["price"]).copy()
    local = d.index.tz_convert("Europe/Paris")
    d["month_start"] = pd.PeriodIndex(local.tz_localize(None), freq="M").start_time
    months = sorted(d.loc[d["month_start"] >= pd.Timestamp(first_fold), "month_start"].unique())
    preds = []
    for m in months:
        m = pd.Timestamp(m)
        train = d[d["month_start"] < m]
        test = d[d["month_start"] == m]
        if len(train) < 24 * 365:
            continue
        model = QuantileGBM().fit(train)
        p = model.predict(test)
        p["y"] = test["price"]
        p["fold"] = m
        preds.append(p)
        log.info("fold %s: train=%d test=%d", m.date(), len(train), len(test))
    out = pd.concat(preds)

    # conformal offsets from previous folds' OOS scores
    for level in INTERVALS:
        out[f"c{level:.1f}_lo"] = np.nan
        out[f"c{level:.1f}_hi"] = np.nan
    for m in sorted(out["fold"].unique()):
        past = out[(out["fold"] < m) & (out["fold"] >= m - pd.DateOffset(months=calib_months))]
        if past.empty:
            continue
        cur = out["fold"] == m
        offs = {
            lvl: cqr_offset(past["y"].to_numpy(), past[lo].to_numpy(), past[hi].to_numpy(), lvl)
            for lvl, (lo, hi) in INTERVALS.items()
        }
        conf = apply_cqr(out.loc[cur, QCOLS], offs)
        out.loc[cur, conf.columns] = conf

    base = naive_forecasts(feats).reindex(out.index)
    out = out.join(base)
    emp = empirical_quantile_baseline(raw).reindex(out.index)
    emp.columns = ["emp_" + c for c in emp.columns]
    out = out.join(emp)
    out["median"] = out["q0.50"]
    return out[out["fold"] >= pd.Timestamp(eval_start)] if eval_start else out


def _interval_stats(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[float, float]:
    return float(((y >= lo) & (y <= hi)).mean()), float((hi - lo).mean())


def evaluate(pred: pd.DataFrame) -> pd.DataFrame:
    """Metrics table, one row per method (rows with complete data only)."""
    p = pred.dropna(subset=["c0.8_lo", "emp_q0.50", "naive_week"]).copy()
    y = p["y"].to_numpy()
    qs = np.array(QUANTILES)
    rows = []

    def prob_row(name: str, qpred: np.ndarray, lo80: np.ndarray, hi80: np.ndarray,
                 lo90: np.ndarray, hi90: np.ndarray) -> dict:  # fmt: skip
        c80, w80 = _interval_stats(y, lo80, hi80)
        c90, w90 = _interval_stats(y, lo90, hi90)
        return {
            "method": name,
            "MAE_median": float(np.abs(y - qpred[:, 9]).mean()),
            "pinball": float(pinball(y, qpred, qs).mean()),
            "CRPS": float(crps_from_quantiles(y, qpred, qs).mean()),
            "cov80": c80, "width80": w80, "cov90": c90, "width90": w90,
        }  # fmt: skip

    for name, cols, pre in (("QuantileGBM", QCOLS, ""), ("Empirical-28d", QCOLS, "emp_")):
        qp = p[[pre + c for c in cols]].to_numpy()
        rows.append(prob_row(name, qp, qp[:, 1], qp[:, 17], qp[:, 0], qp[:, 18]))
    qp = p[QCOLS].to_numpy()
    rows.append(
        prob_row("QuantileGBM + CQR", qp, p["c0.8_lo"].to_numpy(), p["c0.8_hi"].to_numpy(),
                 p["c0.9_lo"].to_numpy(), p["c0.9_hi"].to_numpy())
    )  # fmt: skip
    for name in ("naive_week", "naive_seasonal"):
        rows.append({"method": name, "MAE_median": float(np.abs(y - p[name]).mean())})
    return pd.DataFrame(rows).set_index("method")
