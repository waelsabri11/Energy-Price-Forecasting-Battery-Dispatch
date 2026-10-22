"""Synthetic ENTSO-E-like fixture so tests/CI run without an API key or network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_raw(days: int = 560, seed: int = 0, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    local = idx.tz_convert("Europe/Paris")
    h, dow, doy = local.hour.to_numpy(), local.dayofweek.to_numpy(), local.dayofyear.to_numpy()
    load = 45_000 + 10_000 * np.cos(2 * np.pi * (doy - 20) / 365) + 6_000 * np.sin(np.pi * (h - 6) / 12)
    load = load - 4_000 * (dow >= 5) + rng.normal(0, 800, len(idx))
    wind = np.clip(5_000 + 3_000 * np.sin(np.arange(len(idx)) / 70) + rng.normal(0, 1_500, len(idx)), 0, None)
    solar = np.clip(6_000 * np.sin(np.pi * (h - 6) / 12), 0, None) * (
        0.6 + 0.4 * np.sin(2 * np.pi * (doy - 80) / 365)
    )
    resid = load - wind - solar
    price = 20 + 0.0018 * (resid - 30_000) ** 1.0 * 10 + rng.normal(0, 8, len(idx))
    base = pd.DataFrame(
        {
            "price": price,
            "load_fc": load,
            "fc_wind_onshore": wind * 0.9,
            "fc_wind_offshore": wind * 0.1,
            "fc_solar": solar,
            "price_de_lu": price + rng.normal(0, 5, len(idx)),
            "price_be": price + rng.normal(0, 3, len(idx)),
            "price_es": price * 0.8 + rng.normal(0, 4, len(idx)),
        },
        index=idx,
    )
    base.index.name = "ts_utc"
    return base


@pytest.fixture(scope="session")
def raw() -> pd.DataFrame:
    return make_raw()
