"""Command line entry point: one command per pipeline stage."""

from __future__ import annotations

import logging

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)


@app.callback()
def main() -> None:
    """Energy price forecasting pipeline."""


@app.command()
def ingest() -> None:
    """Download ENTSO-E data into the local parquet cache."""
    from energy_forecasting.ingest import ingest as run

    typer.echo(run())


@app.command()
def features() -> None:
    """Validate raw data and build the feature table."""
    from energy_forecasting.pipeline import stage_features

    stage_features()


@app.command()
def train() -> None:
    """Fit the final quantile model and register it in MLflow."""
    from energy_forecasting.pipeline import stage_train

    stage_train()


@app.command()
def backtest() -> None:
    """Rolling-origin backtest with conformal calibration."""
    from energy_forecasting.pipeline import stage_backtest

    stage_backtest()


@app.command()
def dispatch() -> None:
    """Battery LP dispatch comparison on the backtest forecasts."""
    from energy_forecasting.pipeline import stage_dispatch

    stage_dispatch()


@app.command()
def report() -> None:
    """Write figures and reports/results.md."""
    from energy_forecasting.pipeline import stage_report

    stage_report()


@app.command()
def infer(model_uri: str = typer.Option(None, help="models:/name/version or joblib path")) -> None:
    """Forecast + battery schedule for tomorrow (needs ENTSOE_API_KEY)."""
    from energy_forecasting.inference import run_inference

    run_inference(model_uri)


@app.command()
def serve(port: int = 8000) -> None:
    """Serve the latest forecast over HTTP."""
    import uvicorn

    uvicorn.run("energy_forecasting.api:app", host="0.0.0.0", port=port)  # noqa: S104


if __name__ == "__main__":
    app()
