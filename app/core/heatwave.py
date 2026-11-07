"""India Meteorological Department (IMD) heat-wave criteria, applied at one location.

IMD criteria for plains stations:
* Heat-wave conditions are considered only when Tmax >= 40 degC.
* Departure from normal: +4.5 to +6.4 degC -> heat wave; > +6.4 degC -> severe heat wave.
* Actual Tmax: >= 45 degC -> heat wave; >= 47 degC -> severe heat wave.
* Warm night: Tmin departure from normal >= 4.5 degC (while Tmax >= 40 degC).
* IMD formally *declares* a heat wave when the criteria hold at >= 2 stations of a
  sub-division for >= 2 consecutive days. Here we evaluate one location, so the
  output is "heat-wave conditions at this location", plus a flag when they
  last 2+ consecutive days.
"""
from __future__ import annotations

PLAINS_THRESHOLD_C = 40.0
HW_DEPARTURE = 4.5
SEVERE_DEPARTURE = 6.4      # strictly greater than 6.4
HW_ABSOLUTE = 45.0
SEVERE_ABSOLUTE = 47.0
WARM_NIGHT_DEPARTURE = 4.5

STATUS_ORDER = {"normal": 0, "warm": 1, "hot_day": 2, "heatwave": 3, "severe_heatwave": 4}


def classify_day(tmax: float, tmin: float | None, normal_tmax: float | None,
                 normal_tmin: float | None) -> dict:
    dep = None if normal_tmax is None else round(tmax - normal_tmax, 1)
    dep_min = None if (normal_tmin is None or tmin is None) else round(tmin - normal_tmin, 1)

    status, criterion = "normal", None
    if tmax >= PLAINS_THRESHOLD_C:
        status, criterion = "hot_day", "Tmax >= 40 degC but below heat-wave thresholds"
        if tmax >= SEVERE_ABSOLUTE:
            status, criterion = "severe_heatwave", "Tmax >= 47 degC"
        elif dep is not None and dep > SEVERE_DEPARTURE:
            status, criterion = "severe_heatwave", f"Tmax {dep:+.1f} degC above normal (> 6.4)"
        elif tmax >= HW_ABSOLUTE:
            status, criterion = "heatwave", "Tmax >= 45 degC"
        elif dep is not None and dep >= HW_DEPARTURE:
            status, criterion = "heatwave", f"Tmax {dep:+.1f} degC above normal (4.5 to 6.4)"
    elif tmax >= 37.0:
        status, criterion = "warm", "Tmax 37-40 degC: approaching heat-wave threshold"

    warm_night = bool(dep_min is not None and dep_min >= WARM_NIGHT_DEPARTURE and tmax >= PLAINS_THRESHOLD_C)
    return {
        "status": status,
        "criterion": criterion,
        "tmax_departure_c": dep,
        "tmin_departure_c": dep_min,
        "warm_night": warm_night,
        "is_heatwave": status in ("heatwave", "severe_heatwave"),
    }


def mark_spells(days: list[dict]) -> list[dict]:
    """Add ``spell_length`` / ``in_spell`` (2+ consecutive heat-wave days) to each
    classified day dict (must contain ``is_heatwave``), in date order."""
    run = 0
    for d in days:
        run = run + 1 if d["is_heatwave"] else 0
        d["consecutive_heatwave_days"] = run
    # back-fill so the first day of a spell is also flagged
    i = 0
    while i < len(days):
        if days[i]["is_heatwave"]:
            j = i
            while j < len(days) and days[j]["is_heatwave"]:
                j += 1
            for k in range(i, j):
                days[k]["in_spell"] = (j - i) >= 2
                days[k]["spell_length"] = j - i
            i = j
        else:
            days[i]["in_spell"] = False
            days[i]["spell_length"] = 0
            i += 1
    return days
