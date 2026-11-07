"""Historical heat-burden index: how hard each district has actually been hit
by heat in recent years (default 2020-2025), compared with the other districts.

For every district and every year we count the seven figures in
``climatology.YEARLY_METRICS``:
    days with Tmax >= 40 degC, days with Tmax >= 45 degC, IMD heat-wave days,
    IMD severe heat-wave days, warm nights, longest consecutive heat-wave spell,
    annual maximum temperature.
Each figure is averaged over the years. For each figure, every district gets a
percentile rank among the 22 districts (0 = lowest in Punjab, 100 = highest;
ties share the average rank). The index is the mean of the seven ranks:

    >= 67  High      (among the most heat-burdened districts)
    33-67  Moderate
    <  33  Low

Ranks are used instead of ratios because some figures (e.g. severe heat-wave
days) are zero in many districts, which would make ratios unstable.

How it changes the alert
------------------------
A High heat-burden district gets the same one-level boost as a High-vulnerability
district (Yellow -> Orange, Orange -> Red) when the day's heat is at least
somewhat unusual (R >= 1). Reasoning: repeated heat leaves a cumulative toll
(chronic dehydration and kidney strain in outdoor workers, crop and income loss,
groundwater and power stress), and these districts are where heat waves are
most likely to develop and last. Total boost from all adjustments stays <= 1 level.
"""
from __future__ import annotations

from app.core.climatology import YEARLY_METRICS

LABELS = {
    "days_tmax_ge_40": "Days with Tmax >= 40 degC per year",
    "days_tmax_ge_45": "Days with Tmax >= 45 degC per year",
    "heatwave_days": "IMD heat-wave days per year",
    "severe_heatwave_days": "IMD severe heat-wave days per year",
    "warm_nights": "Warm nights per year",
    "longest_heatwave_spell_days": "Longest heat-wave spell (days, yearly average)",
    "annual_max_tmax_c": "Annual maximum temperature (degC, average)",
}
HIGH, LOW = 67.0, 33.0


def _percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Percentile rank (0-100) of each key's value among all keys; ties averaged."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        rank = 100 * ((i + j) / 2) / (n - 1) if n > 1 else 50.0
        for k in range(i, j + 1):
            out[items[k][0]] = round(rank, 1)
        i = j + 1
    return out


def build_index(averages: dict[str, dict], period: str) -> dict[str, dict]:
    """``averages``: {district_id: {"mean_<metric>": value, ...}} -> index per district."""
    ranks = {m: _percentile_ranks({d: a[f"mean_{m}"] for d, a in averages.items()}) for m in YEARLY_METRICS}
    punjab_mean = {m: round(sum(a[f"mean_{m}"] for a in averages.values()) / len(averages), 2) for m in YEARLY_METRICS}
    out = {}
    for d, a in averages.items():
        comps = [{
            "metric": m, "label": LABELS[m], "value": a[f"mean_{m}"], "punjab_average": punjab_mean[m],
            "rank_percentile": ranks[m][d],
        } for m in YEARLY_METRICS]
        idx = round(sum(c["rank_percentile"] for c in comps) / len(comps), 1)
        cls = "High" if idx >= HIGH else "Low" if idx < LOW else "Moderate"
        out[d] = {"index": idx, "class": cls, "period": period, "components": comps}
    order = sorted(out, key=lambda d: out[d]["index"], reverse=True)
    for i, d in enumerate(order, 1):
        out[d]["rank"] = i
        out[d]["rank_of"] = len(order)
    return out
