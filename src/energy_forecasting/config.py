"""Central settings, loaded from environment / .env (never hard-code secrets)."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    entsoe_api_key: SecretStr = SecretStr("")
    mlflow_tracking_uri: str = "file:./mlruns"
    zone: str = "FR"
    tz: str = "Europe/Paris"
    start: str = "2022-01-01"
    end: str = "2026-10-20"
    data_dir: Path = ROOT / "data"
    seed: int = 42

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


def get_settings() -> Settings:
    return Settings()
