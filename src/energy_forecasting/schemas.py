"""Pandera schemas validating the data handed from one pipeline stage to the next."""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

from energy_forecasting.features import FEATURES, QUANTILES

QCOLS = [f"q{q:.2f}" for q in QUANTILES]

_utc_hourly_index = pa.Index(
    pd.DatetimeTZDtype(tz="UTC"), unique=True, name="ts_utc", checks=pa.Check(
        lambda i: i.is_monotonic_increasing, element_wise=False, error="index not sorted"
    ),
)  # fmt: skip

RawSchema = pa.DataFrameSchema(
    {
        "price": pa.Column(float, pa.Check.in_range(-600, 4000), nullable=True),
        "load_fc": pa.Column(float, pa.Check.in_range(10_000, 120_000), nullable=True),
    },
    index=_utc_hourly_index,
    strict=False,
    coerce=True,
)

FeatureSchema = pa.DataFrameSchema(
    {
        "hour": pa.Column(int, pa.Check.in_range(0, 23)),
        "dow": pa.Column(int, pa.Check.in_range(0, 6)),
        "price": pa.Column(float, pa.Check.in_range(-600, 4000), nullable=True),
        **{c: pa.Column(float, nullable=True, required=True, coerce=True) for c in FEATURES
           if c not in ("hour", "dow", "month", "is_weekend", "is_holiday")},
    },
    index=pa.Index(pd.DatetimeTZDtype(tz="UTC"), unique=True),
    strict=False,
    coerce=True,
)  # fmt: skip

PredictionSchema = pa.DataFrameSchema(
    {
        "y": pa.Column(float),
        **{c: pa.Column(float) for c in QCOLS},
    },
    index=pa.Index(pd.DatetimeTZDtype(tz="UTC"), unique=True),
    checks=pa.Check(
        lambda df: (df[QCOLS].diff(axis=1).iloc[:, 1:] >= -1e-9).all().all(),
        error="quantiles cross",
    ),
    strict=False,
)


def validate_raw(df: pd.DataFrame, max_missing_price_frac: float = 0.02) -> pd.DataFrame:
    df = RawSchema.validate(df)
    miss = df["price"].isna().mean()
    if miss > max_missing_price_frac:
        raise pa.errors.SchemaError(RawSchema, df, f"{miss:.1%} of prices missing")
    return df
