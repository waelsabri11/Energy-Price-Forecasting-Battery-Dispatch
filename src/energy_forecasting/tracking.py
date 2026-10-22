"""MLflow helpers: tracking + model registry (pyfunc wrapper around the quantile GBM)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from pathlib import Path

import joblib
import mlflow
import mlflow.pyfunc
import pandas as pd
from mlflow.pyfunc import PythonModel

from energy_forecasting.config import ROOT, get_settings
from energy_forecasting.features import FEATURES
from energy_forecasting.models import QuantileGBM

log = logging.getLogger(__name__)
EXPERIMENT = "fr-dayahead-price"
REGISTERED_MODEL = "fr-dayahead-price-quantiles"


class QuantilePyfunc(PythonModel):
    """Input: feature frame (columns = FEATURES). Output: 19 quantile columns."""

    def load_context(self, context) -> None:
        self.model: QuantileGBM = joblib.load(context.artifacts["model"])

    def predict(self, context, model_input: pd.DataFrame, params=None) -> pd.DataFrame:
        return self.model.predict(model_input)


@contextlib.contextmanager
def run(name: str) -> Iterator[mlflow.ActiveRun | None]:
    """Start an MLflow run; degrade gracefully (log a warning) if the server is down."""
    uri = get_settings().mlflow_tracking_uri
    try:
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(EXPERIMENT)
        ctx = mlflow.start_run(run_name=name)
    except Exception as exc:  # noqa: BLE001
        log.warning("MLflow unavailable at %s (%s); continuing without tracking", uri, exc)
        yield None
        return
    with ctx as active:
        yield active


def register_model(model_path: Path, example: pd.DataFrame) -> None:
    mlflow.pyfunc.log_model(
        artifact_path="model",
        python_model=QuantilePyfunc(),
        artifacts={"model": str(model_path)},
        code_paths=[str(ROOT / "src" / "energy_forecasting")],
        input_example=example[FEATURES].head(5),
        registered_model_name=REGISTERED_MODEL,
        pip_requirements=["lightgbm", "scikit-learn", "pandas", "numpy", "joblib"],
    )
