"""Synthetic Open-Meteo responses so the API can be tested offline."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

import httpx

from app.core.thermal import cos_solar_zenith, IST


def seasonal_tmax(d: date) -> float:
    # ~19 degC in Jan, ~39 degC late May/early June (roughly Ropar's climate)
    doy = d.timetuple().tm_yday
    return 29 + 10 * math.sin(2 * math.pi * (doy - 60) / 365) + (2.0 if 120 <= doy <= 165 else 0)


def seasonal_tmin(d: date) -> float:
    return seasonal_tmax(d) - 13


def daily_series(start: date, end: date, hot: dict[str, float] | None = None, seed=1):
    rnd = random.Random(seed)
    times, tx, tn = [], [], []
    d = start
    while d <= end:
        times.append(d.isoformat())
        x = seasonal_tmax(d) + rnd.gauss(0, 1.5)
        if hot and d.isoformat() in hot:
            x = hot[d.isoformat()]
        tx.append(round(x, 1))
        tn.append(round(seasonal_tmin(d) + rnd.gauss(0, 1.2), 1))
        d += timedelta(days=1)
    return times, tx, tn


def hourly_series(lat, lon, days: list[str], tmax: list[float], tmin: list[float], rh_mean=30):
    out = {k: [] for k in ["time", "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
                           "shortwave_radiation", "direct_radiation", "diffuse_radiation", "surface_pressure"]}
    for day, hi, lo in zip(days, tmax, tmin):
        for h in range(24):
            t = datetime.fromisoformat(f"{day}T{h:02d}:00")
            frac = math.cos(2 * math.pi * (h - 15) / 24)  # peak 15:00
            temp = lo + (hi - lo) * (frac + 1) / 2
            rh = max(8, min(95, rh_mean + 25 * (1 - (frac + 1) / 2) - 10))
            cz = cos_solar_zenith((t.replace(tzinfo=IST) - timedelta(minutes=30)).astimezone(timezone.utc), lat, lon)
            ghi = max(0.0, 1050 * cz ** 1.15) if cz > 0 else 0.0
            out["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
            out["temperature_2m"].append(round(temp, 1))
            out["relative_humidity_2m"].append(round(rh))
            out["wind_speed_10m"].append(2.0)
            out["shortwave_radiation"].append(round(ghi))
            out["direct_radiation"].append(round(ghi * 0.78))
            out["diffuse_radiation"].append(round(ghi * 0.22))
            out["surface_pressure"].append(978.0)
    return out


class FakeOpenMeteo:
    """Callable for httpx.MockTransport. ``scenario`` = 'hot' or 'mild'."""

    def __init__(self, scenario: str = "hot", today: date | None = None):
        self.scenario = scenario
        self.today = today or (date(2026, 3, 20) if scenario == "early" else date(2026, 5, 24))
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        self.calls.append(str(request.url))
        lat, lon = float(p["latitude"]), float(p["longitude"])
        if "archive" in request.url.host:
            start, end = date.fromisoformat(p["start_date"]), date.fromisoformat(p["end_date"])
            # districts in the south-west are ~1.5 degC hotter in the synthetic climate
            offset = 1.5 if lon < 75.2 else 0.0
            hot = {"2024-05-26": 45.6, "2024-05-27": 46.2, "2024-05-28": 47.3, "2024-05-29": 46.0,
                   "2024-05-30": 44.1, "2022-04-29": 44.0}
            times, tx, tn = daily_series(start, end, hot)
            tx = [round(x + offset, 1) for x in tx]
            body = {"latitude": lat, "longitude": lon,
                    "daily": {"time": times, "temperature_2m_max": tx, "temperature_2m_min": tn,
                              "apparent_temperature_max": [x + 2 for x in tx],
                              "precipitation_sum": [0.0] * len(times), "weather_code": [0] * len(times)}}
            if p.get("hourly"):
                body["hourly"] = hourly_series(lat, lon, times, tx, tn)
            return httpx.Response(200, json=body)

        n = int(p.get("forecast_days", 6))
        past = int(p.get("past_days", 0))
        days = [(self.today + timedelta(days=i)).isoformat() for i in range(-past, n)]
        if self.scenario == "hot":
            fx = [41.0, 43.5, 45.4, 46.1, 44.8, 42.0][:n]
            fn = [27.0, 28.5, 30.5, 32.0, 31.0, 29.0][:n]
        elif self.scenario == "early":   # March: 38-39 degC where ~28 is normal (like March 2022)
            fx = [36.5, 38.0, 39.0, 38.5, 37.0, 35.0][:n]
            fn = [20.0, 21.0, 22.0, 22.0, 21.0, 20.0][:n]
        else:
            fx = [27.0, 26.5, 28.0, 27.5, 26.0, 25.5][:n]
            fn = [13.0, 12.5, 14.0, 13.0, 12.0, 11.5][:n]
        _, px, pn = daily_series(self.today - timedelta(days=past), self.today - timedelta(days=1), seed=7)
        if self.scenario == "early":  # recent weeks were near-normal (~28 degC)
            px, pn = [28.0] * past, [14.0] * past
        tx, tn = px + fx, pn + fn
        hourly = hourly_series(lat, lon, days, tx, tn)
        n = len(days)
        body = {
            "latitude": lat, "longitude": lon,
            "current": {"time": f"{self.today.isoformat()}T14:00", "temperature_2m": tx[0] - 0.5, "relative_humidity_2m": 22,
                        "apparent_temperature": tx[0] + 1, "wind_speed_10m": 2.1, "weather_code": 0, "is_day": 1},
            "hourly": hourly,
            "daily": {"time": days, "temperature_2m_max": tx, "temperature_2m_min": tn,
                      "apparent_temperature_max": [x + 2 for x in tx], "precipitation_sum": [0.0] * n,
                      "precipitation_probability_max": [5] * n, "uv_index_max": [10.5] * n,
                      "weather_code": [0] * n, "sunrise": [None] * n, "sunset": [None] * n},
        }
        return httpx.Response(200, json=body)
