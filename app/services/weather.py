"""Open-Meteo client (forecast + ERA5 historical archive).

Open-Meteo is free for non-commercial use and needs no API key.
* Forecast: https://api.open-meteo.com/v1/forecast
* Archive (ERA5 / ERA5-Land reanalysis, 1940 onwards): https://archive-api.open-meteo.com/v1/archive

Long archive downloads (decades of daily data used for climatological normals)
are cached on disk so they are fetched only once per zone.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app import config

HOURLY_VARS = [
    "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
    "shortwave_radiation", "direct_radiation", "diffuse_radiation", "surface_pressure",
]
CURRENT_VARS = [
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "wind_speed_10m", "weather_code", "is_day",
]
DAILY_FORECAST_VARS = [
    "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max",
    "precipitation_sum", "precipitation_probability_max", "uv_index_max",
    "weather_code", "sunrise", "sunset",
]
DAILY_ARCHIVE_VARS = [
    "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max",
    "precipitation_sum", "weather_code",
]


class WeatherServiceError(RuntimeError):
    pass


def call_weight(n_days: int, n_variables: int, n_locations: int = 1) -> float:
    """Open-Meteo's counting rule for the free tier:
    weight = locations x (days / 14) x (variables / 10), days counted as at least 14,
    and every request counts as at least 1 call."""
    return max(1.0, n_locations * (max(n_days, 14) / 14) * (n_variables / 10))


class RateLimiter:
    """Keeps our usage under the free limits (600 calls/min, 5,000/h, 10,000/day)
    with a safety margin, by waiting before a request that would exceed them."""

    def __init__(self, per_minute: float = 500, per_hour: float = 4500):
        self.limits = [(60.0, per_minute), (3600.0, per_hour)]
        self.log: deque[tuple[float, float]] = deque()
        self.lock = asyncio.Lock()

    def used(self, window: float, now: float) -> float:
        return sum(w for t, w in self.log if now - t < window)

    async def acquire(self, weight: float) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.log and now - self.log[0][0] > 3600:
                    self.log.popleft()
                waits = []
                for window, limit in self.limits:
                    if self.used(window, now) + weight > limit:
                        oldest = next(t for t, _ in self.log if now - t < window)
                        waits.append(window - (now - oldest) + 0.5)
                if not waits:
                    self.log.append((now, weight))
                    return
                await asyncio.sleep(min(max(waits), 65))


class OpenMeteoClient:
    def __init__(self, client: httpx.AsyncClient | None = None, cache_dir: Path | None = None,
                 rate_limit: bool = True):
        self._client = client or httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_S,
                                                   headers={"User-Agent": "ropar-heat-risk/1.0"})
        self._mem: dict[str, tuple[float, Any]] = {}
        self.limiter = RateLimiter() if rate_limit else None
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- low level ---------------------------------------------------------
    async def _get(self, url: str, params: dict, weight: float = 1.0) -> dict:
        if self.limiter:
            await self.limiter.acquire(weight)
        try:
            r = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:  # network down, DNS, timeout
            raise WeatherServiceError(f"Could not reach Open-Meteo: {exc}") from exc
        if r.status_code == 429:
            raise WeatherServiceError(
                "Open-Meteo free limit reached (600 calls/min, 5,000/hour, 10,000/day). "
                "Wait a minute (or an hour) and try again; downloaded history is kept in .cache/.")
        if r.status_code != 200:
            try:
                reason = r.json().get("reason", r.text)
            except ValueError:
                reason = r.text
            raise WeatherServiceError(f"Open-Meteo returned {r.status_code}: {reason}")
        return r.json()

    def _mem_get(self, key: str, ttl: float):
        hit = self._mem.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        return None

    # -- public ------------------------------------------------------------
    async def forecast(self, lat: float, lon: float, days: int = 6, past_days: int = 0) -> dict:
        params = {
            "latitude": lat, "longitude": lon, "timezone": config.TIMEZONE,
            "forecast_days": days, "past_days": past_days, "wind_speed_unit": "ms",
            "current": ",".join(CURRENT_VARS),
            "hourly": ",".join(HOURLY_VARS),
            "daily": ",".join(DAILY_FORECAST_VARS),
        }
        key = "fc:" + json.dumps(params, sort_keys=True)
        cached = self._mem_get(key, config.FORECAST_TTL_S)
        if cached is not None:
            return cached
        n_vars = len(CURRENT_VARS) + len(HOURLY_VARS) + len(DAILY_FORECAST_VARS)
        data = await self._get(config.FORECAST_URL, params, call_weight(days + past_days, n_vars))
        self._mem[key] = (time.time(), data)
        return data

    def _archive_path(self, key: str) -> Path:
        return self.cache_dir / ("archive_" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")

    def archive_cached(self, lat: float, lon: float, start: str, end: str, *,
                       daily: list[str] | None = None, hourly: list[str] | None = None) -> bool:
        """True if this exact archive request is already saved on disk."""
        params: dict[str, Any] = {
            "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
            "timezone": config.TIMEZONE, "wind_speed_unit": "ms",
        }
        if daily:
            params["daily"] = ",".join(daily)
        if hourly:
            params["hourly"] = ",".join(hourly)
        return self._archive_path("ar:" + json.dumps(params, sort_keys=True)).exists()

    async def archive(self, lat: float, lon: float, start: str, end: str, *,
                      daily: list[str] | None = None, hourly: list[str] | None = None,
                      disk_cache: bool = False) -> dict:
        params: dict[str, Any] = {
            "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
            "timezone": config.TIMEZONE, "wind_speed_unit": "ms",
        }
        if daily:
            params["daily"] = ",".join(daily)
        if hourly:
            params["hourly"] = ",".join(hourly)
        key = "ar:" + json.dumps(params, sort_keys=True)
        cached = self._mem_get(key, config.ARCHIVE_TTL_S)
        if cached is not None:
            return cached
        path = self._archive_path(key)
        if disk_cache and path.exists():
            data = json.loads(path.read_text())
        else:
            n_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            weight = call_weight(n_days, len(daily or []) + len(hourly or []))
            data = await self._get(config.ARCHIVE_URL, params, weight)
            if disk_cache:
                path.write_text(json.dumps(data))
        self._mem[key] = (time.time(), data)
        return data
