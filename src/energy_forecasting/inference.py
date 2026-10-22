"""Daily batch inference: forecast + battery schedule for delivery day D+1."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import joblib
import pandas as pd

from energy_forecasting.config import Settings, get_settings
from energy_forecasting.dispatch import Battery, make_scenarios, solve_schedule
from energy_forecasting.features import FEATURES, TZ, build_features
from energy_forecasting.ingest import NEIGHBOURS, _client, fetch_cached, to_hourly
from energy_forecasting.models import QCOLS

log = logging.getLogger(__name__)


def load_model(model_uri: str | None):
    """`models:/<name>/<stage-or-version>` from MLflow, or a local joblib path."""
    if model_uri and model_uri.startswith("models:/"):
        import mlflow
        import mlflow.pyfunc

        mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)
        return mlflow.pyfunc.load_model(model_uri)
    return joblib.load(model_uri or get_settings().processed_dir / "model.joblib")


def recent_raw(s: Settings, lookback_days: int = 45) -> pd.DataFrame:
    today = pd.Timestamp.now(tz=TZ).normalize()
    s = s.model_copy(
        update={
            "start": (today - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d"),
            "end": (today + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        }
    )
    client = _client(s)
    parts: dict[str, pd.Series] = {}
    parts["price"] = to_hourly(fetch_cached(s, client, "price", s.zone))["value"]
    parts["load_fc"] = to_hourly(fetch_cached(s, client, "load_fc", s.zone)).iloc[:, 0]
    ws = to_hourly(fetch_cached(s, client, "wind_solar_fc", s.zone))
    for col in ws.columns:
        parts["fc_" + col.lower().replace(" ", "_")] = ws[col]
    for nb in NEIGHBOURS:
        parts[f"price_{nb.lower()}"] = to_hourly(fetch_cached(s, client, "price", nb))["value"]
    return pd.DataFrame(parts).sort_index()


def run_inference(model_uri: str | None = None, out_dir: Path | None = None, rho: float = 0.7) -> dict:
    s = get_settings()
    out_dir = out_dir or Path(os.environ.get("OUTPUT_DIR", s.data_dir / "forecasts"))
    out_dir.mkdir(parents=True, exist_ok=True)
    model = load_model(model_uri)
    feats = build_features(recent_raw(s))
    target_day = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None) + pd.Timedelta(days=1)
    x = feats[feats["delivery_date"] == target_day]
    x = x.dropna(subset=["load_fc"])
    if len(x) < 23:
        raise RuntimeError(f"day-ahead forecasts for {target_day.date()} not published yet")
    q = model.predict(x[FEATURES])
    q = q[QCOLS] if hasattr(q, "columns") else pd.DataFrame(q, index=x.index, columns=QCOLS)
    scen = make_scenarios(q.to_numpy(), rho)
    batt = Battery()
    c, d = solve_schedule(scen, batt, lam=0.5)
    result = pd.DataFrame({"charge_mw": c, "discharge_mw": d, **q.reset_index(drop=True)}, index=q.index)
    stem = target_day.strftime("%Y-%m-%d")
    result.to_parquet(out_dir / f"forecast_{stem}.parquet")
    payload = {
        "delivery_date": stem,
        "quantiles": {k: v.round(2).tolist() for k, v in q.items()},
        "charge_mw": c.round(3).tolist(),
        "discharge_mw": d.round(3).tolist(),
    }
    (out_dir / "latest.json").write_text(json.dumps(payload))
    log.info("wrote forecast for %s to %s", stem, out_dir)
    return payload
