import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from energy_forecasting.api import app


def test_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/forecast/latest").status_code == 404
    (tmp_path / "latest.json").write_text(json.dumps({"delivery_date": "2026-01-01"}))
    assert c.get("/forecast/latest").json()["delivery_date"] == "2026-01-01"
