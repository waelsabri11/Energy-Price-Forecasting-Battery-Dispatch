from __future__ import annotations

import numpy as np
import pandas as pd

from energy_forecasting.backtest import crps_from_quantiles, pinball
from energy_forecasting.dispatch import (
    Battery,
    make_scenarios,
    realised_revenue,
    solve_schedule,
)
from energy_forecasting.features import QUANTILES
from energy_forecasting.models import cqr_offset
from energy_forecasting.schemas import validate_raw


def test_pinball_zero_for_perfect_and_asymmetric() -> None:
    q = np.array([0.1, 0.9])
    assert pinball(np.array([5.0]), np.array([[5.0, 5.0]]), q).sum() == 0
    loss = pinball(np.array([10.0]), np.array([[0.0, 0.0]]), q)[0]
    assert np.isclose(loss[0], 1.0) and np.isclose(loss[1], 9.0)


def test_crps_non_negative() -> None:
    qs = np.array(QUANTILES)
    out = crps_from_quantiles(np.array([1.0, 2.0]), np.tile(np.linspace(0, 3, 19), (2, 1)), qs)
    assert (out >= 0).all()


def test_cqr_restores_coverage() -> None:
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 5000)
    lo, hi = np.full(5000, -0.5), np.full(5000, 0.5)  # intervals far too narrow
    off = cqr_offset(y[:2500], lo[:2500], hi[:2500], 0.9)
    cov = ((y[2500:] >= lo[2500:] - off) & (y[2500:] <= hi[2500:] + off)).mean()
    assert 0.87 < cov < 0.93


def test_lp_dispatch_physical_constraints_and_profit() -> None:
    b = Battery()
    prices = np.r_[np.full(8, 20.0), np.full(8, 60.0), np.full(8, 40.0)]
    c, d = solve_schedule(prices[None, :], b)
    soc = b.soc_init * b.capacity_mwh + np.cumsum(b.eta * c - d / b.eta)
    assert (c <= b.power_mw + 1e-6).all() and (d <= b.power_mw + 1e-6).all()
    assert soc.min() >= b.soc_min * b.capacity_mwh - 1e-6
    assert soc.max() <= b.soc_max * b.capacity_mwh + 1e-6
    assert soc[-1] >= b.soc_init * b.capacity_mwh - 1e-6
    assert realised_revenue(prices, c, d, b) > 0
    assert (np.minimum(c, d) < 1e-6).all()  # no simultaneous charge/discharge on positive prices


def test_flat_prices_do_nothing() -> None:
    c, d = solve_schedule(np.full((1, 24), 50.0), Battery())
    assert c.sum() < 1e-6 and d.sum() < 1e-6


def test_perfect_foresight_is_upper_bound() -> None:
    rng = np.random.default_rng(1)
    b = Battery()
    y = 50 + 30 * np.sin(np.arange(24) / 24 * 2 * np.pi) + rng.normal(0, 5, 24)
    forecast = y + rng.normal(0, 15, 24)
    perfect = realised_revenue(y, *solve_schedule(y[None], b), b)
    other = realised_revenue(y, *solve_schedule(forecast[None], b), b)
    assert perfect >= other - 1e-6


def test_scenarios_are_ordered_by_quantiles_and_reproducible() -> None:
    q = np.tile(np.linspace(10, 90, 19), (24, 1))
    a = make_scenarios(q, 0.7, 200, seed=3)
    assert a.shape == (200, 24)
    assert np.allclose(a, make_scenarios(q, 0.7, 200, seed=3))
    assert 35 < np.median(a) < 65


def test_cvar_dispatch_runs_and_is_feasible() -> None:
    q = np.tile(np.linspace(10, 90, 19), (24, 1)) + 20 * np.sin(np.arange(24) / 4)[:, None]
    sc = make_scenarios(q, 0.7, 40, seed=0)
    c, d = solve_schedule(sc, Battery(), lam=0.5)
    assert c.shape == (24,) and d.sum() >= 0


def test_validate_raw_rejects_bad_prices(raw: pd.DataFrame) -> None:
    validate_raw(raw)
    bad = raw.copy()
    bad.iloc[5, bad.columns.get_loc("price")] = 99999
    try:
        validate_raw(bad)
    except Exception as exc:  # noqa: BLE001
        assert "price" in str(exc)
    else:
        raise AssertionError("expected schema error")
