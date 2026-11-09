"""Punjab Heat-Risk API (22 districts).

Run:  uvicorn app.main:app --reload
Docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config
from app.core import burden, heatwave, risk, thermal, vulnerability
from app.services import assessment
from app.services.weather import OpenMeteoClient, WeatherServiceError


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.weather = OpenMeteoClient()
    # download every district's climate history once, in the background
    task = asyncio.create_task(assessment.prefetch_all_history(app.state.weather)) if config.PREFETCH_ON_START else None
    yield
    if task:
        task.cancel()
    await app.state.weather.aclose()


app = FastAPI(
    title="Punjab Heat-Risk API",
    description="Heat-wave detection, thermal stress (Heat Index + WBGT), relative heat versus each district's "
                "own climate, Census 2011 vulnerability and early-warning alerts for 22 districts of Punjab.",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_client(request: Request) -> OpenMeteoClient:
    return request.app.state.weather


@app.exception_handler(WeatherServiceError)
async def weather_error(_: Request, exc: WeatherServiceError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


DistrictId = Path(..., description="District id, e.g. ludhiana, bathinda, rupnagar (see /districts)")


def _district_or_404(district_id: str) -> dict:
    ds = vulnerability.load_districts()
    if district_id not in ds:
        raise HTTPException(404, f"Unknown district '{district_id}'. Valid: {', '.join(ds)}")
    return ds[district_id]


# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


@app.get("/districts", tags=["districts"], summary="List the 22 districts with vulnerability class")
async def list_districts():
    ranks = {r["id"]: r for r in vulnerability.ranked()}
    out = []
    for d in vulnerability.load_districts().values():
        r = ranks[d["id"]]
        out.append({"id": d["id"], "name": d["name"], "hq": d["hq"], "lat": d["lat"], "lon": d["lon"],
                    "region": d["region"], "vulnerability_index": r["index"], "vulnerability_class": r["class"],
                    "vulnerability_rank": r["rank"], "census_note": d.get("census_note")})
    return {"state": "Punjab", "count": len(out),
            "excluded": [{"name": "Malerkotla", "reason": "Formed in 2021; no Census 2011 data of its own."}],
            "districts": out}


@app.get("/districts/{district_id}/census", tags=["population"],
         summary="Census 2011 profile, vulnerability index and population risk factors")
async def district_census(district_id: str = DistrictId):
    _district_or_404(district_id)
    return vulnerability.census_profile(district_id)


@app.get("/districts/{district_id}/current", tags=["weather"], summary="Conditions and thermal stress right now")
async def district_current(district_id: str = DistrictId, client: OpenMeteoClient = Depends(get_client)):
    _district_or_404(district_id)
    a = await assessment.assess(client, district_id)
    return {"district": a["district"], "current": a["current"], "today": a["days"][0], "advice": a["advice_today"]}


@app.get("/districts/{district_id}/forecast", tags=["weather"],
         summary="Today + 5 days: absolute and relative heat, IMD check, alert and reasons")
async def district_forecast(district_id: str = DistrictId, client: OpenMeteoClient = Depends(get_client)):
    _district_or_404(district_id)
    a = await assessment.assess(client, district_id)
    return {"district": a["district"], "summary": a["summary"], "days": a["days"], "notes": a["notes"]}


@app.get("/districts/{district_id}/hourly", tags=["weather"],
         summary="Hourly Heat Index, WBGT (shade/sun) and final feels-like temperature")
async def district_hourly(district_id: str = DistrictId, client: OpenMeteoClient = Depends(get_client)):
    d = _district_or_404(district_id)
    wx = await client.forecast(d["lat"], d["lon"], days=config.FORECAST_DAYS)
    return {"district": district_id, "hours": assessment.hourly_thermal(d, wx["hourly"])}


@app.get("/districts/{district_id}/risk", tags=["risk"],
         summary="Everything for one district: alert, heat-wave answer, relative heat, population and advice")
async def district_risk(district_id: str = DistrictId,
                        replay_date: date | None = Query(None, description="Optional past start date (YYYY-MM-DD) to replay a historical event with ERA5 data, e.g. 2024-05-26"),
                        client: OpenMeteoClient = Depends(get_client)):
    _district_or_404(district_id)
    if replay_date and replay_date > date.today() - timedelta(days=7):
        raise HTTPException(422, "replay_date must be at least 7 days in the past (archive delay).")
    a = await assessment.assess(client, district_id, replay_date=replay_date)
    pop = vulnerability.census_profile(district_id)
    a["population"] = {
        "census_unit": pop["census_unit"],
        "census_note": pop["census_note"],
        "population_2011": pop["population_2011"],
        "population_estimate_now": pop["population_estimate_now"],
        "vulnerability": pop["vulnerability"],
        "risk_factors": pop["risk_factors"],
        "regional_context": pop["regional_context"],
        "vulnerable_groups": pop["vulnerable_groups"],
    }
    return a


@app.get("/districts/{district_id}/history", tags=["history"],
         summary="Heat statistics from historical data (default 2020-2025)")
async def district_history(district_id: str = DistrictId,
                           start_year: int = Query(config.HISTORY_START_YEAR, ge=1991),
                           end_year: int = Query(config.HISTORY_END_YEAR, le=config.HISTORY_END_YEAR),
                           client: OpenMeteoClient = Depends(get_client)):
    _district_or_404(district_id)
    if start_year > end_year:
        raise HTTPException(422, "start_year must be <= end_year")
    return await assessment.history(client, district_id, start_year, end_year)


@app.get("/districts/{district_id}/climate", tags=["history"],
         summary="1991-2020 normal Tmax and percentiles through the year (what 'normal' means here)")
async def district_climate(district_id: str = DistrictId, client: OpenMeteoClient = Depends(get_client)):
    _district_or_404(district_id)
    return await assessment.climate_profile(client, district_id)


@app.get("/punjab/overview", tags=["risk"], summary="Alert for every district, worst first (for a state map)")
async def punjab_overview(client: OpenMeteoClient = Depends(get_client)):
    ids = list(vulnerability.load_districts())
    sem = asyncio.Semaphore(4)  # be gentle with the free API

    async def one(i):
        async with sem:
            return await assessment.assess(client, i)

    results = await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)
    out = []
    for i, r in zip(ids, results):
        if isinstance(r, Exception):
            out.append({"district": {"id": i}, "error": str(r)})
            continue
        today = r["days"][0]
        out.append({
            "district": r["district"], "max_alert": r["summary"]["max_alert"],
            "max_severity_score": r["summary"]["max_severity_score"], "worst_day": r["summary"]["worst_day"],
            "heatwave_expected": r["summary"]["heatwave_expected"], "headline": r["summary"]["headline"],
            "today_alert": today["alert"], "today_tmax_c": today["tmax_c"],
            "today_anomaly_c": today["relative"]["anomaly_c"], "peak_feels_like_c": r["summary"]["peak_feels_like_c"],
            "max_anomaly_c": r["summary"]["max_anomaly_c"], "vulnerability_class": r["vulnerability"]["class"],
        })
    out.sort(key=lambda x: (-(x.get("max_alert") or {}).get("level", -1), -(x.get("max_severity_score") or 0)))
    return {"districts": out}


@app.get("/status", tags=["meta"], summary="How much climate history has been downloaded")
async def status(client: OpenMeteoClient = Depends(get_client)):
    st = assessment.download_status(client)
    try:
        await assessment.get_burden(client)
        st["heat_burden_ready"] = True
    except assessment.BurdenPending:
        st["heat_burden_ready"] = False
    return st


@app.get("/punjab/heat-burden", tags=["history"],
         summary="Historical heat-burden index 2020-2025 for all districts (feeds the alert)")
async def punjab_heat_burden(client: OpenMeteoClient = Depends(get_client)):
    try:
        b = await assessment.get_burden(client)
    except assessment.BurdenPending as exc:
        raise HTTPException(202, f"Not ready yet: {exc}. Check /status.")
    names = {d["id"]: d["name"] for d in vulnerability.load_districts().values()}
    rows = sorted(({"id": k, "name": names[k], **v} for k, v in b.items()), key=lambda r: r["rank"])
    return {"method": burden.__doc__, "period": rows[0]["period"] if rows else None, "ranking": rows}


@app.get("/punjab/vulnerability", tags=["population"], summary="All districts ranked by Census vulnerability index")
async def punjab_vulnerability():
    return {"method": vulnerability.vulnerability_index("rupnagar")["method"], "ranking": vulnerability.ranked()}


@app.get("/calculate", tags=["tools"], summary="Thermal indices and the risk matrix for any input (testing / teaching)")
async def calculate(t: float = Query(..., description="Air temperature (= Tmax), degC"),
                    rh: float = Query(..., ge=1, le=100, description="Relative humidity, %"),
                    ghi: float = Query(0, ge=0, le=1400, description="Global solar radiation, W/m2"),
                    direct_fraction: float = Query(0.75, ge=0, le=1),
                    wind: float = Query(1.5, ge=0, description="Wind speed at 10 m, m/s"),
                    sun_elevation_deg: float = Query(60, ge=-90, le=90),
                    pressure: float = Query(980, description="Surface pressure, hPa"),
                    normal_tmax: float | None = Query(None, description="Local normal Tmax for the date, degC (to test the relative layer)"),
                    percentile: float | None = Query(None, ge=0, le=100),
                    acclimatisation: float | None = Query(None, description="Last-3-days minus previous-30-days mean temperature, degC"),
                    vulnerability_class: str = Query("Moderate", pattern="^(Low|Moderate|High)$"),
                    heat_burden_class: str = Query("Moderate", pattern="^(Low|Moderate|High)$")):
    cz = math.sin(math.radians(sun_elevation_deg))
    r = thermal.compute(t, rh, ghi_wm2=ghi, direct_wm2=ghi * direct_fraction, wind_10m_ms=wind,
                        pressure_hpa=pressure, cos_zenith=cz)
    absolute = risk.absolute_level(t, r.final_feels_like_c, r.final_category)
    relative = risk.relative_level(t, normal_tmax, percentile, acclimatisation)
    day = risk.classify_days([{"absolute": absolute, "relative": relative}], vulnerability_class, heat_burden_class)[0]
    return {"thermal": r.as_dict(), "absolute": absolute, "relative": relative,
            "imd": heatwave.classify_day(t, None, normal_tmax, None),
            "matrix_level": day["matrix_level"], "alert": day["alert"],
            "severity_score": day["severity_score"], "reasons": day["reasons"]}


@app.get("/methodology", tags=["meta"], summary="How every number is calculated")
async def methodology():
    return {
        "thermal": thermal.__doc__,
        "heatwave": heatwave.__doc__,
        "relative_heat": assessment.climatology.__doc__,
        "risk": risk.__doc__,
        "risk_matrix": {"rows_absolute": risk.ABS_NAMES, "columns_relative": risk.REL_NAMES, "matrix": risk.MATRIX},
        "vulnerability": vulnerability.__doc__,
        "heat_burden": burden.__doc__,
        "alert_levels": risk.LEVELS,
        "wbgt_flags": [{"min_wbgt_c": th, "category": c, "flag": f, "work_rest_moderate_work": wr, "advice": a}
                       for th, c, f, wr, a in thermal.WBGT_FLAGS],
        "heat_index_categories": [{"min_c": th, "category": n, "effect": e} for th, n, e in thermal.HI_CATEGORIES],
        "data_sources": {
            "forecast": config.FORECAST_URL,
            "historical": config.ARCHIVE_URL + " (ERA5 / ERA5-Land reanalysis)",
            "census": "Census of India 2011, district tables (see /districts/{id}/census)",
        },
    }
