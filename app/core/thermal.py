"""
Human thermal-stress calculations.

Why combine Heat Index and WBGT?
--------------------------------
The NOAA Heat Index (HI) uses only air temperature and humidity. It describes
how hot it feels *in the shade with a light breeze*. NOAA notes that direct
sunshine can raise HI values by up to about 8 degC (15 degF).

The Wet Bulb Globe Temperature (WBGT) also accounts for sunshine (through the
black-globe temperature) and wind. So we:

1. compute WBGT in the shade and WBGT in the sun for the same hour;
2. find the shade air temperature T* that would give the same WBGT as standing
   in the sun now (holding the moisture content of the air fixed);
3. evaluate the Heat Index at T* -> ``final_feels_like_c``.

``final_feels_like_c`` is therefore "the Heat Index you experience in direct
sun". It keeps the familiar HI scale and its NOAA categories, but includes the
radiant load that HI alone leaves out. At night, or under heavy cloud,
WBGT_sun ~= WBGT_shade, so the result falls back to the ordinary Heat Index.

Formulas used
-------------
* Saturation vapour pressure: Magnus / Bolton (1980).
* Heat Index: NOAA / NWS Rothfusz regression with NWS adjustments.
* Wet-bulb temperature: psychrometric equation solved by bisection.
* Black-globe temperature: Dimiceli, Piltz & Amburn (2011), the method behind
  the US National Weather Service experimental WBGT.
* Outdoor WBGT = 0.7*Tnw + 0.2*Tg + 0.1*Ta  (ISO 7243).
  Shade WBGT  = 0.7*Tnw + 0.3*Ta.
  The natural wet-bulb Tnw is approximated by the psychrometric wet-bulb, a
  common simplification that slightly underestimates WBGT in strong sun.
* Solar zenith angle: NOAA simplified solar-position equations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

SIGMA = 5.67e-8  # Stefan-Boltzmann constant, W m-2 K-4

# ---------------------------------------------------------------------------
# Moisture
# ---------------------------------------------------------------------------

def saturation_vapour_pressure(t_c: float) -> float:
    """Saturation vapour pressure over water, hPa (Bolton 1980)."""
    return 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))


def vapour_pressure(t_c: float, rh_pct: float) -> float:
    return max(rh_pct, 0.1) / 100.0 * saturation_vapour_pressure(t_c)


def dew_point(t_c: float, rh_pct: float) -> float:
    e = vapour_pressure(t_c, rh_pct)
    g = math.log(e / 6.112)
    return 243.5 * g / (17.67 - g)


def wet_bulb(t_c: float, rh_pct: float, pressure_hpa: float = 1013.25) -> float:
    """Psychrometric (ventilated) wet-bulb temperature, degC.

    Solves e = es(Tw) - A * P * (T - Tw) with A = 6.6e-4 (1 + 0.00115 Tw).
    """
    e = vapour_pressure(t_c, rh_pct)
    lo, hi = dew_point(t_c, rh_pct) - 0.5, t_c
    for _ in range(60):
        mid = (lo + hi) / 2
        a = 6.6e-4 * (1 + 0.00115 * mid)
        f = saturation_vapour_pressure(mid) - a * pressure_hpa * (t_c - mid) - e
        if f > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Heat Index (NOAA)
# ---------------------------------------------------------------------------

def heat_index(t_c: float, rh_pct: float) -> float:
    """NOAA Heat Index in degC (computed in degF internally)."""
    t = t_c * 9 / 5 + 32
    rh = min(max(rh_pct, 0.0), 100.0)
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        hi_f = simple
    else:
        hi_f = (-42.379 + 2.04901523 * t + 10.14333127 * rh
                - 0.22475541 * t * rh - 0.00683783 * t * t
                - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
                + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
        if rh < 13 and 80 <= t <= 112:
            hi_f -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            hi_f += ((rh - 85) / 10) * ((87 - t) / 5)
    return (hi_f - 32) * 5 / 9


# NOAA/NWS categories (thresholds in degC, converted from 80/90/103/125 degF)
HI_CATEGORIES = [
    (51.7, "Extreme danger", "Heat stroke highly likely."),
    (39.4, "Danger", "Heat cramps or heat exhaustion likely; heat stroke possible with prolonged exposure or activity."),
    (32.2, "Extreme caution", "Heat cramps or heat exhaustion possible with prolonged exposure or activity."),
    (26.7, "Caution", "Fatigue possible with prolonged exposure or activity."),
    (-99, "No significant heat stress", "Normal conditions."),
]


def hi_category(value_c: float) -> dict:
    for threshold, name, effect in HI_CATEGORIES:
        if value_c >= threshold:
            return {"category": name, "effect": effect}
    return {"category": HI_CATEGORIES[-1][1], "effect": HI_CATEGORIES[-1][2]}


# ---------------------------------------------------------------------------
# Sun position
# ---------------------------------------------------------------------------

def cos_solar_zenith(when_utc: datetime, lat: float, lon: float) -> float:
    """Cosine of the solar zenith angle (NOAA simplified equations)."""
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    gamma = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    true_solar_min = hour * 60 + eqtime + 4 * lon
    ha = math.radians(true_solar_min / 4 - 180)
    phi = math.radians(lat)
    return math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)


# ---------------------------------------------------------------------------
# Globe temperature and WBGT
# ---------------------------------------------------------------------------

def wind_10m_to_2m(u10_ms: float) -> float:
    """FAO-56 logarithmic wind profile, 10 m -> 2 m."""
    return u10_ms * 4.87 / math.log(67.8 * 10 - 5.42)


def globe_temperature(t_c: float, rh_pct: float, ghi_wm2: float, direct_fraction: float,
                      cos_zenith: float, wind_2m_ms: float) -> float:
    """Black-globe temperature, degC (Dimiceli et al. 2011, NWS method).

    The published equation is a linearisation (tangent at 40 degC) fitted with
    temperatures in degC, so Ta is used in degC throughout, exactly as in the
    NWS algorithm (weather.gov/media/tsa/pdf/WBGTpaper2.pdf, Eq. 10).
    """
    ta = t_c
    s = max(ghi_wm2, 0.0)
    cz = cos_zenith
    fdb = min(max(direct_fraction, 0.0), 1.0)
    if cz < 0.0872 or s < 1:  # sun below ~5 deg: treat all radiation as diffuse
        fdb = 0.0
        cz = 1.0
    fdif = 1.0 - fdb
    ea_hpa = vapour_pressure(t_c, rh_pct)
    emis_a = 0.575 * ea_hpa ** (1 / 7)
    u_mph = max(wind_2m_ms, 0.5) * 3600  # metres per hour
    h = 0.315
    b = s * (fdb / (4 * SIGMA * cz) + 1.2 / SIGMA * fdif) + emis_a * ta ** 4
    c = h * u_mph ** 0.58 / 5.3865e-8
    return (b + c * ta + 7_680_000) / (c + 256_000)


def wbgt_shade(t_c: float, rh_pct: float, pressure_hpa: float = 1013.25) -> float:
    tw = wet_bulb(t_c, rh_pct, pressure_hpa)
    return 0.7 * tw + 0.3 * t_c


def wbgt_sun(t_c: float, rh_pct: float, tg_c: float, pressure_hpa: float = 1013.25) -> float:
    tw = wet_bulb(t_c, rh_pct, pressure_hpa)
    return 0.7 * tw + 0.2 * tg_c + 0.1 * t_c


# Heat-stress flag categories for outdoor work (adapted from US Army TB MED 507)
# work/rest minutes per hour for MODERATE work (e.g. farm work, carrying loads)
WBGT_FLAGS = [
    (32.2, 5, "Black", "20 min work / 40 min rest", "Stop heavy outdoor work; only essential light work in shade."),
    (31.1, 4, "Red", "30 min work / 30 min rest", "Reschedule heavy work to early morning or evening."),
    (29.4, 3, "Yellow", "40 min work / 20 min rest", "Frequent shaded rest and water breaks."),
    (27.8, 2, "Green", "50 min work / 10 min rest", "Drink water regularly; watch for early signs of heat illness."),
    (25.6, 1, "White", "No limit for acclimatised workers", "Normal precautions."),
    (-99, 0, "None", "No limit", "Low heat stress."),
]


def wbgt_flag(value_c: float) -> dict:
    for threshold, cat, flag, work_rest, advice in WBGT_FLAGS:
        if value_c >= threshold:
            return {"category": cat, "flag": flag, "work_rest": work_rest, "advice": advice}
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Combined result
# ---------------------------------------------------------------------------

def shade_equivalent_temperature(t_c: float, rh_pct: float, target_wbgt: float,
                                 pressure_hpa: float = 1013.25) -> float:
    """Air temperature (shade) giving ``target_wbgt`` with the same vapour pressure."""
    e = vapour_pressure(t_c, rh_pct)

    def wbgt_at(t: float) -> float:
        rh = min(100.0, 100.0 * e / saturation_vapour_pressure(t))
        return wbgt_shade(t, rh, pressure_hpa)

    if target_wbgt <= wbgt_at(t_c):
        return t_c
    lo, hi = t_c, t_c + 40
    for _ in range(50):
        mid = (lo + hi) / 2
        if wbgt_at(mid) < target_wbgt:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass
class ThermalResult:
    air_temp_c: float
    rh_pct: float
    heat_index_c: float          # shade, NOAA
    wet_bulb_c: float
    globe_temp_c: float
    wbgt_shade_c: float
    wbgt_sun_c: float
    solar_load_c: float          # WBGT_sun - WBGT_shade
    shade_equivalent_temp_c: float
    final_feels_like_c: float    # Heat Index in direct sun (HI + WBGT combined)
    hi_category: str
    final_category: str
    final_effect: str
    wbgt_flag: str
    wbgt_category: int
    work_rest: str
    sun_up: bool

    def as_dict(self) -> dict:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def compute(t_c: float, rh_pct: float, *, ghi_wm2: float = 0.0, direct_wm2: float | None = None,
            wind_10m_ms: float = 1.0, pressure_hpa: float = 1013.25,
            cos_zenith: float = 0.0) -> ThermalResult:
    """All indices for one hour of weather."""
    hi = heat_index(t_c, rh_pct)
    tw = wet_bulb(t_c, rh_pct, pressure_hpa)
    fdb = (direct_wm2 / ghi_wm2) if (direct_wm2 is not None and ghi_wm2 > 1) else 0.7
    tg = globe_temperature(t_c, rh_pct, ghi_wm2, fdb, cos_zenith, wind_10m_to_2m(wind_10m_ms))
    w_shade = 0.7 * tw + 0.3 * t_c
    w_sun = 0.7 * tw + 0.2 * tg + 0.1 * t_c
    sun_up = cos_zenith > 0.0872 and ghi_wm2 > 50
    if sun_up and w_sun > w_shade:
        t_star = shade_equivalent_temperature(t_c, rh_pct, w_sun, pressure_hpa)
        rh_star = min(100.0, 100.0 * vapour_pressure(t_c, rh_pct) / saturation_vapour_pressure(t_star))
        final = max(heat_index(t_star, rh_star), hi)
        effective_wbgt = w_sun
    else:
        t_star, final, effective_wbgt = t_c, hi, max(w_shade, w_sun)
    cat = hi_category(final)
    flag = wbgt_flag(effective_wbgt)
    return ThermalResult(
        air_temp_c=t_c, rh_pct=rh_pct, heat_index_c=hi, wet_bulb_c=tw, globe_temp_c=tg,
        wbgt_shade_c=w_shade, wbgt_sun_c=effective_wbgt, solar_load_c=max(0.0, w_sun - w_shade),
        shade_equivalent_temp_c=t_star, final_feels_like_c=final,
        hi_category=hi_category(hi)["category"], final_category=cat["category"],
        final_effect=cat["effect"], wbgt_flag=flag["flag"], wbgt_category=flag["category"],
        work_rest=flag["work_rest"], sun_up=sun_up,
    )


IST = timezone(timedelta(hours=5, minutes=30))


def compute_for_hour(local_time: datetime, lat: float, lon: float, *, t_c: float, rh_pct: float,
                     ghi_wm2: float | None, direct_wm2: float | None, wind_10m_ms: float | None,
                     pressure_hpa: float | None) -> ThermalResult:
    """Open-Meteo radiation is the mean over the *preceding* hour, so the sun
    position is taken at the middle of that hour."""
    if local_time.tzinfo is None:
        local_time = local_time.replace(tzinfo=IST)
    mid = (local_time - timedelta(minutes=30)).astimezone(timezone.utc)
    return compute(
        t_c, rh_pct,
        ghi_wm2=ghi_wm2 or 0.0, direct_wm2=direct_wm2,
        wind_10m_ms=wind_10m_ms if wind_10m_ms is not None else 1.0,
        pressure_hpa=pressure_hpa or 1013.25,
        cos_zenith=cos_solar_zenith(mid, lat, lon),
    )
