"""Local climate history: normals, percentiles and heat statistics.

For every calendar day we collect all daily values within +/- ``window`` days
over the normal period (1991-2020: about 30 x 15 = 450 values). From these we get
* the normal (mean) Tmax and Tmin, and
* the percentile rank of any new Tmax ("hotter than 97% of past late-May days").

This is what makes the risk *relative to the place*: 45 degC where 32 degC is
normal ranks far higher than 45 degC where 42 degC is normal.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date

from app.core import heatwave


def _doy_noleap(d: date) -> int:
    """Day of year on a 365-day calendar (Feb 29 is folded onto Feb 28)."""
    if d.month == 2 and d.day == 29:
        d = date(d.year, 2, 28)
    return date(2001, d.month, d.day).timetuple().tm_yday


def _pct(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q / 100
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 2)


def build_normals(times: list[str], tmax: list[float | None], tmin: list[float | None],
                  window: int = 7) -> dict[int, dict]:
    """For each calendar day: mean Tmax/Tmin, Tmax percentiles and the sorted
    Tmax sample (key ``_tmax_sorted``, used for percentile ranks)."""
    by_doy_hi: dict[int, list[float]] = defaultdict(list)
    by_doy_lo: dict[int, list[float]] = defaultdict(list)
    for t, hi, lo in zip(times, tmax, tmin):
        doy = _doy_noleap(date.fromisoformat(t))
        if hi is not None:
            by_doy_hi[doy].append(hi)
        if lo is not None:
            by_doy_lo[doy].append(lo)
    normals = {}
    for doy in range(1, 366):
        his, los = [], []
        for off in range(-window, window + 1):
            k = (doy - 1 + off) % 365 + 1
            his.extend(by_doy_hi.get(k, []))
            los.extend(by_doy_lo.get(k, []))
        his.sort()
        normals[doy] = {
            "tmax": round(sum(his) / len(his), 2) if his else None,
            "tmin": round(sum(los) / len(los), 2) if los else None,
            "tmax_p75": _pct(his, 75), "tmax_p90": _pct(his, 90),
            "tmax_p95": _pct(his, 95), "tmax_p99": _pct(his, 99),
            "tmax_record": his[-1] if his else None,
            "samples": len(his),
            "_tmax_sorted": his,
        }
    return normals


def normal_for(normals: dict[int, dict] | None, d: date) -> dict:
    if not normals:
        return {"tmax": None, "tmin": None}
    return normals[_doy_noleap(d)]


def public_normal(n: dict) -> dict:
    """Normal without the internal sample list."""
    return {k: v for k, v in n.items() if not k.startswith("_")}


def percentile_rank(normals: dict[int, dict] | None, d: date, tmax: float) -> float | None:
    """Share (0-100) of past same-season days that were cooler than ``tmax``."""
    if not normals:
        return None
    vals = normals[_doy_noleap(d)].get("_tmax_sorted") or []
    if not vals:
        return None
    below = bisect_left(vals, tmax)
    equal = bisect_right(vals, tmax) - below
    return round(100 * (below + 0.5 * equal) / len(vals), 1)


# The seven yearly figures that describe a district's recent heat burden
YEARLY_METRICS = [
    "days_tmax_ge_40",              # days with Tmax >= 40 degC
    "days_tmax_ge_45",              # days with Tmax >= 45 degC
    "heatwave_days",                # IMD heat-wave days (incl. severe)
    "severe_heatwave_days",         # IMD severe heat-wave days
    "warm_nights",                  # IMD warm nights
    "longest_heatwave_spell_days",  # longest run of consecutive heat-wave days
    "annual_max_tmax_c",            # hottest Tmax of the year
]


def history_stats(times: list[str], tmax: list, tmin: list, normals: dict[int, dict],
                  start_year: int, end_year: int) -> dict:
    """Year-by-year heat statistics using the same IMD rules as the forecast,
    plus 'unusual heat' days (Tmax >= local 95th percentile for the date)."""
    days = []
    for t, hi, lo in zip(times, tmax, tmin):
        d = date.fromisoformat(t)
        if hi is None or not (start_year <= d.year <= end_year):
            continue
        n = normal_for(normals, d)
        c = heatwave.classify_day(hi, lo, n["tmax"], n["tmin"])
        c.update(date=t, tmax=hi, tmin=lo, normal_tmax=n["tmax"],
                 percentile=percentile_rank(normals, d, hi))
        days.append(c)
    heatwave.mark_spells(days)

    years: dict[int, dict] = {}
    monthly = defaultdict(list)
    for c in days:
        y = int(c["date"][:4])
        ys = years.setdefault(y, {
            "year": y, "days_tmax_ge_40": 0, "days_tmax_ge_45": 0, "heatwave_days": 0,
            "severe_heatwave_days": 0, "warm_nights": 0, "heatwave_spells": 0,
            "unusual_heat_days": 0, "early_season_unusual_heat_days": 0,
            "longest_heatwave_spell_days": 0, "_run": 0,
            "hottest_day": None, "hottest_tmax_c": None, "largest_anomaly_c": None, "largest_anomaly_day": None,
            "first_40c_day": None, "last_40c_day": None,
        })
        if c["tmax"] >= 40:
            ys["days_tmax_ge_40"] += 1
            ys["first_40c_day"] = ys["first_40c_day"] or c["date"]
            ys["last_40c_day"] = c["date"]
        if c["tmax"] >= 45:
            ys["days_tmax_ge_45"] += 1
        if c["is_heatwave"]:
            ys["heatwave_days"] += 1
            ys["_run"] += 1
            ys["longest_heatwave_spell_days"] = max(ys["longest_heatwave_spell_days"], ys["_run"])
        else:
            ys["_run"] = 0
        if c["status"] == "severe_heatwave":
            ys["severe_heatwave_days"] += 1
        if c["warm_night"]:
            ys["warm_nights"] += 1
        if c["is_heatwave"] and c["in_spell"] and c["consecutive_heatwave_days"] == 1:
            ys["heatwave_spells"] += 1
        # unusual heat only matters in the warm season (Tmax >= 30 degC)
        if c["percentile"] is not None and c["percentile"] >= 95 and c["tmax"] >= 30:
            ys["unusual_heat_days"] += 1
            if int(c["date"][5:7]) in (2, 3, 4):
                ys["early_season_unusual_heat_days"] += 1
        if ys["hottest_tmax_c"] is None or c["tmax"] > ys["hottest_tmax_c"]:
            ys["hottest_tmax_c"], ys["hottest_day"] = c["tmax"], c["date"]
        dep = c["tmax_departure_c"]
        if dep is not None and c["tmax"] >= 30 and (ys["largest_anomaly_c"] is None or dep > ys["largest_anomaly_c"]):
            ys["largest_anomaly_c"], ys["largest_anomaly_day"] = dep, c["date"]
        monthly[(y, int(c["date"][5:7]))].append(c["tmax"])

    monthly_mean = [
        {"year": y, "month": m, "mean_tmax_c": round(sum(v) / len(v), 1)}
        for (y, m), v in sorted(monthly.items())
    ]
    top = sorted(days, key=lambda c: c["tmax"], reverse=True)[:10]
    events = [{"date": c["date"], "tmax_c": c["tmax"], "departure_c": c["tmax_departure_c"],
               "percentile": c["percentile"], "status": c["status"]} for c in top]
    warm = [c for c in days if c["tmax"] >= 30 and c["tmax_departure_c"] is not None]
    most_unusual = sorted(warm, key=lambda c: c["tmax_departure_c"], reverse=True)[:10]
    unusual = [{"date": c["date"], "tmax_c": c["tmax"], "normal_tmax_c": c["normal_tmax"],
                "departure_c": c["tmax_departure_c"], "percentile": c["percentile"]} for c in most_unusual]
    spells = []
    i = 0
    while i < len(days):
        if days[i]["in_spell"]:
            j = i
            while j < len(days) and days[j]["in_spell"] and days[j]["consecutive_heatwave_days"] == j - i + 1:
                j += 1
            seg = days[i:j]
            spells.append({"start": seg[0]["date"], "end": seg[-1]["date"], "days": len(seg),
                           "peak_tmax_c": max(s["tmax"] for s in seg)})
            i = j
        else:
            i += 1
    year_rows = []
    for y in sorted(years):
        row = {k: v for k, v in years[y].items() if not k.startswith("_")}
        row["annual_max_tmax_c"] = row["hottest_tmax_c"]
        year_rows.append(row)
    n = len(year_rows) or 1
    averages = {f"mean_{k}": round(sum((r[k] or 0) for r in year_rows) / n, 2) for k in YEARLY_METRICS}
    return {
        "yearly_metrics": [{"year": r["year"], **{k: r[k] for k in YEARLY_METRICS}} for r in year_rows],
        "averages": averages,
        "years": year_rows,
        "hottest_days": events,
        "most_unusual_hot_days": unusual,
        "heatwave_spells": spells,
        "monthly_mean_tmax": monthly_mean,
    }
