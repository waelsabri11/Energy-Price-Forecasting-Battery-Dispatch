"""No-leakage checks: features of delivery day D+1 may only use info available at D gate closure."""

from __future__ import annotations

import numpy as np
import pandas as pd

from energy_forecasting.features import FEATURES, GATE_CLOSURE_HOUR_LOCAL, TZ, build_features

PRICE_DERIVED = [c for c in FEATURES if "price" in c or c.startswith(("day_", "mean_", "lag24_"))]


def test_future_prices_do_not_change_features(raw: pd.DataFrame) -> None:
    """Corrupt every price from a delivery day onwards: that day's features must not move."""
    base = build_features(raw)
    day = pd.Timestamp("2025-03-12")  # an arbitrary mid-sample delivery day
    start_utc = day.tz_localize(TZ).tz_convert("UTC")
    noisy = raw.copy()
    cols = [c for c in raw.columns if c.startswith("price")]
    noisy.loc[noisy.index >= start_utc, cols] += 1e4
    pert = build_features(noisy)
    mask = (pert["delivery_date"] == day) | (pert["delivery_date"] == day + pd.Timedelta(days=1))
    # day D+1's price features must be unchanged when everything from D+1 on is corrupted
    a = base.loc[mask & (base["delivery_date"] == day), PRICE_DERIVED]
    b = pert.loc[mask & (pert["delivery_date"] == day), PRICE_DERIVED]
    pd.testing.assert_frame_equal(a, b)


def test_every_lag_is_before_target_day(raw: pd.DataFrame) -> None:
    """Perturb a single past price hour and check which target hours see it."""
    base = build_features(raw)
    for lag_col, hours in (("price_lag24", 24), ("price_lag48", 48), ("price_lag168", 168)):
        t = base.index[2000]
        src = t - pd.Timedelta(hours=hours)
        day_start = t.tz_convert(TZ).normalize().tz_convert("UTC")
        if src >= day_start:
            assert np.isnan(base.loc[t, lag_col])
        else:
            assert base.loc[t, lag_col] == raw.loc[src, "price"]


def test_origin_precedes_gate_closure(raw: pd.DataFrame) -> None:
    f = build_features(raw)
    gate = f["origin_date"].dt.tz_localize(TZ, nonexistent="shift_forward", ambiguous="NaT") + pd.Timedelta(
        hours=GATE_CLOSURE_HOUR_LOCAL
    )
    delivery_start = f["delivery_date"].dt.tz_localize(TZ, nonexistent="shift_forward", ambiguous="NaT")
    ok = (gate < delivery_start) | gate.isna() | delivery_start.isna()
    assert ok.all()
    assert ((f["delivery_date"] - f["origin_date"]) == pd.Timedelta(days=1)).all()


def test_daily_stats_use_previous_day_only(raw: pd.DataFrame) -> None:
    f = build_features(raw)
    day = pd.Timestamp("2025-02-10")
    row = f[f["delivery_date"] == day].iloc[0]
    local = raw.index.tz_convert(TZ).normalize().tz_localize(None)
    prev = raw.loc[local == day - pd.Timedelta(days=1), "price"]
    assert np.isclose(row["day_mean_d"], prev.mean())
    assert np.isclose(row["day_max_d"], prev.max())
