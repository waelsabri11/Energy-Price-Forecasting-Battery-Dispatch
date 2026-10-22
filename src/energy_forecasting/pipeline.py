"""Pipeline stages: features -> train -> backtest -> dispatch -> report.

Each stage reads/writes parquet in `settings.processed_dir` and validates its inputs
and outputs with pandera.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import pandas as pd

from energy_forecasting import tracking
from energy_forecasting.backtest import evaluate, run_backtest
from energy_forecasting.config import ROOT, Settings, get_settings
from energy_forecasting.dispatch import Battery, run_dispatch_backtest, summarise
from energy_forecasting.features import FEATURES, build_features
from energy_forecasting.models import QCOLS, QuantileGBM
from energy_forecasting.schemas import FeatureSchema, PredictionSchema, validate_raw

log = logging.getLogger(__name__)
REPORTS = ROOT / "reports"


def _paths(s: Settings) -> dict[str, Path]:
    s.processed_dir.mkdir(parents=True, exist_ok=True)
    return {
        "raw": s.raw_dir / "entsoe_hourly.parquet",
        "features": s.processed_dir / "features.parquet",
        "model": s.processed_dir / "model.joblib",
        "pred": s.processed_dir / "backtest_predictions.parquet",
        "metrics": s.processed_dir / "backtest_metrics.csv",
        "daily": s.processed_dir / "dispatch_daily.parquet",
        "dispatch": s.processed_dir / "dispatch_summary.csv",
        "meta": s.processed_dir / "meta.json",
    }


def load_raw(path: Path) -> pd.DataFrame:
    return validate_raw(pd.read_parquet(path))


def stage_features(s: Settings | None = None) -> Path:
    s = s or get_settings()
    p = _paths(s)
    raw = load_raw(p["raw"])
    feats = FeatureSchema.validate(build_features(raw))
    feats.to_parquet(p["features"])
    log.info("features: %s rows, %.1f%% target-missing", len(feats), 100 * feats["price"].isna().mean())
    return p["features"]


def stage_train(s: Settings | None = None) -> Path:
    s = s or get_settings()
    p = _paths(s)
    feats = FeatureSchema.validate(pd.read_parquet(p["features"]))
    train = feats.dropna(subset=["price"])
    model = QuantileGBM().fit(train)
    joblib.dump(model, p["model"])
    with tracking.run("train") as active:
        if active:
            mlflow.log_params({**model.params, "n_train_rows": len(train), "n_features": len(FEATURES),
                               "train_end": str(train.index.max())})  # fmt: skip
            tracking.register_model(p["model"], train)
    return p["model"]


def stage_backtest(s: Settings | None = None) -> pd.DataFrame:
    s = s or get_settings()
    p = _paths(s)
    feats = FeatureSchema.validate(pd.read_parquet(p["features"]))
    raw = load_raw(p["raw"])
    pred = run_backtest(feats, raw)
    PredictionSchema.validate(pred)
    pred.to_parquet(p["pred"])
    metrics = evaluate(pred)
    metrics.to_csv(p["metrics"])
    with tracking.run("backtest") as active:
        if active:
            for method, row in metrics.iterrows():
                mlflow.log_metrics({f"{method}.{k}".replace(" ", "_").replace("+", "plus"): v
                                    for k, v in row.dropna().items()})  # fmt: skip
            mlflow.log_artifact(str(p["metrics"]))
    log.info("\n%s", metrics.round(3).to_string())
    return metrics


def stage_dispatch(s: Settings | None = None, batt: Battery | None = None) -> pd.DataFrame:
    s = s or get_settings()
    batt = batt or Battery()
    p = _paths(s)
    pred = PredictionSchema.validate(pd.read_parquet(p["pred"]))
    daily, rho = run_dispatch_backtest(pred, batt, QCOLS, lambdas=(0.5, 0.9), seed=s.seed)
    daily.to_parquet(p["daily"])
    summary = summarise(daily, batt)
    summary.to_csv(p["dispatch"])
    p["meta"].write_text(json.dumps({"rho": rho, "n_days": len(daily), "battery": batt.__dict__}))
    with tracking.run("dispatch") as active:
        if active:
            mlflow.log_params({**batt.__dict__, "copula_rho": round(rho, 3)})
            for strat, row in summary.iterrows():
                mlflow.log_metrics({f"{strat}.{k}": v for k, v in row.items()})
            mlflow.log_artifact(str(p["dispatch"]))
    log.info("\n%s", summary.round(1).to_string())
    return summary


def stage_report(s: Settings | None = None) -> None:
    """Figures + results.md fragments from the stage outputs."""
    s = s or get_settings()
    p = _paths(s)
    (REPORTS / "figures").mkdir(parents=True, exist_ok=True)
    pred = pd.read_parquet(p["pred"])

    # forecast fan chart for a sample week
    week = pred.loc[pred.index >= pred.index[len(pred) // 2]].iloc[: 24 * 7]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.fill_between(week.index, week["c0.9_lo"], week["c0.9_hi"], alpha=0.2, label="90% (CQR)")
    ax.fill_between(week.index, week["c0.8_lo"], week["c0.8_hi"], alpha=0.35, label="80% (CQR)")
    ax.plot(week.index, week["q0.50"], lw=1.2, label="median")
    ax.plot(week.index, week["y"], "k", lw=1, label="actual")
    ax.set_ylabel("EUR/MWh")
    ax.legend(ncol=4, fontsize=8)
    ax.set_title("FR day-ahead price: probabilistic forecast (sample week)")
    fig.tight_layout()
    fig.savefig(REPORTS / "figures" / "forecast_week.png", dpi=130)
    plt.close(fig)

    if p["daily"].exists():
        daily = pd.read_parquet(p["daily"])
        cum = daily.cumsum() / 1e3
        fig, ax = plt.subplots(figsize=(10, 4))
        for col in cum.columns:
            ax.plot(cum.index, cum[col], label=col)
        ax.set_ylabel("cumulative revenue, kEUR")
        ax.legend(fontsize=8)
        ax.set_title("Battery dispatch: cumulative revenue at realised prices")
        fig.tight_layout()
        fig.savefig(REPORTS / "figures" / "dispatch_cumulative.png", dpi=130)
        plt.close(fig)

    parts = []
    if p["metrics"].exists():
        parts.append(
            "## Forecast metrics\n\n" + pd.read_csv(p["metrics"], index_col=0).round(3).to_markdown()
        )
    if p["dispatch"].exists():
        parts.append("## Dispatch\n\n" + pd.read_csv(p["dispatch"], index_col=0).round(1).to_markdown())
    (REPORTS / "results.md").write_text("\n\n".join(parts) + "\n")
