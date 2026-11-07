"""Builds the full heat-risk assessment for a district from weather + climate history + census."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app import config
import asyncio
import json

from app.core import burden, climatology, heatwave, risk, thermal, vulnerability
from app.services.weather import DAILY_ARCHIVE_VARS, HOURLY_VARS, OpenMeteoClient

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Heavy rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}


def describe_code(code) -> str | None:
    return None if code is None else WMO_CODES.get(int(code), f"Code {code}")


def _get(d: dict, key: str, i: int):
    arr = d.get(key)
    return None if arr is None or i >= len(arr) else arr[i]


# ---------------------------------------------------------------------------
# Climate history (normals 1991-2020 + history 2020-2025 from one cached download)
# ---------------------------------------------------------------------------
LONG_VARS = ["temperature_2m_max", "temperature_2m_min"]


def _long_range() -> tuple[str, str]:
    return (f"{min(config.NORMAL_START_YEAR, config.HISTORY_START_YEAR)}-01-01",
            f"{max(config.NORMAL_END_YEAR, config.HISTORY_END_YEAR)}-12-31")


def history_cached(client: OpenMeteoClient, district: dict) -> bool:
    start, end = _long_range()
    return client.archive_cached(district["lat"], district["lon"], start, end, daily=LONG_VARS)


async def _long_daily(client: OpenMeteoClient, district: dict) -> dict:
    start, end = _long_range()
    data = await client.archive(district["lat"], district["lon"], start, end, daily=LONG_VARS, disk_cache=True)
    return data["daily"]


_NORMALS_CACHE: dict[str, dict] = {}


async def get_normals(client: OpenMeteoClient, district: dict) -> dict[int, dict]:
    key = f"{district['lat']},{district['lon']}"
    if key in _NORMALS_CACHE:
        return _NORMALS_CACHE[key]
    d = await _long_daily(client, district)
    keep = [i for i, t in enumerate(d["time"])
            if config.NORMAL_START_YEAR <= int(t[:4]) <= config.NORMAL_END_YEAR]
    normals = climatology.build_normals([d["time"][i] for i in keep],
                                        [d["temperature_2m_max"][i] for i in keep],
                                        [d["temperature_2m_min"][i] for i in keep],
                                        window=config.NORMAL_WINDOW_DAYS)
    _NORMALS_CACHE[key] = normals
    return normals


async def history(client: OpenMeteoClient, district_id: str, start_year: int, end_year: int) -> dict:
    district = vulnerability.load_districts()[district_id]
    d = await _long_daily(client, district)
    normals = await get_normals(client, district)
    stats = climatology.history_stats(d["time"], d["temperature_2m_max"], d["temperature_2m_min"],
                                      normals, start_year, end_year)
    hb = None
    if (start_year, end_year) == (config.HISTORY_START_YEAR, config.HISTORY_END_YEAR):
        try:
            hb = (await get_burden(client)).get(district_id)
        except BurdenPending as exc:
            hb = {"status": "pending", "detail": str(exc)}
        except Exception:  # other districts unavailable: still return this district's history
            hb = None
    return {
        "district": {"id": district["id"], "name": district["name"]},
        "period": f"{start_year}-{end_year}",
        "heat_burden": hb,
        "normal_period": f"{config.NORMAL_START_YEAR}-{config.NORMAL_END_YEAR}",
        "source": "ERA5 reanalysis via Open-Meteo archive (about 25 km grid; tends to read 1-3 degC cooler than "
                  "station maxima, but departures and percentiles use its own normals, so they stay consistent).",
        **stats,
    }


_BURDEN_CACHE: dict[str, dict] = {}
_BURDEN_LOCK = asyncio.Lock()


class BurdenPending(RuntimeError):
    """History for some districts is still being downloaded."""


def download_status(client: OpenMeteoClient) -> dict:
    ds = vulnerability.load_districts()
    done = [i for i, d in ds.items() if history_cached(client, d)]
    return {"downloaded": len(done), "total": len(ds), "missing": [i for i in ds if i not in done]}


async def prefetch_all_history(client: OpenMeteoClient) -> None:
    """Download every district's 1991-2025 daily history once (about 183 Open-Meteo
    calls each, ~4,000 in total), paced by the rate limiter, then build the
    heat-burden index. Runs in the background at server start; resumes from .cache/."""
    for d in vulnerability.load_districts().values():
        if not history_cached(client, d):
            try:
                await _long_daily(client, d)
            except Exception:  # network problem: try again next start, keep the rest working
                return
    await get_burden(client, allow_download=True)


async def get_burden(client: OpenMeteoClient, allow_download: bool = False) -> dict[str, dict]:
    """Historical heat-burden index for all districts (computed once, cached in
    memory and in .cache/). Needs every district's history; unless
    ``allow_download`` it raises BurdenPending while downloads are incomplete."""
    y0, y1 = config.HISTORY_START_YEAR, config.HISTORY_END_YEAR
    key = f"{y0}-{y1}"
    if key in _BURDEN_CACHE:
        return _BURDEN_CACHE[key]
    if not allow_download:
        st = download_status(client)
        path = client.cache_dir / f"heat_burden_{key}_{st['total']}.json"
        if st["downloaded"] < st["total"] and not path.exists():
            raise BurdenPending(f"history downloaded for {st['downloaded']} of {st['total']} districts; "
                                "the server downloads the rest in the background (about 8 minutes on first start)")
    async with _BURDEN_LOCK:
        if key in _BURDEN_CACHE:
            return _BURDEN_CACHE[key]
        path = client.cache_dir / f"heat_burden_{key}_{len(vulnerability.load_districts())}.json"
        if path.exists():
            result = json.loads(path.read_text())
        else:
            sem = asyncio.Semaphore(4)

            async def one(did: str):
                async with sem:
                    d = vulnerability.load_districts()[did]
                    daily = await _long_daily(client, d)
                    normals = await get_normals(client, d)
                    stats = climatology.history_stats(daily["time"], daily["temperature_2m_max"],
                                                      daily["temperature_2m_min"], normals, y0, y1)
                    return did, stats["averages"]

            pairs = await asyncio.gather(*(one(i) for i in vulnerability.load_districts()))
            result = burden.build_index(dict(pairs), key)
            path.write_text(json.dumps(result))
        _BURDEN_CACHE[key] = result
        return result


async def climate_profile(client: OpenMeteoClient, district_id: str) -> dict:
    """Normal Tmax and percentiles for the 1st and 15th of each month."""
    district = vulnerability.load_districts()[district_id]
    normals = await get_normals(client, district)
    rows = []
    for m in range(1, 13):
        for day in (1, 15):
            n = climatology.public_normal(climatology.normal_for(normals, date(2001, m, day)))
            rows.append({"date": f"{m:02d}-{day:02d}", **n})
    return {"district": {"id": district["id"], "name": district["name"]},
            "normal_period": f"{config.NORMAL_START_YEAR}-{config.NORMAL_END_YEAR}", "calendar": rows}


# ---------------------------------------------------------------------------
# Hourly + daily processing
# ---------------------------------------------------------------------------
def hourly_thermal(district: dict, hourly: dict, from_date: str | None = None) -> list[dict]:
    out = []
    for i, t in enumerate(hourly["time"]):
        if from_date and t[:10] < from_date:
            continue
        temp = hourly["temperature_2m"][i]
        rh = hourly["relative_humidity_2m"][i]
        if temp is None or rh is None:
            continue
        res = thermal.compute_for_hour(
            datetime.fromisoformat(t), district["lat"], district["lon"], t_c=temp, rh_pct=rh,
            ghi_wm2=_get(hourly, "shortwave_radiation", i), direct_wm2=_get(hourly, "direct_radiation", i),
            wind_10m_ms=_get(hourly, "wind_speed_10m", i), pressure_hpa=_get(hourly, "surface_pressure", i),
        )
        out.append({"time": t, **res.as_dict()})
    return out


def _ranges(hours: list[str]) -> list[str]:
    if not hours:
        return []
    hs = sorted(int(h[11:13]) for h in hours)
    out, start, prev = [], hs[0], hs[0]
    for h in hs[1:]:
        if h != prev + 1:
            out.append(f"{start:02d}:00-{prev + 1:02d}:00")
            start = h
        prev = h
    out.append(f"{start:02d}:00-{prev + 1:02d}:00")
    return out


def acclimatisation_series(daily: dict) -> dict[str, float | None]:
    """For each day: mean(T) of that day and the 2 before, minus mean(T) of the
    30 days before those 3 (T = (Tmax + Tmin) / 2)."""
    times = daily["time"]
    tm = [None if (a is None or b is None) else (a + b) / 2
          for a, b in zip(daily["temperature_2m_max"], daily["temperature_2m_min"])]
    out = {}
    for i, t in enumerate(times):
        last3 = tm[max(0, i - 2): i + 1]
        prev30 = tm[max(0, i - 32): max(0, i - 2)]
        if len(last3) < 3 or len(prev30) < 30 or None in last3 or None in prev30:
            out[t] = None
        else:
            out[t] = round(sum(last3) / 3 - sum(prev30) / 30, 1)
    return out


def build_days(district: dict, daily: dict, hours: list[dict], normals: dict | None,
               vuln_class: str, first_day: str, burden_class: str | None = None) -> list[dict]:
    by_day: dict[str, list[dict]] = {}
    for h in hours:
        by_day.setdefault(h["time"][:10], []).append(h)
    accl = acclimatisation_series(daily)

    days = []
    for i, day in enumerate(daily["time"]):
        if day < first_day:
            continue
        tmax, tmin = daily["temperature_2m_max"][i], daily["temperature_2m_min"][i]
        if tmax is None:
            continue
        dd = date.fromisoformat(day)
        hrs = by_day.get(day, [])
        n = climatology.normal_for(normals, dd)
        imd = heatwave.classify_day(tmax, tmin, n["tmax"], n["tmin"])
        if hrs:
            peak = max(hrs, key=lambda h: h["final_feels_like_c"])
            peak_hi = max(h["heat_index_c"] for h in hrs)
            peak_wbgt = max(hrs, key=lambda h: h["wbgt_sun_c"])
            unsafe = [h["time"] for h in hrs if h["wbgt_category"] >= 4]
            caution = [h["time"] for h in hrs if h["wbgt_category"] >= 3]
            danger_hours = [h["time"] for h in hrs if h["final_feels_like_c"] >= 39.4]
        else:
            app_max = _get(daily, "apparent_temperature_max", i) or tmax
            peak = {"final_feels_like_c": app_max, "final_category": thermal.hi_category(app_max)["category"],
                    "time": None, "solar_load_c": None}
            peak_hi, peak_wbgt, unsafe, caution, danger_hours = None, None, [], [], []

        absolute = risk.absolute_level(tmax, peak["final_feels_like_c"], peak["final_category"])
        relative = risk.relative_level(tmax, n["tmax"], climatology.percentile_rank(normals, dd, tmax), accl.get(day))
        days.append({
            "date": day,
            "weekday": dd.strftime("%a"),
            "weather": describe_code(_get(daily, "weather_code", i)),
            "tmax_c": tmax,
            "tmin_c": tmin,
            "normal_tmax_c": n["tmax"],
            "normal_tmin_c": n["tmin"],
            "tmax_p95_c": n.get("tmax_p95"),
            "precipitation_mm": _get(daily, "precipitation_sum", i),
            "precipitation_probability_pct": _get(daily, "precipitation_probability_max", i),
            "uv_index_max": _get(daily, "uv_index_max", i),
            "imd": imd,
            "thermal": {
                "peak_final_feels_like_c": peak["final_feels_like_c"],
                "peak_final_time": peak["time"],
                "peak_final_category": peak["final_category"],
                "peak_heat_index_shade_c": peak_hi,
                "solar_load_at_peak_c": peak.get("solar_load_c"),
                "peak_wbgt_sun_c": peak_wbgt["wbgt_sun_c"] if peak_wbgt else None,
                "peak_wbgt_flag": peak_wbgt["wbgt_flag"] if peak_wbgt else None,
                "peak_work_rest": peak_wbgt["work_rest"] if peak_wbgt else None,
                "danger_hours": _ranges(danger_hours),
                "outdoor_work_unsafe_hours": _ranges(unsafe),
                "outdoor_work_caution_hours": _ranges(caution),
            },
            "absolute": absolute,
            "relative": relative,
        })
    heatwave.mark_spells([d["imd"] for d in days])
    for d in days:
        if d["imd"]["is_heatwave"]:
            d["extra_reasons"] = [f"IMD heat-wave criteria met ({d['imd']['criterion']})"
                                  + (f"; part of a {d['imd']['spell_length']}-day spell" if d["imd"]["in_spell"] else "")]
    return risk.classify_days(days, vuln_class, burden_class)


def summarise(days: list[dict], district: dict) -> dict:
    worst = max(days, key=lambda d: (d["risk_level"], d["severity_score"]))
    hw_days = [d for d in days if d["imd"]["is_heatwave"]]
    unusual = [d for d in days if d["relative"]["level"] >= 2 and d["absolute"]["level"] >= 1]
    hot_days = [d for d in days if d["tmax_c"] >= 40]
    fmt = lambda ds: ", ".join(f"{d['weekday']} {d['date'][5:]}" for d in ds)
    if hw_days:
        status = "heatwave_expected"
        msg = (f"Heat-wave conditions likely in {district['name']} on {fmt(hw_days)} "
               f"(Tmax up to {max(d['tmax_c'] for d in hw_days):.1f} degC).")
    elif unusual:
        status = "unusual_heat_no_heatwave"
        top = max(unusual, key=lambda d: d["relative"]["anomaly_c"] or 0)
        msg = (f"No heat wave by IMD criteria, but unusually hot for the season on {fmt(unusual)} "
               f"(up to {top['relative']['anomaly_c']:+.1f} degC above normal).")
    elif hot_days:
        status = "hot_days_no_heatwave"
        msg = f"No heat wave; very hot but near-normal for the season on {fmt(hot_days)}."
    else:
        status = "no_heatwave"
        msg = (f"No heat wave expected in {district['name']} over the next {len(days)} days. "
               f"Highest temperature {max(d['tmax_c'] for d in days):.1f} degC.")
    anoms = [d["relative"]["anomaly_c"] for d in days if d["relative"]["anomaly_c"] is not None]
    return {
        "heatwave_status": status,
        "heatwave_expected": bool(hw_days),
        "first_heatwave_day": hw_days[0]["date"] if hw_days else None,
        "heatwave_days": [d["date"] for d in hw_days],
        "unusual_heat_days": [d["date"] for d in unusual],
        "max_risk_level": worst["risk_level"],
        "max_alert": worst["alert"],
        "max_severity_score": worst["severity_score"],
        "worst_day": worst["date"],
        "peak_feels_like_c": worst["thermal"]["peak_final_feels_like_c"],
        "max_anomaly_c": max(anoms) if anoms else None,
        "headline": msg,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def assess(client: OpenMeteoClient, district_id: str, replay_date: date | None = None,
                 days: int = config.FORECAST_DAYS) -> dict:
    district = vulnerability.load_districts()[district_id]
    vuln = vulnerability.vulnerability_index(district["census"])

    normals, normals_note = None, None
    try:
        normals = await get_normals(client, district)
    except Exception as exc:  # keep working with absolute thresholds only
        normals_note = f"Climate history unavailable ({exc}); relative level set to 0 and departure-based IMD rules skipped."

    hb, burden_note = None, None
    try:
        hb = (await get_burden(client)).get(district_id)
    except BurdenPending as exc:
        burden_note = f"Historical heat-burden index is being prepared ({exc}); no burden adjustment applied yet."
    except Exception as exc:
        burden_note = f"Historical heat-burden index unavailable ({exc}); no burden adjustment applied."

    if replay_date is None:
        wx = await client.forecast(district["lat"], district["lon"], days=days, past_days=config.PAST_DAYS)
        first_day = wx["daily"]["time"][-days]
        mode, source = "forecast", "Open-Meteo forecast (best-match models: ECMWF IFS, GFS, ICON, etc.)"
    else:
        start = replay_date - timedelta(days=config.PAST_DAYS)
        end = replay_date + timedelta(days=days - 1)
        wx = await client.archive(district["lat"], district["lon"], start.isoformat(), end.isoformat(),
                                  daily=DAILY_ARCHIVE_VARS, hourly=HOURLY_VARS)
        first_day = replay_date.isoformat()
        mode, source = "replay", "ERA5 reanalysis via Open-Meteo archive (past event replay)"

    hours = hourly_thermal(district, wx["hourly"], from_date=first_day)
    day_list = build_days(district, wx["daily"], hours, normals, vuln["class"], first_day,
                          hb["class"] if hb else None)

    if mode == "forecast" and "current" in wx:
        cur = wx["current"]
        hour_key = cur["time"][:13]
        hv = {k: None for k in ("shortwave_radiation", "direct_radiation", "surface_pressure")}
        h = wx.get("hourly", {})
        for i, t in enumerate(h.get("time", [])):
            if t[:13] == hour_key:
                hv = {k: _get(h, k, i) for k in hv}
                break
        now_thermal = thermal.compute_for_hour(
            datetime.fromisoformat(cur["time"]), district["lat"], district["lon"],
            t_c=cur["temperature_2m"], rh_pct=cur["relative_humidity_2m"],
            ghi_wm2=hv["shortwave_radiation"], direct_wm2=hv["direct_radiation"],
            wind_10m_ms=cur.get("wind_speed_10m"), pressure_hpa=hv["surface_pressure"],
        ).as_dict()
        current = {
            "time": cur["time"], "weather": describe_code(cur.get("weather_code")),
            "temperature_c": cur["temperature_2m"], "relative_humidity_pct": cur["relative_humidity_2m"],
            "apparent_temperature_c": cur.get("apparent_temperature"), "wind_speed_ms": cur.get("wind_speed_10m"),
            "is_day": bool(cur.get("is_day", 1)), "thermal": now_thermal,
        }
    else:
        first = day_list[0]["date"]
        h14 = next((h for h in hours if h["time"].startswith(first + "T14")), hours[0] if hours else None)
        current = {"time": h14["time"] if h14 else None,
                   "note": "Replay mode: conditions at 14:00 on the first day.", "thermal": h14}

    today_level = day_list[0]["risk_level"]
    summary = summarise(day_list, district)
    return {
        "district": {k: district[k] for k in ("id", "name", "hq", "lat", "lon", "region")},
        "mode": mode,
        "generated_at": datetime.now(thermal.IST).isoformat(timespec="seconds"),
        "weather_source": source,
        "summary": summary,
        "current": current,
        "days": day_list,
        "vulnerability": {"index": vuln["index"], "class": vuln["class"]},
        "heat_burden": None if hb is None else {
            "index": hb["index"], "class": hb["class"], "rank": hb["rank"], "rank_of": hb["rank_of"],
            "period": hb["period"],
            "averages": {c["metric"]: c["value"] for c in hb["components"]},
        },
        "advice_today": {"level": today_level, **risk.advice_for(today_level)},
        "advice_peak": {"level": summary["max_risk_level"], **risk.advice_for(summary["max_risk_level"])},
        "notes": [n for n in [
            normals_note, burden_note,
            "Alert = matrix of ABSOLUTE heat load (feels-like in sun, Tmax) x RELATIVE heat (anomaly and percentile "
            "vs this district's 1991-2020 climate, plus sudden-rise check), then adjusted for vulnerability and persistence. See /methodology.",
            "Heat-wave status is for the district headquarters; IMD officially declares heat waves when 2+ stations meet the criteria for 2+ days.",
        ] if n],
    }
