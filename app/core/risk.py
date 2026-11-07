"""Explainable heat-risk classification: ABSOLUTE x RELATIVE matrix.

Why two dimensions?
    The Heat Index / WBGT measure the physical heat load on the body (same
    everywhere). But health impact also depends on how unusual the heat is for
    the place: people, homes, work routines and hospitals adapt to the local
    normal. 45 degC where 32 degC is normal is more dangerous than 45 degC where
    42 degC is normal. So we score both and combine them.

Step 1 - ABSOLUTE level A (0-3): physical heat load, highest of
    * peak final feels-like (Heat Index in sun, NOAA categories):
        >= 32.2 Extreme caution -> 1, >= 39.4 Danger -> 2, >= 51.7 Extreme danger -> 3
    * Tmax: >= 37 -> 1, >= 40 -> 2, >= 45 -> 3

Step 2 - RELATIVE level R (0-3): how unusual the day is for THIS district on
    THIS date, versus 1991-2020 (all days within +/- 7 calendar days):
        R = 3  percentile >= 99  or  Tmax > normal + 6.4 (IMD severe departure)
        R = 2  percentile >= 95  or  Tmax >= normal + 4.5 (IMD heat-wave departure)
        R = 1  percentile >= 85  or  Tmax >= normal + 2.0
        R = 0  otherwise
    Acclimatisation: if the mean temperature of the last 3 days is >= 3 degC above
    the mean of the 30 days before them (a sudden rise the body has not adapted
    to; idea from the Excess Heat Factor, Nairn & Fawcett 2015), R rises by 1.

Step 3 - MATRIX  (rows A, columns R)        R0  R1  R2  R3
                                    A0       0   0   0   0
                                    A1       1   1   2   2
                                    A2       1   2   3   3
                                    A3       2   3   3   3
    * No physical heat (A0) never gives an alert, however unusual.
    * Normal-for-the-season heat (R0) stays at Yellow/Orange, to avoid daily
      Red alerts all through May-June.
    * Unusual heat (R2-R3) on a dangerous day (A2+) is Red.

Step 4 - DISTRICT SUSCEPTIBILITY: if the district has High Census vulnerability
    OR a High historical heat burden (it was among the hardest-hit districts in
    2020-2025: heat-wave days, days >= 40/45 degC, warm nights, longest spells,
    annual maximum; see burden.py), Yellow -> Orange and Orange -> Red, but only
    when the heat is at least somewhat unusual (R >= 1).

Step 5 - PERSISTENCE: otherwise, an Orange day becomes Red from the 3rd
    consecutive matrix-Orange-or-above day with R >= 1 (heat stress accumulates).

    Steps 4 and 5 together raise a day by at most ONE level above the matrix.

Levels use the IMD colour scheme: 0 Green, 1 Yellow, 2 Orange, 3 Red.

Severity score (0-100) ranks days and districts WITHIN a level, so 45 degC
where 32 is normal (score 100) still ranks above 45 degC where 42 is normal
(score 69) even though IMD makes both Red (Tmax >= 45 is a heat wave anywhere).
"""
from __future__ import annotations

LEVELS = [
    {"level": 0, "color": "Green", "label": "No significant heat risk", "action": "No action needed"},
    {"level": 1, "color": "Yellow", "label": "Heat caution", "action": "Be aware"},
    {"level": 2, "color": "Orange", "label": "Heat danger", "action": "Be prepared"},
    {"level": 3, "color": "Red", "label": "Extreme heat / heat wave", "action": "Take action"},
]

MATRIX = [
    [0, 0, 0, 0],
    [1, 1, 2, 2],
    [1, 2, 3, 3],
    [2, 3, 3, 3],
]
ABS_NAMES = ["Low heat load", "Moderate heat load", "High heat load", "Extreme heat load"]
REL_NAMES = ["Normal for the season", "Above normal", "Unusual heat", "Extreme for this place"]
ACCLIMATISATION_JUMP_C = 3.0


def feels_like_level(final_c: float) -> int:
    if final_c >= 51.7:
        return 3
    if final_c >= 39.4:
        return 2
    if final_c >= 32.2:
        return 1
    return 0


def tmax_level(tmax: float) -> int:
    return 3 if tmax >= 45 else 2 if tmax >= 40 else 1 if tmax >= 37 else 0


def absolute_level(tmax: float, peak_final_c: float, peak_final_cat: str) -> dict:
    fl, tl = feels_like_level(peak_final_c), tmax_level(tmax)
    reasons = []
    if fl:
        reasons.append(f"Feels-like in sun peaks at {peak_final_c:.1f} degC ({peak_final_cat}) -> {fl}")
    if tl:
        reasons.append(f"Tmax {tmax:.1f} degC -> {tl}")
    lvl = max(fl, tl)
    return {"level": lvl, "name": ABS_NAMES[lvl], "reasons": reasons or ["Below heat-stress thresholds"]}


def relative_level(tmax: float, normal_tmax: float | None, percentile: float | None,
                   acclimatisation_c: float | None) -> dict:
    anom = None if normal_tmax is None else round(tmax - normal_tmax, 1)
    p = percentile
    lvl, reasons = 0, []
    if (p is not None and p >= 99) or (anom is not None and anom > 6.4):
        lvl = 3
    elif (p is not None and p >= 95) or (anom is not None and anom >= 4.5):
        lvl = 2
    elif (p is not None and p >= 85) or (anom is not None and anom >= 2.0):
        lvl = 1
    if anom is not None:
        reasons.append(f"Tmax {anom:+.1f} degC vs the 1991-2020 normal ({normal_tmax:.1f} degC)")
    if p is not None:
        reasons.append(f"Hotter than {p:.0f}% of past days at this time of year")
    if acclimatisation_c is not None and acclimatisation_c >= ACCLIMATISATION_JUMP_C and lvl < 3:
        lvl += 1
        reasons.append(f"Sudden rise: last 3 days {acclimatisation_c:+.1f} degC warmer than the 30 days before "
                       "(people not yet acclimatised) -> +1")
    if normal_tmax is None:
        reasons.append("Local normals unavailable; relative level set to 0")
    return {"level": lvl, "name": REL_NAMES[lvl], "anomaly_c": anom, "percentile": p,
            "acclimatisation_c": acclimatisation_c, "reasons": reasons}


def severity_score(absolute: dict, relative: dict) -> float:
    """0-100 score to rank days/districts *within* an alert level.
    50 points for absolute heat (A/3) + 50 points for how unusual it is:
    the larger of anomaly/8 degC and (percentile-75)/25, each capped at 1.
    Example: Tmax 45 degC where normal is 32 -> 50 + 50 = 100;
             Tmax 45 degC where normal is 42 -> 50 + 19 = 69."""
    a = absolute["level"] / 3
    anom = relative.get("anomaly_c")
    p = relative.get("percentile")
    rel = max(min(max(anom or 0, 0) / 8, 1), min(max(((p or 0) - 75) / 25, 0), 1))
    return round(50 * a + 50 * rel, 1)


def classify_days(days: list[dict], vulnerability_class: str, burden_class: str | None = None) -> list[dict]:
    """Each day needs ``absolute`` and ``relative`` dicts (from the functions above).
    Adds ``matrix_level``, ``risk_level``, ``alert`` and ``reasons``."""
    run = 0
    for d in days:
        a, r = d["absolute"]["level"], d["relative"]["level"]
        lvl = MATRIX[a][r]
        reasons = [f"Absolute: {ABS_NAMES[a]} (A{a}); Relative: {REL_NAMES[r]} (R{r}) -> matrix gives "
                   f"{LEVELS[lvl]['color']}"]
        d["matrix_level"] = base = lvl
        run = run + 1 if (base >= 2 and r >= 1) else 0
        why = [w for w, c in (("High Census vulnerability", vulnerability_class),
                              ("High historical heat burden (2020-2025)", burden_class)) if c == "High"]
        if lvl in (1, 2) and r >= 1 and why:
            lvl += 1
            reasons.append(f"{' and '.join(why)} with above-normal heat -> raised to {LEVELS[lvl]['color']}")
        elif lvl == 2 and run >= 3:
            lvl += 1
            reasons.append(f"Day {run} in a row of above-normal dangerous heat -> raised to {LEVELS[lvl]['color']}")
        d["risk_level"] = lvl
        d["alert"] = LEVELS[lvl]
        d["severity_score"] = severity_score(d["absolute"], d["relative"])
        d["reasons"] = reasons + d.get("extra_reasons", [])
        d.pop("extra_reasons", None)
    return days


# ---------------------------------------------------------------------------
# Advice (based on NDMA heat-wave guidelines and state heat action plans)
# ---------------------------------------------------------------------------
ADVICE = {
    0: {
        "public": ["Normal activity. Drink water regularly."],
        "vulnerable_groups": ["No special measures needed."],
        "outdoor_workers": ["Normal work schedule; keep drinking water at work sites."],
        "authorities": ["Routine readiness: review the Heat Action Plan and stock ORS and IV fluids before the summer season (March-June)."],
    },
    1: {
        "public": [
            "Drink water often, even if not thirsty; carry a bottle when going out.",
            "Avoid direct sun 12:00-15:00; wear light, loose cotton clothes and cover your head.",
            "Use ORS, lassi, nimbu-pani or buttermilk; limit tea, coffee, alcohol and fizzy drinks.",
        ],
        "vulnerable_groups": [
            "Check on elderly neighbours who live alone.",
            "Keep infants and young children in shade and well hydrated.",
        ],
        "outdoor_workers": [
            "Water every 15-20 minutes; take short breaks in shade.",
            "New or returning workers should build up work time gradually over a week (acclimatisation).",
        ],
        "authorities": [
            "Issue heat advisories through local media, WhatsApp groups and gurdwara/temple announcements.",
            "Ensure drinking-water points (chhabeels) at bus stands, mandis and markets.",
            "Ensure ORS is stocked at PHCs, CHCs and anganwadi centres.",
        ],
    },
    2: {
        "public": [
            "Avoid strenuous outdoor activity between 11:00 and 16:00.",
            "Never leave children or pets in parked vehicles.",
            "Know the signs of heat exhaustion: heavy sweating, dizziness, headache, nausea, cramps. Move to shade, cool the body and give ORS.",
            "Keep homes cool: close curtains in the day, open windows at night, use wet cloths.",
        ],
        "vulnerable_groups": [
            "Elderly, young children, pregnant women and people with chronic illness should stay indoors in the afternoon.",
            "Set up a buddy system to check on high-risk people twice a day.",
        ],
        "outdoor_workers": [
            "Shift heavy work (harvesting, construction, loading) to before 11:00 and after 16:00.",
            "Make shaded rest and water breaks compulsory; follow the WBGT work/rest guide for each hour.",
        ],
        "authorities": [
            "Activate the district Heat Action Plan.",
            "Open cooling centres (community halls, gurdwaras, schools) during the afternoon.",
            "Hospitals and CHCs should keep heat-stroke rooms with ice packs, cold IV fluids and trained staff.",
            "Consider changing school timings and rescheduling MGNREGA and construction work hours.",
            "Coordinate with PSPCL to avoid long power cuts and with water supply to avoid shortages.",
            "Provide shade and water for livestock.",
        ],
    },
    3: {
        "public": [
            "Stay indoors during the day as far as possible.",
            "Heat stroke is an emergency: body temperature above 40 degC, confusion, fainting, hot dry skin. Call 108 (ambulance) or 112, cool the person with water and fanning while waiting.",
            "Avoid cooking in hot hours; eat light meals and keep drinking fluids.",
        ],
        "vulnerable_groups": [
            "Active outreach by ASHA and anganwadi workers to elderly people living alone, pregnant women and households without cooling.",
            "Move highly vulnerable people to cooling centres if homes are too hot.",
        ],
        "outdoor_workers": [
            "Stop heavy outdoor work between 12:00 and 16:00; only essential light work with frequent breaks.",
            "Employers must provide shade, cool water and ORS at every work site.",
        ],
        "authorities": [
            "Issue orders through the District Disaster Management Authority to suspend outdoor labour in the afternoon.",
            "Declare school holidays or close schools early.",
            "Deploy mobile medical teams and keep ambulances on standby near mandis, pilgrimage sites and highways.",
            "Keep cooling centres open for long hours; send water tankers to areas with shortages.",
            "Track heat-illness cases and deaths daily (IDSP) and report to the state.",
        ],
    },
}


def advice_for(level: int) -> dict:
    return ADVICE[level]
