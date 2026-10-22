"""Tiny read-only API serving the latest batch forecast."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

app = FastAPI(title="FR day-ahead price forecast", version="0.1.0")


def _latest() -> Path:
    return Path(os.environ.get("OUTPUT_DIR", "data/forecasts")) / "latest.json"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/forecast/latest")
def latest() -> dict:
    p = _latest()
    if not p.exists():
        raise HTTPException(404, "no forecast produced yet")
    return json.loads(p.read_text())
