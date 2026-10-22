"""End-to-end run of every stage on synthetic data (no key, no network)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from energy_forecasting import pipeline
from energy_forecasting.config import Settings


@pytest.mark.slow
def test_full_pipeline(raw: pd.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp_path / 'mlruns'}")
    s = Settings(data_dir=tmp_path)
    s.raw_dir.mkdir(parents=True)
    raw.to_parquet(s.raw_dir / "entsoe_hourly.parquet")
    monkeypatch.setattr(pipeline, "REPORTS", tmp_path / "reports")
    # shorter backtest to keep CI fast
    from energy_forecasting import backtest

    orig = backtest.run_backtest
    monkeypatch.setattr(
        pipeline, "run_backtest",
        lambda f, r: orig(f, r, first_fold="2025-03-01", eval_start="2025-05-01", calib_months=2),
    )  # fmt: skip
    pipeline.stage_features(s)
    pipeline.stage_train(s)
    metrics = pipeline.stage_backtest(s)
    assert metrics.loc["QuantileGBM", "MAE_median"] < metrics.loc["naive_week", "MAE_median"]
    summary = pipeline.stage_dispatch(s)
    assert summary.loc["perfect_foresight", "pct_of_perfect"] == pytest.approx(100.0)
    assert summary.loc["median", "pct_of_perfect"] <= 100.0 + 1e-6
    pipeline.stage_report(s)
    assert (tmp_path / "reports" / "results.md").exists()
