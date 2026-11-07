"""Unit tests for formulas and classification rules."""
from datetime import datetime, timezone

import pytest

from app.core import heatwave, risk, thermal, vulnerability


def f2c(f):
    return (f - 32) * 5 / 9


@pytest.mark.parametrize("tf,rh,expected_f", [(96, 65, 121), (90, 50, 95), (104, 40, 119), (86, 90, 105)])
def test_heat_index_matches_noaa_chart(tf, rh, expected_f):
    hi_f = thermal.heat_index(f2c(tf), rh) * 9 / 5 + 32
    assert abs(hi_f - expected_f) <= 1.5


def test_wet_bulb_reference():
    # 20 degC / 50% at sea level: psychrometric wet bulb ~13.8 degC
    assert abs(thermal.wet_bulb(20, 50, 1013.25) - 13.8) < 0.3
    # saturated air: wet bulb = air temperature
    assert abs(thermal.wet_bulb(30, 100) - 30) < 0.1


def test_solar_zenith_ropar_june_noon():
    # Local solar noon at 76.53E is ~06:52 UTC; zenith ~ 30.97 - 23.44 = 7.5 deg
    cz = thermal.cos_solar_zenith(datetime(2026, 6, 21, 6, 52, tzinfo=timezone.utc), 30.97, 76.53)
    assert abs(cz - 0.991) < 0.005


def test_globe_equals_air_at_night():
    assert abs(thermal.globe_temperature(30, 60, 0, 0, -0.5, 1.0) - 30) < 0.3


def test_sun_raises_final_feels_like_but_not_beyond_noaa_8c():
    shade = thermal.compute(40, 30, ghi_wm2=0, cos_zenith=-0.2)
    sun = thermal.compute(40, 30, ghi_wm2=950, direct_wm2=780, cos_zenith=0.95, wind_10m_ms=2)
    assert shade.final_feels_like_c == pytest.approx(shade.heat_index_c)
    gain = sun.final_feels_like_c - sun.heat_index_c
    assert 2 <= gain <= 9
    assert sun.wbgt_sun_c > sun.wbgt_shade_c


def test_more_wind_less_solar_load():
    calm = thermal.compute(40, 30, ghi_wm2=900, direct_wm2=700, cos_zenith=0.9, wind_10m_ms=0.5)
    windy = thermal.compute(40, 30, ghi_wm2=900, direct_wm2=700, cos_zenith=0.9, wind_10m_ms=6)
    assert calm.globe_temp_c > windy.globe_temp_c


@pytest.mark.parametrize("tmax,normal,status", [
    (39.5, 33, "warm"),           # below 40: never a heat wave
    (41.0, 38, "hot_day"),        # +3 departure
    (42.0, 37.5, "heatwave"),     # +4.5 departure
    (44.0, 37.5, "severe_heatwave"),  # +6.5 departure
    (45.2, 42, "heatwave"),       # absolute >= 45
    (47.1, 44, "severe_heatwave"),    # absolute >= 47
])
def test_imd_rules(tmax, normal, status):
    assert heatwave.classify_day(tmax, 28, normal, 25)["status"] == status


def test_warm_night():
    c = heatwave.classify_day(41, 31, 39, 26)
    assert c["warm_night"]


def test_spells():
    days = [{"is_heatwave": x} for x in [False, True, True, True, False, True]]
    heatwave.mark_spells(days)
    assert [d["in_spell"] for d in days] == [False, True, True, True, False, False]




# ---------------------------------------------------------------------------
# Census vulnerability (district level)
# ---------------------------------------------------------------------------
from app.core import climatology  # noqa: E402
from datetime import date  # noqa: E402


def test_census_state_totals():
    c = vulnerability.load_census()
    assert c["state"]["population"] == 27_743_338
    assert abs(c["state"]["literacy_rate_pct"] - 75.84) < 0.05      # official Punjab literacy
    assert abs(c["districts"]["rupnagar"]["child_0_6"] - 72_926) < 100  # official Rupnagar 0-6


def test_vulnerability_ranking():
    r = vulnerability.ranked()
    assert len(r) == 22
    assert "malerkotla" not in [x["id"] for x in r]
    top5 = {x["id"] for x in r[:5]}
    assert {"mansa", "sri_muktsar_sahib"} <= top5          # south-west Malwa most vulnerable
    assert r[-1]["id"] in {"sas_nagar", "ludhiana"}
    assert vulnerability.vulnerability_index("mansa")["class"] == "High"


# ---------------------------------------------------------------------------
# Relative heat
# ---------------------------------------------------------------------------
def _normals():
    times, hi, lo = [], [], []
    for y in range(1991, 2021):
        for k in range(-10, 11):
            d = date(y, 5, 20).toordinal() + k
            times.append(date.fromordinal(d).isoformat())
            hi.append(38.0 + (k % 7) * 0.5)   # 38.0-41.0
            lo.append(24.0)
    return climatology.build_normals(times, hi, lo)


def test_percentile_rank():
    n = _normals()
    d = date(2026, 5, 20)
    assert climatology.percentile_rank(n, d, 37.0) == 0
    assert climatology.percentile_rank(n, d, 42.0) == 100
    assert 40 < climatology.percentile_rank(n, d, 39.5) < 70


def test_users_example_32_vs_42_normal():
    """45 degC where 32 is normal is worse than 45 degC where 42 is normal."""
    a = risk.absolute_level(45, 50.0, "Danger")
    place_a = risk.relative_level(45, 32, 99.9, None)
    place_b = risk.relative_level(45, 42, 80, None)
    ra = risk.classify_days([{"absolute": a, "relative": place_a}], "Moderate")[0]
    rb = risk.classify_days([{"absolute": dict(a), "relative": place_b}], "Moderate")[0]
    assert place_a["level"] == 3 and place_b["level"] == 1
    assert ra["risk_level"] == 3 and rb["risk_level"] == 3 - 0  # A3 x R1 -> Red
    a2 = risk.absolute_level(42, 47.0, "Danger")              # A2
    ra2 = risk.classify_days([{"absolute": a2, "relative": risk.relative_level(42, 32, 99.9, None)}], "Moderate")[0]
    rb2 = risk.classify_days([{"absolute": dict(a2), "relative": risk.relative_level(42, 41, 60, None)}], "Moderate")[0]
    assert ra2["alert"]["color"] == "Red" and rb2["alert"]["color"] == "Yellow"


def test_no_alert_without_physical_heat():
    a = risk.absolute_level(28, 29.0, "Caution")
    r = risk.relative_level(28, 20, 100, 5)
    assert risk.classify_days([{"absolute": a, "relative": r}], "High")[0]["risk_level"] == 0


def test_acclimatisation_bumps_relative():
    assert risk.relative_level(38, 37, 60, None)["level"] == 0
    assert risk.relative_level(38, 37, 60, 4.0)["level"] == 1


def test_vulnerability_only_with_unusual_heat():
    a = risk.absolute_level(41, 45.0, "Danger")                # A2
    normal = risk.relative_level(41, 41, 50, None)              # R0 -> Yellow
    above = risk.relative_level(41, 38.5, 88, None)             # R1 -> Orange
    assert risk.classify_days([{"absolute": a, "relative": normal}], "High")[0]["risk_level"] == 1
    assert risk.classify_days([{"absolute": dict(a), "relative": above}], "High")[0]["risk_level"] == 3


def test_persistence():
    a = risk.absolute_level(41, 45.0, "Danger")
    rel = risk.relative_level(41, 38.5, 88, None)               # A2 x R1 = Orange
    days = [{"absolute": dict(a), "relative": dict(rel)} for _ in range(3)]
    out = risk.classify_days(days, "Moderate")
    assert [d["risk_level"] for d in out] == [2, 2, 3]


def test_severity_score_users_example():
    a = risk.absolute_level(45, 50.0, "Danger")
    s_a = risk.severity_score(a, risk.relative_level(45, 32, 99.9, None))
    s_b = risk.severity_score(a, risk.relative_level(45, 42, 80, None))
    assert s_a == 100 and s_b < 75
