# Punjab Heat-Risk Backend (FastAPI), v2

Heat-wave early-warning backend for **22 districts of Punjab**. Malerkotla is excluded because it was formed in 2021 and has no Census 2011 data of its own.

For each district it:

1. fetches **current weather**, a **5-day forecast** and the **past 30 days** (Open-Meteo, free, no API key);
2. computes hourly thermal stress: **Heat Index + WBGT combined into a final "feels-like in the sun" temperature**;
3. compares the forecast with **that district's own 1991-2020 climate**: anomaly vs normal, percentile rank, and a sudden-rise (acclimatisation) check;
4. checks the **IMD heat-wave criteria**;
5. builds a **Census 2011 vulnerability index** relative to the Punjab average;
6. combines everything in an **absolute x relative risk matrix** to give a Green / Yellow / Orange / Red alert, plus a 0-100 severity score, the reasons and advice;
7. analyses **historical heat 2020-2025** and can **replay a past event**.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate             # Mac/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/docs**.

**First start:** the server downloads 1991-2025 daily history for all 22 districts in the background and saves it to `.cache/`. It is paced to stay under Open-Meteo's free limits (600 calls/min, 5,000/hour, 10,000/day). Each district counts as about 183 calls, so the whole state is about 4,000 calls and takes about 8 minutes. Check progress at `GET /status`. Everything else works meanwhile; only the heat-burden adjustment waits until all 22 are downloaded. After that it never downloads again (until you delete `.cache/`).

Tests run offline with a fake weather service: `python -m pytest -q`

## Endpoints

| Endpoint | What it gives |
|---|---|
| `GET /districts` | 22 districts with vulnerability index, class and rank |
| `GET /districts/{id}/risk` | **Main endpoint**: heat-wave yes/no, daily alert with absolute and relative levels and reasons, current conditions, population, advice. `?replay_date=2024-05-26` replays a past event |
| `GET /districts/{id}/forecast` | Today + 5 days |
| `GET /districts/{id}/current` | Conditions and thermal stress now |
| `GET /districts/{id}/hourly` | Hourly Heat Index, WBGT shade/sun, final feels-like |
| `GET /districts/{id}/census` | Census 2011 profile, vulnerability breakdown, risk factors |
| `GET /districts/{id}/history` | 2020-2025 `yearly_metrics` (the 7 figures below, per year), 6-year `averages`, the district's `heat_burden`, spells, hottest and most unusual days |
| `GET /districts/{id}/climate` | What "normal" means there: normal Tmax and 75th/90th/95th/99th percentiles through the year |
| `GET /punjab/overview` | Alert for every district, worst first (for a state map) |
| `GET /punjab/vulnerability` | Districts ranked by vulnerability |
| `GET /punjab/heat-burden` | Districts ranked by historical heat burden 2020-2025 (feeds the alert) |
| `GET /status` | How many districts' history is downloaded; whether the heat-burden index is ready |
| `GET /calculate?t=45&rh=20&ghi=900&normal_tmax=32&percentile=99.9` | Try the formulas and the matrix by hand |
| `GET /methodology` | Every formula and threshold |

District ids: `amritsar, barnala, bathinda, faridkot, fatehgarh_sahib, fazilka, firozpur, gurdaspur, hoshiarpur, jalandhar, kapurthala, ludhiana, mansa, moga, pathankot, patiala, rupnagar, sas_nagar, sangrur, sbs_nagar, sri_muktsar_sahib, tarn_taran`

## Method

**1. Final feels-like temperature (Heat Index + WBGT).** For each hour, compute WBGT in the shade and in the sun. The sun version uses the black-globe temperature (US National Weather Service method), which accounts for sun angle, radiation and wind. Then find the shade temperature that would give the sun WBGT, and evaluate the NOAA Heat Index there. At night this equals the plain Heat Index.

**2. Absolute level A (0-3)**, the physical heat load. Take the highest of:
- the feels-like category: 32.2 / 39.4 / 51.7 degC give 1 / 2 / 3;
- Tmax: 37 / 40 / 45 degC give 1 / 2 / 3.

**3. Relative level R (0-3)**: how unusual the day is for this district at this time of year, compared with every 1991-2020 day within ±7 days of the date.

| R | Rule |
|---|---|
| 3 | percentile ≥ 99 **or** anomaly > 6.4 degC (IMD severe departure) |
| 2 | percentile ≥ 95 **or** anomaly ≥ 4.5 degC (IMD heat-wave departure) |
| 1 | percentile ≥ 85 **or** anomaly ≥ 2 degC |
| 0 | otherwise |

Sudden rise: if the last 3 days average ≥ 3 degC warmer than the 30 days before them, R goes up by 1, because people are not yet acclimatised. This follows the idea of the Excess Heat Factor (Nairn & Fawcett 2015).

**4. Matrix**

| | R0 normal | R1 above normal | R2 unusual | R3 extreme for this place |
|---|---|---|---|---|
| **A0** | Green | Green | Green | Green |
| **A1** | Yellow | Yellow | Orange | Orange |
| **A2** | Yellow | Orange | **Red** | **Red** |
| **A3** | Orange | **Red** | **Red** | **Red** |

**5. Historical heat burden (2020-2025).** For every district and year, the system counts:
- days with Tmax ≥ 40 degC;
- days with Tmax ≥ 45 degC;
- IMD heat-wave days;
- IMD severe heat-wave days;
- warm nights;
- the longest consecutive heat-wave spell;
- the annual maximum temperature.

Each figure is averaged over the 6 years, and each district gets a percentile rank among the 22 districts for each figure (0 = lowest, 100 = highest). The index is the mean of the 7 ranks: ≥ 67 is High, < 33 is Low.

**6. Adjustments** (at most one level in total):
- A district with **High Census vulnerability or High historical heat burden** goes up one level when R ≥ 1. Repeated heat leaves a cumulative toll (chronic dehydration and kidney strain in outdoor workers, lost income, water and power stress), and these are the districts where heat waves develop and last most often.
- Otherwise, the 3rd consecutive day of Orange-or-worse matrix heat with R ≥ 1 goes from Orange to Red.

**7. Severity score (0-100)** ranks days and districts within a level:

    score = 50 × A/3 + 50 × max(anomaly/8, (percentile − 75)/25)

Example: 45 degC where 32 degC is normal scores 100; 45 degC where 42 degC is normal scores 69.

**8. Vulnerability index** is the mean of (district value / Punjab value) × 100 for five indicators: children 0-6 %, people aged 50+ %, Scheduled Caste %, illiteracy %, and agricultural workers %. Punjab = 100; ≥ 105 is High, < 95 is Low.

## Data
- Weather: Open-Meteo forecast and ERA5 archive, at each district headquarters.
- Census 2011, district level: `app/data/census_districts_2011.json`, built by `scripts/build_districts.py` from the Census district table. The derived child 0-6 figure matches the official Rupnagar figure within 0.05%, and the state totals match exactly.
- Pathankot and Fazilka were created after the 2011 Census, so they use their parent district's (Gurdaspur / Firozpur) 2011 figures. Sangrur's figures include present-day Malerkotla.

## Limitations (state these in the report)
- Census 2011 is old; current population is projected with Punjab's 2001-2011 growth rate.
- 50+ is the oldest age band available in the district table, so it stands in for "elderly".
- One weather point per district (its headquarters) does not capture differences within a district.
- ERA5 smooths extremes, reading 1-3 degC cooler than station maxima. Anomalies and percentiles use ERA5's own climate, so they stay consistent.
- The natural wet-bulb temperature is approximated by the psychrometric wet-bulb.
- IMD officially declares a heat wave when 2 or more stations meet the criteria for 2 or more days. This system evaluates one point per district.
