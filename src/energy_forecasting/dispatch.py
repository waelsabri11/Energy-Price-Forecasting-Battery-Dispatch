"""Battery day-ahead dispatch as a linear program (cvxpy + HiGHS).

One schedule per delivery day is committed before prices are known (day-ahead market),
so the schedule is non-anticipative: a single (charge, discharge) profile shared by all
scenarios. Objective: (1 - lam) * E[revenue] + lam * CVaR_alpha[revenue]  (Rockafellar-Uryasev).
"""

from __future__ import annotations

from dataclasses import dataclass

import cvxpy as cp
import numpy as np
import pandas as pd
from scipy.stats import norm

from energy_forecasting.features import QUANTILES

LEVELS = np.array(QUANTILES)


@dataclass(frozen=True)
class Battery:
    power_mw: float = 10.0
    capacity_mwh: float = 20.0
    round_trip_eff: float = 0.90
    soc_min: float = 0.10  # fractions of capacity
    soc_max: float = 0.90
    soc_init: float = 0.50  # start of every day; end-of-day SoC must be >= this
    degradation_eur_mwh: float = 4.0  # cost per MWh discharged (throughput wear)

    @property
    def eta(self) -> float:
        return float(np.sqrt(self.round_trip_eff))


def solve_schedule(
    scenarios: np.ndarray, batt: Battery, lam: float = 0.0, alpha: float = 0.2
) -> tuple[np.ndarray, np.ndarray]:
    """scenarios: (S, T) price paths in EUR/MWh. Returns charge, discharge in MW (T,)."""
    scenarios = np.atleast_2d(scenarios)
    n_s, horizon = scenarios.shape
    c = cp.Variable(horizon, nonneg=True)
    d = cp.Variable(horizon, nonneg=True)
    soc = cp.Variable(horizon + 1)
    cons = [
        c <= batt.power_mw,
        d <= batt.power_mw,
        soc[0] == batt.soc_init * batt.capacity_mwh,
        soc[1:] == soc[:-1] + batt.eta * c - d / batt.eta,
        soc >= batt.soc_min * batt.capacity_mwh,
        soc <= batt.soc_max * batt.capacity_mwh,
        soc[horizon] >= batt.soc_init * batt.capacity_mwh,
    ]
    rev = scenarios @ (d - c) - batt.degradation_eur_mwh * cp.sum(d)  # (S,)
    if lam > 0 and n_s > 1:
        z = cp.Variable()
        cvar = z - cp.sum(cp.pos(z - rev)) / (alpha * n_s)
        obj = (1 - lam) * cp.sum(rev) / n_s + lam * cvar
    else:
        obj = cp.sum(rev) / n_s
    cp.Problem(cp.Maximize(obj), cons).solve(solver=cp.HIGHS)
    if c.value is None or d.value is None:
        raise RuntimeError("dispatch LP did not solve")
    return np.clip(c.value, 0, None), np.clip(d.value, 0, None)


def realised_revenue(prices: np.ndarray, c: np.ndarray, d: np.ndarray, batt: Battery) -> float:
    return float(prices @ (d - c) - batt.degradation_eur_mwh * d.sum())


def _inverse_cdf(u: np.ndarray, q_row: np.ndarray) -> np.ndarray:
    """Piecewise-linear inverse CDF through the quantile grid, linear tails beyond it."""
    lo_slope = (q_row[1] - q_row[0]) / (LEVELS[1] - LEVELS[0])
    hi_slope = (q_row[-1] - q_row[-2]) / (LEVELS[-1] - LEVELS[-2])
    x = np.interp(u, LEVELS, q_row)
    x = np.where(u < LEVELS[0], q_row[0] + lo_slope * (u - LEVELS[0]), x)
    return np.where(u > LEVELS[-1], q_row[-1] + hi_slope * (u - LEVELS[-1]), x)


def ar1_chol(horizon: int, rho: float) -> np.ndarray:
    idx = np.arange(horizon)
    return np.linalg.cholesky(rho ** np.abs(idx[:, None] - idx[None, :]))


def make_scenarios(q_day: np.ndarray, rho: float, n_scen: int = 50, seed: int = 0) -> np.ndarray:
    """q_day: (T, 19) quantiles -> (S, T) scenarios via a Gaussian AR(1) copula."""
    horizon = q_day.shape[0]
    rng = np.random.default_rng(seed)
    eps = rng.standard_normal((n_scen, horizon)) @ ar1_chol(horizon, rho).T
    u = np.clip(norm.cdf(eps), 0.005, 0.995)
    return np.stack([_inverse_cdf(u[:, t], q_day[t]) for t in range(horizon)], axis=1)


def estimate_rho(y: np.ndarray, q: np.ndarray, date: np.ndarray) -> float:
    """Lag-1 correlation of normal scores of the PIT within a day (copula dependence)."""
    pit = np.array([np.interp(y[i], q[i], LEVELS, left=0.01, right=0.99) for i in range(len(y))])
    z = norm.ppf(np.clip(pit, 0.01, 0.99))
    s = pd.Series(z)
    same_day = pd.Series(date).eq(pd.Series(date).shift(1))
    a, b = s[same_day], s.shift(1)[same_day]
    return float(np.clip(np.corrcoef(a, b)[0, 1], 0.0, 0.95))


def run_dispatch_backtest(
    pred: pd.DataFrame,
    batt: Battery,
    qcols: list[str],
    lambdas: tuple[float, ...] = (0.5,),
    n_scen: int = 50,
    seed: int = 42,
    eval_start: str = "2025-01-01",
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Daily revenues (EUR) at realised prices for each strategy.

    The copula correlation rho for each month is estimated only from the raw-quantile
    predictions of earlier folds (burn-in folds included), never from the month itself.
    """
    local = pred.index.tz_convert("Europe/Paris")
    date = np.asarray(local.normalize().tz_localize(None))
    folds = pred["fold"].to_numpy()
    rho_by_month: dict[str, float] = {}
    rows = []
    for k, (day, g) in enumerate(pred.groupby(date)):
        fold = g["fold"].iloc[0]
        if fold < pd.Timestamp(eval_start):
            continue
        if len(g) < 23 or g[qcols + ["y", "naive_week"]].isna().any().any():
            continue
        key = str(fold.date())
        if key not in rho_by_month:
            past = folds < fold
            rho_by_month[key] = estimate_rho(
                pred["y"].to_numpy()[past], pred[qcols].to_numpy()[past], date[past]
            )
        rho = rho_by_month[key]
        y = g["y"].to_numpy()
        row: dict[str, float] = {"date": day}
        for name, price in (
            ("perfect_foresight", y),
            ("naive_week", g["naive_week"].to_numpy()),
            ("median", g["q0.50"].to_numpy()),
        ):
            row[name] = realised_revenue(y, *solve_schedule(price[None, :], batt), batt)
        sc = make_scenarios(g[qcols].to_numpy(), rho, n_scen, seed + k)
        row["scenario_mean"] = realised_revenue(y, *solve_schedule(sc, batt), batt)
        for lam in lambdas:
            row[f"cvar_{lam:g}"] = realised_revenue(y, *solve_schedule(sc, batt, lam=lam), batt)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date"), rho_by_month


def summarise(daily: pd.DataFrame, batt: Battery) -> pd.DataFrame:
    perfect = daily["perfect_foresight"].sum()
    n_days = len(daily)
    out = pd.DataFrame(
        {
            "total_eur": daily.sum(),
            "eur_per_mw_year": daily.sum() / n_days * 365 / batt.power_mw,
            "pct_of_perfect": 100 * daily.sum() / perfect,
            "worst_5pct_day_eur": daily.quantile(0.05),
            "daily_std_eur": daily.std(),
        }
    )
    return out
