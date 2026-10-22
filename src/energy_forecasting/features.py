"""Feature engineering for the day-ahead forecast made at gate closure of day D.

Forecast origin: day D, 12:00 CET (day-ahead gate closure). Target: the 24 hourly
prices of delivery day D+1. Information available at the origin:

* day-ahead prices up to and including delivery day D (published D-1 ~13:00),
* TSO day-ahead forecasts (load, wind, solar) for delivery day D+1,
* the calendar.

Every price-derived feature is therefore a lag whose source timestamp lies strictly
before 00:00 local time of day D+1. `build_features` enforces this explicitly and
`tests/test_no_leakage.py` checks it by perturbing future prices.
"""

from __future__ import annotations

import holidays
import numpy as np
import pandas as pd

TZ = "Europe/Paris"
GATE_CLOSURE_HOUR_LOCAL = 12
QUANTILES = tuple(np.round(np.arange(0.05, 0.951, 0.05), 2))  # 19 levels
NEIGHBOUR_COLS = ("price_de_lu", "price_be", "price_es")

TARGET = "price"
FEATURES = [
    "hour", "dow", "month", "is_weekend", "is_holiday", "doy_sin", "doy_cos",
    "load_fc", "wind_fc", "solar_fc", "residual_load_fc",
    "price_lag24", "price_lag48", "price_lag168",
    "day_mean_d", "day_min_d", "day_max_d", "day_std_d", "mean_7d", "mean_28d",
    "lag24_de_lu", "lag24_be", "lag24_es", "day_mean_de_lu_d",
]  # fmt: skip


def _lag(s: pd.Series, hours: int, day_start: pd.Series) -> pd.Series:
    """Value `hours` earlier, masked unless its timestamp precedes target-day start."""
    out = s.shift(hours, freq="h").reindex(s.index)
    src_ts = pd.Series(s.index - pd.Timedelta(hours=hours), index=s.index)
    return out.where(src_ts < day_start)


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    """raw: hourly UTC frame (price, load_fc, fc_*, price_<neighbour>). Returns features."""
    raw = raw.sort_index()
    idx = raw.index
    local = idx.tz_convert(TZ)
    df = pd.DataFrame(index=idx)
    df["origin_date"] = local.normalize().tz_localize(None) - pd.Timedelta(days=1)
    df["delivery_date"] = local.normalize().tz_localize(None)
    day_start = pd.Series(local.normalize().tz_convert("UTC"), index=idx)

    df["hour"] = local.hour
    df["dow"] = local.dayofweek
    df["month"] = local.month
    df["is_weekend"] = (local.dayofweek >= 5).astype(int)
    fr_h = holidays.country_holidays("FR", years=sorted(set(local.year)))
    df["is_holiday"] = [int(d in fr_h) for d in local.date]
    doy = local.dayofyear.to_numpy()
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

    wind_cols = [c for c in raw.columns if c.startswith("fc_wind")]
    df["load_fc"] = raw["load_fc"]
    df["wind_fc"] = raw[wind_cols].sum(axis=1, min_count=1) if wind_cols else np.nan
    df["solar_fc"] = raw.get("fc_solar", np.nan)
    df["residual_load_fc"] = df["load_fc"] - df["wind_fc"] - df["solar_fc"]

    price = raw["price"]
    for k in (24, 48, 168):
        df[f"price_lag{k}"] = _lag(price, k, day_start)

    # daily stats of the last fully known day D, joined on the delivery date
    by_day = price.groupby(df["delivery_date"])
    stats = pd.DataFrame(
        {"mean": by_day.mean(), "min": by_day.min(), "max": by_day.max(), "std": by_day.std()}
    )
    d_shift = df["delivery_date"] - pd.Timedelta(days=1)
    for name in ("mean", "min", "max", "std"):
        df[f"day_{name}_d"] = d_shift.map(stats[name])
    daily_mean = stats["mean"]
    df["mean_7d"] = d_shift.map(daily_mean.rolling(7, min_periods=5).mean())
    df["mean_28d"] = d_shift.map(daily_mean.rolling(28, min_periods=20).mean())

    for col in NEIGHBOUR_COLS:
        if col in raw:
            short = col.replace("price_", "")
            df[f"lag24_{short}"] = _lag(raw[col], 24, day_start)
            if short == "de_lu":
                nb_day = raw[col].groupby(df["delivery_date"]).mean()
                df["day_mean_de_lu_d"] = d_shift.map(nb_day)
    for c in FEATURES:
        if c not in df:
            df[c] = np.nan
    df[TARGET] = price
    return df


def naive_forecasts(df: pd.DataFrame) -> pd.DataFrame:
    """Point baselines derived from the lag features."""
    out = pd.DataFrame(index=df.index)
    out["naive_week"] = df["price_lag168"]
    # Tue-Fri: yesterday's price; Mon/Sat/Sun: same day last week
    out["naive_seasonal"] = np.where(df["dow"].isin([1, 2, 3, 4]), df["price_lag24"], df["price_lag168"])
    return out


def empirical_quantile_baseline(raw: pd.DataFrame, window_days: int = 28) -> pd.DataFrame:
    """Quantiles of the same local hour over the `window_days` days up to day D."""
    local = raw.index.tz_convert(TZ)
    p = raw["price"].copy()
    p.index = pd.MultiIndex.from_arrays([local.normalize().tz_localize(None), local.hour])
    piv = p.groupby(level=[0, 1]).mean().unstack()
    cols = {}
    for q in QUANTILES:
        # row for delivery date X = window over the days strictly before X (<= D)
        cols[q] = piv.rolling(window_days, min_periods=14).quantile(float(q)).shift(1)
    res = {q: c.stack() for q, c in cols.items()}
    out = pd.DataFrame(res)
    out.index.names = ["delivery_date", "hour"]
    key = pd.MultiIndex.from_arrays([local.normalize().tz_localize(None), local.hour])
    res_df = out.reindex(key)
    res_df.index = raw.index
    res_df.columns = [f"q{q:.2f}" for q in QUANTILES]
    return res_df
