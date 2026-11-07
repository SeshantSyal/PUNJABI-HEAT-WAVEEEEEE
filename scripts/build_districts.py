"""Build app/data/census_districts_2011.json from the Census 2011 district table.

Input: india-districts-census-2011.csv (Census 2011 district-level PCA + houselisting
fields, as published on Kaggle / mirrored at github.com/nihl-jn/India_Census_2011).
Literacy rates come from census2011.co.in (Census 2011 district table), because
literacy rate is calculated on the population aged 7+, and the CSV has no 0-6 column.
Child population 0-6 is then derived exactly:  P_06 = Population - Literate / (rate/100).

    python scripts/build_districts.py path/to/india-districts-census-2011.csv
"""
import csv
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "census_districts_2011.json"

LITERACY_RATE = {  # % of population aged 7+, Census 2011
    "Ludhiana": 82.20, "Amritsar": 76.27, "Gurdaspur": 79.95, "Jalandhar": 82.48, "Firozpur": 68.92,
    "Patiala": 75.28, "Sangrur": 67.99, "Hoshiarpur": 84.59, "Bathinda": 68.28, "Tarn Taran": 67.81,
    "Moga": 70.68, "Sahibzada Ajit Singh Nagar": 83.80, "Muktsar": 65.81, "Kapurthala": 79.07,
    "Mansa": 61.83, "Rupnagar": 82.19, "Faridkot": 69.55, "Shahid Bhagat Singh Nagar": 79.78,
    "Fatehgarh Sahib": 79.35, "Barnala": 67.82,
}
KEY = {"Sahibzada Ajit Singh Nagar": "sas_nagar", "Shahid Bhagat Singh Nagar": "sbs_nagar",
       "Muktsar": "sri_muktsar_sahib", "Fatehgarh Sahib": "fatehgarh_sahib", "Tarn Taran": "tarn_taran"}


def main(path: str) -> None:
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8-sig"))
            if r["State name"].strip().upper() == "PUNJAB"]
    out = {}
    for r in rows:
        n = r["District name"]
        g = lambda k: int(r[k])
        pop = g("Population")
        child = round(pop - g("Literate") / (LITERACY_RATE[n] / 100))
        out[KEY.get(n, n.lower())] = {
            "census_name": n,
            "census_code": r["District code"],
            "population": pop,
            "male": g("Male"),
            "female": g("Female"),
            "child_0_6": child,
            "age_50_plus": g("Age_Group_50"),
            "sc_population": g("SC"),
            "literates": g("Literate"),
            "literacy_rate_pct": LITERACY_RATE[n],
            "workers": g("Workers"),
            "main_workers": g("Main_Workers"),
            "marginal_workers": g("Marginal_Workers"),
            "cultivators": g("Cultivator_Workers"),
            "agricultural_labourers": g("Agricultural_Workers"),
            "household_industry_workers": g("Household_Workers"),
            "other_workers": g("Other_Workers"),
        }
    state = {k: sum(d[k] for d in out.values()) for k in
             ["population", "male", "female", "child_0_6", "age_50_plus", "sc_population", "literates", "workers",
              "main_workers", "marginal_workers", "cultivators", "agricultural_labourers",
              "household_industry_workers", "other_workers"]}
    state["literacy_rate_pct"] = round(100 * state["literates"] / (state["population"] - state["child_0_6"]), 2)
    doc = {
        "_about": "Census of India 2011, 20 districts of Punjab as they existed in 2011. "
                  "child_0_6 is derived from literates and literacy rate (error < 0.1%). "
                  "age_50_plus is the oldest age band in this table and is used as the elderly indicator.",
        "sources": ["Census 2011 district PCA table (india-districts-census-2011.csv, github.com/nihl-jn/India_Census_2011)",
                    "Literacy rates: https://www.census2011.co.in/census/state/districtlist/punjab.html"],
        "projection": {"decadal_growth_rate_pct": 13.89, "base_year": 2011,
                       "_about": "Punjab 2001-2011 decadal growth, used to estimate current population."},
        "state": state,
        "districts": out,
    }
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"{len(out)} districts, Punjab population {state['population']:,}")


if __name__ == "__main__":
    main(sys.argv[1])
