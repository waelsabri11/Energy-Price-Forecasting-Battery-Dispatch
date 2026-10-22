"""ENTSO-E ingestion with retries, throttling and a monthly parquet cache."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import HTTPError, Timeout
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from energy_forecasting.config import Settings, get_settings

log = logging.getLogger(__name__)

NEIGHBOURS = ("DE_LU", "BE", "ES")
MIN_INTERVAL_S = 0.25  # ENTSO-E allows 400 req/min; stay well below
_last_call = 0.0
_lock = threading.Lock()


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


class _Retryable(Exception):
    """Wrapper for transient errors (429 / 5xx / network)."""


def _is_transient(exc: HTTPError) -> bool:
    status = exc.response.status_code if exc.response is not None else 0
    return status == 429 or status >= 500


def _call(fn: Callable[[], pd.DataFrame | pd.Series]) -> pd.DataFrame | pd.Series | None:
    @retry(
        retry=retry_if_exception_type(_Retryable),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def run() -> pd.DataFrame | pd.Series | None:
        _throttle()
        try:
            return fn()
        except NoMatchingDataError:
            return None
        except HTTPError as exc:
            if _is_transient(exc):
                raise _Retryable(str(exc)) from exc
            raise
        except (ReqConnectionError, Timeout) as exc:
            raise _Retryable(str(exc)) from exc

    return run()


def _client(settings: Settings) -> EntsoePandasClient:
    key = settings.entsoe_api_key.get_secret_value()
    if not key:
        raise RuntimeError("ENTSOE_API_KEY is not set (see .env.example)")
    return EntsoePandasClient(api_key=key, retry_count=2, retry_delay=3, timeout=60)


def _fetchers(
    client: EntsoePandasClient, zone: str, kind: str
) -> Callable[[pd.Timestamp, pd.Timestamp], Any]:
    if kind == "price":
        return lambda s, e: client.query_day_ahead_prices(zone, start=s, end=e)
    if kind == "load_fc":
        return lambda s, e: client.query_load_forecast(zone, start=s, end=e)
    if kind == "wind_solar_fc":
        return lambda s, e: client.query_wind_and_solar_forecast(zone, start=s, end=e)
    raise ValueError(kind)


def fetch_cached(
    settings: Settings, client: EntsoePandasClient, kind: str, zone: str, tz: str | None = None
) -> pd.DataFrame:
    """Fetch [start, end] month by month; completed months are cached on disk."""
    tz = tz or settings.tz
    fetch = _fetchers(client, zone, kind)
    out_dir = settings.raw_dir / f"{kind}_{zone}"
    out_dir.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(settings.start, settings.end, freq="M")
    now = pd.Timestamp.now(tz="UTC")
    frames = []
    for m in months:
        path = out_dir / f"{m}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
            continue
        s = pd.Timestamp(m.start_time, tz=tz)
        e = pd.Timestamp((m + 1).start_time, tz=tz)
        raw = _call(partial(fetch, s, e))
        if raw is None:
            log.warning("no data %s %s %s", kind, zone, m)
            continue
        df = raw.to_frame("value") if isinstance(raw, pd.Series) else raw
        df = df[(df.index >= s) & (df.index < e)]
        df.columns = [str(c) if not isinstance(c, tuple) else "_".join(c) for c in df.columns]
        df.index = df.index.tz_convert("UTC")
        if e.tz_convert("UTC") < now - pd.Timedelta(days=3):
            df.to_parquet(path)
        frames.append(df)
        log.info("fetched %s %s %s (%d rows)", kind, zone, m, len(df))
    if not frames:
        raise RuntimeError(f"no data for {kind} {zone}")
    return pd.concat(frames).sort_index()


def to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Hourly mean (handles the 15-min day-ahead MTU introduced on 2025-10-01)."""
    return df.groupby(df.index.floor("h")).mean()


def ingest(settings: Settings | None = None) -> Path:
    """Download everything and write data/raw/entsoe_hourly.parquet (UTC, hourly)."""
    settings = settings or get_settings()
    client = _client(settings)
    tasks = {"price": settings.zone, "load_fc": settings.zone, "wind_solar_fc": settings.zone}
    jobs = [(k, z) for k, z in tasks.items()] + [("price", nb) for nb in NEIGHBOURS]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:  # server latency dominates
        futs = {j: pool.submit(fetch_cached, settings, client, j[0], j[1]) for j in jobs}
        res = {j: to_hourly(f.result()) for j, f in futs.items()}
    parts = {"price": res[("price", settings.zone)]["value"]}
    parts["load_fc"] = res[("load_fc", settings.zone)].iloc[:, 0]
    ws = res[("wind_solar_fc", settings.zone)]
    for col in ws.columns:
        parts["fc_" + col.lower().replace(" ", "_")] = ws[col]
    for nb in NEIGHBOURS:
        parts[f"price_{nb.lower()}"] = res[("price", nb)]["value"]
    df = pd.DataFrame(parts).sort_index()
    df.index.name = "ts_utc"
    out = settings.raw_dir / "entsoe_hourly.parquet"
    df.to_parquet(out)
    log.info("wrote %s %s", out, df.shape)
    return out
