"""End-to-end API tests with a fake Open-Meteo (no internet needed)."""


def test_districts(hot_api):
    r = hot_api.get("/districts").json()
    ids = [d["id"] for d in r["districts"]]
    assert r["count"] == 22 and "malerkotla" not in ids
    assert {"ludhiana", "bathinda", "rupnagar", "pathankot", "fazilka"} <= set(ids)


def test_unknown_district(hot_api):
    assert hot_api.get("/districts/malerkotla/risk").status_code == 404


def test_census(hot_api):
    r = hot_api.get("/districts/mansa/census").json()
    assert r["vulnerability"]["class"] == "High" and r["vulnerability"]["rank"] == 1
    assert any(f["factor"] == "Outdoor agricultural work" for f in r["risk_factors"])
    f = hot_api.get("/districts/fazilka/census").json()
    assert f["census_unit"] == "Firozpur" and f["census_note"]


def test_hot_forecast(hot_api):
    r = hot_api.get("/districts/bathinda/risk")
    assert r.status_code == 200, r.text
    a = r.json()
    assert len(a["days"]) == 6 and a["days"][0]["date"] == "2026-05-24"
    assert a["summary"]["heatwave_expected"] is True
    assert a["summary"]["max_alert"]["color"] == "Red"
    d = a["days"][3]
    assert {"absolute", "relative", "matrix_level", "reasons"} <= set(d)
    assert d["relative"]["percentile"] is not None and d["relative"]["acclimatisation_c"] is not None
    assert d["thermal"]["peak_final_feels_like_c"] > d["thermal"]["peak_heat_index_shade_c"]
    assert a["population"]["population_2011"] == 1_388_525


def test_same_weather_different_climate(hot_api):
    """Identical forecast: the district with the cooler climate gets the larger anomaly."""
    sw = hot_api.get("/districts/bathinda/forecast").json()["days"][1]     # synthetic climate +1.5 degC
    ne = hot_api.get("/districts/rupnagar/forecast").json()["days"][1]
    assert sw["tmax_c"] == ne["tmax_c"]
    assert ne["relative"]["anomaly_c"] > sw["relative"]["anomaly_c"]


def test_early_season_heat(early_api):
    """March heat (38-39 degC, ~10 degC above normal, after a normal month) must alert."""
    a = early_api.get("/districts/ludhiana/risk").json()
    d = max(a["days"], key=lambda x: x["tmax_c"])
    assert d["relative"]["level"] == 3
    assert d["alert"]["color"] in ("Orange", "Red")
    assert a["summary"]["heatwave_status"] == "unusual_heat_no_heatwave"


def test_mild_forecast(mild_api):
    a = mild_api.get("/districts/amritsar/risk").json()
    assert a["summary"]["heatwave_status"] == "no_heatwave"
    assert all(d["risk_level"] == 0 for d in a["days"])


def test_current_and_hourly(hot_api):
    c = hot_api.get("/districts/patiala/current").json()
    assert c["current"]["thermal"]["final_feels_like_c"] >= c["current"]["thermal"]["heat_index_c"]
    assert len(hot_api.get("/districts/patiala/hourly").json()["hours"]) == 144


def test_history_climate_and_cache(hot_api):
    r = hot_api.get("/districts/rupnagar/history").json()
    assert [y["year"] for y in r["years"]] == list(range(2020, 2026))
    assert "unusual_heat_days" in r["years"][0] and r["most_unusual_hot_days"]
    n = len(hot_api.fake.calls)
    hot_api.get("/districts/rupnagar/history")
    assert len(hot_api.fake.calls) == n
    cl = hot_api.get("/districts/rupnagar/climate").json()
    assert len(cl["calendar"]) == 24 and "_tmax_sorted" not in cl["calendar"][0]


def test_replay(hot_api):
    a = hot_api.get("/districts/rupnagar/risk", params={"replay_date": "2024-05-26"}).json()
    assert a["mode"] == "replay" and a["days"][0]["date"] == "2024-05-26"
    assert a["summary"]["heatwave_expected"]


def test_overview_and_ranking(hot_api):
    r = hot_api.get("/punjab/overview").json()
    assert len(r["districts"]) == 22 and all("max_alert" in d for d in r["districts"])
    v = hot_api.get("/punjab/vulnerability").json()
    assert v["ranking"][0]["id"] == "mansa"


def test_calculate(hot_api):
    r = hot_api.get("/calculate", params={"t": 45, "rh": 20, "ghi": 900, "normal_tmax": 32, "percentile": 99.9}).json()
    assert r["alert"]["color"] == "Red" and r["relative"]["level"] == 3
    r2 = hot_api.get("/calculate", params={"t": 45, "rh": 20, "ghi": 900, "normal_tmax": 42, "percentile": 80}).json()
    assert r2["relative"]["level"] == 1


# ---------------------------------------------------------------------------
# Historical heat burden (2020-2025) feeding the alert
# ---------------------------------------------------------------------------
YEARLY = {"days_tmax_ge_40", "days_tmax_ge_45", "heatwave_days", "severe_heatwave_days",
          "warm_nights", "longest_heatwave_spell_days", "annual_max_tmax_c"}


def _download_all(api):
    for d in api.get("/districts").json()["districts"]:
        assert api.get(f"/districts/{d['id']}/climate").status_code == 200


def test_yearly_metrics(hot_api):
    r = hot_api.get("/districts/bathinda/history").json()
    assert len(r["yearly_metrics"]) == 6 and YEARLY <= set(r["yearly_metrics"][0])
    y24 = next(y for y in r["yearly_metrics"] if y["year"] == 2024)
    assert y24["longest_heatwave_spell_days"] >= 4 and y24["annual_max_tmax_c"] >= 47
    assert {f"mean_{k}" for k in YEARLY} == set(r["averages"])
    assert r["heat_burden"]["status"] == "pending"          # other districts not downloaded yet


def test_burden_pending_then_ready(hot_api):
    s = hot_api.get("/status").json()
    assert s["downloaded"] == 0 and not s["heat_burden_ready"]
    assert hot_api.get("/punjab/heat-burden").status_code == 202
    a = hot_api.get("/districts/bathinda/risk").json()
    assert a["heat_burden"] is None and any("being prepared" in n for n in a["notes"])
    _download_all(hot_api)
    s = hot_api.get("/status").json()
    assert s["downloaded"] == 22 and s["heat_burden_ready"]
    b = hot_api.get("/punjab/heat-burden").json()["ranking"]
    assert len(b) == 22 and len(b[0]["components"]) == 7
    by_id = {x["id"]: x for x in b}
    # synthetic climate: south-west districts are 1.5 degC hotter -> highest burden
    assert by_id["bathinda"]["class"] == "High" and by_id["rupnagar"]["class"] != "High"
    assert by_id["bathinda"]["index"] > by_id["rupnagar"]["index"]


def test_burden_changes_the_alert(hot_api):
    _download_all(hot_api)
    a = hot_api.get("/districts/bathinda/risk").json()
    assert a["heat_burden"]["class"] == "High"
    assert any("historical heat burden" in r for d in a["days"] for r in d["reasons"])


def test_rate_limiter_waits():
    import asyncio
    from app.services.weather import RateLimiter, call_weight
    assert round(call_weight(12784, 2), 1) == 182.6            # 1991-2025 daily, 2 variables
    assert call_weight(7, 2) == 1

    async def run():
        lim = RateLimiter(per_minute=10, per_hour=100)
        await lim.acquire(6)
        try:
            await asyncio.wait_for(lim.acquire(6), timeout=0.3)
            return False
        except asyncio.TimeoutError:
            return True
    assert asyncio.run(run())
