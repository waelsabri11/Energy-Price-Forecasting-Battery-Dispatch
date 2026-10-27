"""Register data/processed/model.joblib in the MLflow server given by MLFLOW_TRACKING_URI."""

from __future__ import annotations

import pandas as pd

from energy_forecasting import tracking
from energy_forecasting.config import get_settings

s = get_settings()
feats = pd.read_parquet(s.processed_dir / "features.parquet").dropna(subset=["price"])
with tracking.run("seed-from-local") as active:
    assert active is not None, "MLflow unreachable"
    tracking.register_model(s.processed_dir / "model.joblib", feats)
print("registered", tracking.REGISTERED_MODEL)
