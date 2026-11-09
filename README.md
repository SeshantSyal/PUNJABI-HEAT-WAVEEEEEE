# Punjab Heatwave Early Warning System (HTSI-Core)

**An interactive Punjab district heat-risk dashboard backed by a FastAPI weather, climate, and vulnerability assessment engine.**

The project combines Open-Meteo weather forecasts and historical reanalysis, thermal-stress calculations, district-specific climate normals, IMD-style heat-wave checks, Census 2011 vulnerability indicators, and historical heat-burden rankings. A **new browser-based command-center frontend** displays district alerts on a clickable Punjab map, with thermal telemetry, vulnerability bars, a temperature chart, and a WhatsApp message shortcut.

> **Important: The supplied frontend is currently a fixed-date historical demo, not a fully live or end-to-end alert-dispatch system.** It requests a replay beginning **26 May 2024** for individual districts. Its Punjab-wide map requests the **current** six-day overview because `/punjab/overview` does **not** implement the `replay_date` parameter. The frontend also has **no actual SMS-sending backend**. See [Frontend integration caveats](#frontend-integration-caveats).

## At a glance

| Component | Technology | Purpose |
|---|---|---|
| Frontend | Standalone HTML, vanilla JavaScript, Tailwind CSS (CDN) | Dark-themed state command center |
| District map | Leaflet 1.9.4 + bundled `punjabData.js` GeoJSON | Interactive, risk-colored polygons for 22 districts |
| Visualization | Chart.js (CDN) | Six-day maximum-temperature line graph |
| Backend | Python, FastAPI, Uvicorn, Pydantic | JSON API for weather, historical replay, risk, population, and methodology |
| Weather and climate | Open-Meteo forecast + historical archive | Current/forecast weather and ERA5-based historical comparisons |
| Risk modelling | `app/core/` | Heat Index, WBGT, IMD-style heat-wave detection, risk matrix, vulnerability, heat burden |
| Population inputs | Census of India 2011 JSON | Five vulnerability components and district population profiles |
| Tests | Pytest + offline mock Open-Meteo | Backend/core regression checks |

**Coverage:** 22 Punjab districts. Malerkotla is excluded from the API dataset because it became a separate district in 2021 and does not have its own Census 2011 district data. The map contains 22 district features.

## Run the complete project

### Prerequisites

- Python **3.10 or newer** (the backend uses Python 3.10+ type-hint syntax).
- A web browser and internet connection for Open-Meteo and the frontend's CDN-hosted libraries.
- No Node.js, npm install, frontend build step, database, or Open-Meteo API key is required for this implementation.

### 1. Start the FastAPI backend

Open a terminal in the **project root** (the directory containing `requirements.txt` and `app/`).

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

If activation is blocked by PowerShell execution policy, you can use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that terminal session, or run the venv Python directly: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload`.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

The API runs at **http://127.0.0.1:8000**. Useful checks:

- Health check: http://127.0.0.1:8000/health
- Interactive API docs: http://127.0.0.1:8000/docs
- History-download progress: http://127.0.0.1:8000/status

### 2. Open the new frontend

Keep the API running. Then open `heatwave_early_warning_dashboard.html` in a browser **from the same folder as `punjabData.js`**. You can double-click the HTML file, or serve the project root with a basic static web server, in another terminal:

```bash
python -m http.server 5500
```

Visit **http://127.0.0.1:5500/heatwave_early_warning_dashboard.html**. The local HTTP-server approach avoids `file://` browser restrictions and is recommended.

**Both servers must remain running:** port `8000` serves API requests; port `5500` (optional) serves only the HTML and local GeoJSON JavaScript file. The frontend currently hardcodes `http://127.0.0.1:8000` as the API base URL; change the two `fetch(...)` URLs in the HTML if you host the API elsewhere. This frontend is **not** automatically served by FastAPI.

The frontend also needs internet access to load Tailwind, Leaflet, Chart.js, Phosphor icons, and Google Fonts from CDNs. The geo-boundaries themselves are supplied locally by `punjabData.js`.

### 3. Understand the first-run download

At startup, the backend starts fetching approximately **1991–2025** daily climate history for all 22 districts and caches it in `.cache/`. The Open-Meteo client rate-limits requests to respect the free-tier quotas, so the initial population of the cache can take several minutes or longer depending on connectivity and service limits. The `GET /status` response reports `downloaded`, `total`, `missing`, and `heat_burden_ready`.

Most API operations can work while the historical burden data is downloading, but the **historical burden adjustment** is not active until every district's required history is available. District-specific normal/climate requests may themselves need historical data. The process resumes using cached downloads on later runs. Set `PREFETCH_ON_START=0` to disable prefetching (see [Configuration](#configuration)). Avoid repeated reloads while the initial download is underway.

## How the system works

```text
                   Open-Meteo Forecast / ERA5 Historical Archive
                                      |
                          app/services/weather.py
                         (networking, caching, pacing)
                                      |
                    app/services/assessment.py
             (district assessment, replay, summaries, history)
                         /                        \
              app/core/*.py             Census/district JSON
       (thermal, climate, IMD,         (2011 population data)
         risk, burden, vulnerability)
                         \                        /
                                app/main.py
                              FastAPI REST API
                                 |         |
                 GET /punjab/overview   GET /districts/{id}/risk
                                 \         /
              heatwave_early_warning_dashboard.html
                      + local punjabData.js
                      Leaflet map + Chart.js
                                 |
                     User-initiated WhatsApp URL
                       (not an SMS/API dispatch)
```

### What the analysis engine computes

1. **Weather inputs:** Current air temperature, humidity, wind, radiation and pressure; daily forecast Tmax/Tmin; and earlier days needed for an acclimatisation check.
2. **Thermal stress:** NOAA-style Heat Index; psychrometric wet-bulb estimate; WBGT in the shade and sun, using estimated globe temperature, solar radiation and wind; and a combined *sun-adjusted feels-like temperature*. The API exposes intermediate values and outdoor-work guidance.
3. **District-specific climate comparison:** Uses a **1991–2020** normal, calculated with a ±7-day seasonal window, to derive temperature anomaly and percentile for a given date. A sudden recent warming can raise relative risk.
4. **Heat-wave checks:** Evaluates IMD-inspired absolute and departure criteria at one weather location per district, and marks multi-day spells. This is **not** an official IMD declaration.
5. **Social vulnerability:** Compares five Census 2011 district percentages with Punjab averages: ages 0–6, ages 50+, Scheduled Caste population, illiteracy, and agricultural workers.
6. **Historical burden:** Computes district-level 2020–2025 averages/rankings for hot days (40°C and 45°C), heat-wave and severe heat-wave days, warm nights, longest heat-wave spell, and annual peak Tmax.
7. **Final alert:** Crosses absolute heat load with relative heat severity, adjusts for high vulnerability/high historical burden or persistent heat subject to a maximum one-level adjustment, and returns **Green, Yellow, Orange or Red**, severity score, explanations and advice.

### Risk classification in more detail

**Absolute severity (`A`, 0–3)** is the higher of the heat-load categories from sun-adjusted feels-like temperature and daily Tmax:

- Feels-like cutoffs: **32.2 / 39.4 / 51.7 °C** → levels 1 / 2 / 3.
- Tmax cutoffs: **37 / 40 / 45 °C** → levels 1 / 2 / 3.

**Relative severity (`R`, 0–3)** measures how exceptional Tmax is for that district and time of year:

| Relative level | Condition |
|---|---|
| R0 | Otherwise normal |
| R1 | ≥85th percentile or ≥2°C above normal |
| R2 | ≥95th percentile or ≥4.5°C above normal |
| R3 | ≥99th percentile or >6.4°C above normal |

If the most recent three-day mean is at least **3°C** warmer than the preceding 30-day mean, relative severity increases by one (up to R3).

The **baseline matrix** is:

| Absolute \ Relative | R0 | R1 | R2 | R3 |
|---|---|---|---|---|
| A0 | Green | Green | Green | Green |
| A1 | Yellow | Yellow | Orange | Orange |
| A2 | Yellow | Orange | Red | Red |
| A3 | Orange | Red | Red | Red |

A district with **High Census vulnerability or High historical heat burden** can increase one alert level when R≥1. Otherwise, the third consecutive day of Orange-or-higher matrix heat with R≥1 can escalate Orange to Red. Total post-matrix escalation is capped at one level. The 0–100 severity score further ranks risk within an alert band; formulas and explanations are returned by `GET /methodology`.

The vulnerability index is the mean of five `(district percentage / Punjab percentage) × 100` ratios, with Punjab = 100: **High ≥105**, **Low <95**. The historical burden index averages percentile ranks of seven measures among the districts: **High ≥67**, **Low <33**.

## New frontend: features and interactions

The frontend is implemented **entirely in** `heatwave_early_warning_dashboard.html`. It is not a React/Vue application and has no package manager configuration.

| UI area | What it displays | Where its values originate |
|---|---|---|
| Header | HTSI-Core / SIH 26083 branding, local browser clock, fixed “SIMULATION: 26 MAY 2024” label | Browser clock + hardcoded date text |
| Punjab map | 22 GeoJSON district polygons, Green/Yellow/Orange/Red colors, hover and click interaction | `punjabData.js` shapes + `/punjab/overview` risk data |
| District overview | Selected district name, WBGT, risk badge, summary text | `/districts/{id}/risk?replay_date=2024-05-26`, **except that the badge prefers the state-overview result** |
| Vulnerability indicators | Ages 50+, agricultural workers, illiteracy percentages as progress bars | `population.vulnerability.components` in district risk response |
| Telemetry | Temperature, RH, solar-load contribution to WBGT, wind speed | District risk response; replay mode has missing current-weather fields and uses some fixed fallback values |
| Line graph | Maximum temperatures for the returned six days | `days[].weekday` and `days[].tmax_c` from district risk response (historical replay in current frontend) |
| Intervention panel | A button enabled for Orange/Red risk and two displayed action options | Overview-preferred risk level; fixed suggested actions |
| WhatsApp shortcut | Opens `wa.me` with a prefilled district, risk and WBGT message | Browser `window.open(...)` only; **no API dispatch** |

**Using the dashboard:** Start the API, open the HTML page, then select a district polygon to update the panels. Tarn Taran is automatically selected when the page loads. The chart and individual district details are wired to the fixed **26 May 2024** replay. The fixed simulation date is part of the frontend source; there is **no date picker or live/replay toggle**.

### Frontend integration caveats

These are implementation findings from the supplied files, **not changes made to the application code**:

1. **Map date mismatch:** `bootSystem()` requests `/punjab/overview?replay_date=2024-05-26`; the backend endpoint does **not** define or forward this query argument, so FastAPI ignores it. The map is driven by the **current six-day forecast** while individual district requests use the **historical replay**. The frontend forces the district badge to match the overview's alert where available, so the badge may also disagree with the replay-day details.
2. **Map colors represent the *maximum six-day forecast alert*** (`max_alert`), **not necessarily today's alert**. This is distinct from the historic district replay.
3. **No SMS/WhatsApp dispatch API exists:** The button named “TRIGGER SMS ALERT API” opens an external WhatsApp URL to a **hardcoded phone number** with a prefilled text message. The user must choose to send it in WhatsApp. No text is sent directly by FastAPI and there is no recipient management, delivery receipt, authentication, or alert log. Do not use it for real emergency operations without appropriate recipient verification and a proper alert service.
4. **Misleading statuses/placeholder content:** The “API CONNECTED” label is static rather than based on a health check; fallback map color is green when overview fails; some replay-mode “live telemetry” fields are substituted with hardcoded numbers (`51` for humidity and `0.6` for wind) or `0` for missing values. These are not observed historical measurements.
5. **Solar Load is not irradiance:** The displayed `solar_load_c` is the difference between sun and shade WBGT (in °C), **not solar radiation in W/m²**, despite the abbreviated display label.
6. **Alert toast is not evidence of delivery:** A dormant toast says an API call succeeded, but the action does not invoke such a call. The dispatch button changes its HTML to “DISPATCHING...” before opening WhatsApp and does not restore it afterwards.
7. **Local-only backend URL:** Both frontend API calls target `127.0.0.1:8000`. Remote deployment requires editing these URLs and configuring deployment/networking appropriately. The current backend enables permissive cross-origin requests and should be secured before public use.
8. **No frontend automated tests:** The included 42 tests exercise the backend and model logic with mocked weather responses; they do not validate real CDN loading, rendered map behavior, browser interactions, or actual Open-Meteo service availability.

To make the dashboard reliably **live**, remove the fixed `replay_date` from district fetches and change the simulation labels; use `today_alert` for current-day colors or deliberately keep `max_alert` for the six-day outlook. To make it reliably **historical**, add date-aware assessment support to `/punjab/overview` and use the same date/alert semantics in the map and district panel. Both changes require application code work beyond this README update.

## API reference

The backend serves JSON; there is no authentication in this version.

| Method / endpoint | Purpose |
|---|---|
| `GET /health` | Basic server health |
| `GET /status` | Cached climate-history progress and heat-burden readiness |
| `GET /districts` | Supported districts, headquarters, location and vulnerability rank |
| `GET /districts/{id}/risk` | Main assessment: summary, current/simulated conditions, six days, vulnerability/burden, explanations/advice, full population profile |
| `GET /districts/{id}/risk?replay_date=2024-05-26` | Six-day historical replay beginning on the specified date (subject to archive delay) |
| `GET /districts/{id}/current` | Current conditions, today and relevant advice |
| `GET /districts/{id}/forecast` | Six-day outlook with warnings |
| `GET /districts/{id}/hourly` | Hour-by-hour thermal metrics |
| `GET /districts/{id}/census` | Census population, vulnerability components and factors |
| `GET /districts/{id}/history` | Historical heat metrics; optional `start_year` / `end_year` |
| `GET /districts/{id}/climate` | District-normal climate and seasonal percentiles |
| `GET /punjab/overview` | **Current** six-day district summaries sorted by maximum alert/severity; response is `{ "districts": [...] }` |
| `GET /punjab/vulnerability` | Statewide social vulnerability ranking |
| `GET /punjab/heat-burden` | Statewide 2020–2025 burden ranking (returns HTTP 202 until ready) |
| `GET /calculate` | Manual thermal/risk calculations from supplied numeric inputs |
| `GET /methodology` | Formulas, thresholds, risk matrix, source descriptions |
| `GET /docs` | Swagger interactive endpoint documentation |

**Useful calls:**

```text
http://127.0.0.1:8000/districts
http://127.0.0.1:8000/punjab/overview
http://127.0.0.1:8000/districts/ludhiana/risk
http://127.0.0.1:8000/districts/ludhiana/risk?replay_date=2024-05-26
http://127.0.0.1:8000/districts/ludhiana/census
http://127.0.0.1:8000/districts/ludhiana/history?start_year=2020&end_year=2025
http://127.0.0.1:8000/calculate?t=45&rh=20&ghi=900&normal_tmax=32&percentile=99.9
```

The valid district IDs are:

```text
amritsar, barnala, bathinda, faridkot, fatehgarh_sahib, fazilka,
firozpur, gurdaspur, hoshiarpur, jalandhar, kapurthala, ludhiana,
mansa, moga, pathankot, patiala, rupnagar, sas_nagar, sangrur,
sbs_nagar, sri_muktsar_sahib, tarn_taran
```

The map converts some boundary labels to API IDs, including **Shahid Bhagat Singh Nagar → `sbs_nagar`**, **Sahibzada Ajit Singh Nagar → `sas_nagar`**, and **Muktsar → `sri_muktsar_sahib`**.

## Project layout

```text
.
├── heatwave_early_warning_dashboard.html  # New single-page frontend
├── punjabData.js                          # Local Punjab district GeoJSON variable
├── app/
│   ├── main.py                            # FastAPI routes, CORS and startup tasks
│   ├── config.py                          # Environment-driven configuration
│   ├── core/
│   │   ├── thermal.py                     # Heat Index, wet bulb, WBGT, feels-like
│   │   ├── heatwave.py                    # IMD-inspired checks and spells
│   │   ├── climatology.py                 # Seasonal normals/percentiles/history
│   │   ├── risk.py                        # Matrix, scores, adjustments and advice
│   │   ├── vulnerability.py               # Census-based social vulnerability
│   │   └── burden.py                      # Historical heat-burden index
│   ├── services/
│   │   ├── weather.py                     # Open-Meteo fetch/cache/rate limiter
│   │   └── assessment.py                  # Assessment & replay orchestration
│   └── data/
│       ├── census_districts_2011.json    # Census values
│       └── districts.json                # District definitions / coordinates
├── scripts/build_districts.py            # Dataset generation helper
├── tests/
│   ├── conftest.py                        # Test configuration
│   ├── fake_open_meteo.py                # Offline weather fixture
│   ├── test_api.py                        # Endpoint tests
│   └── test_core.py                       # Calculation/model tests
├── requirements.txt                      # Python packages (no JS build deps)
├── HowToRun.txt                           # Original short run instructions
├── CHANGES.md                             # Historical backend patch notes
└── README.md                              # This guide
```

Runtime `.cache/` and `.venv/` are generated locally and ignored by Git. Frontend third-party libraries are not vendored.

## Configuration

The backend reads environment variables from its process environment (there is no `.env` loader installed by default):

| Variable | Default | Effect |
|---|---|---|
| `PREFETCH_ON_START` | `1` | `0` disables startup download of all districts' long history |
| `ROPAR_CACHE_DIR` | `<project>/.cache` | Cache location |
| `OPEN_METEO_FORECAST_URL` | `https://api.open-meteo.com/v1/forecast` | Forecast service endpoint |
| `OPEN_METEO_ARCHIVE_URL` | `https://archive-api.open-meteo.com/v1/archive` | Archive service endpoint |
| `HTTP_TIMEOUT_S` | `30` | HTTP timeout in seconds |
| `FORECAST_TTL_S` | `900` | In-memory forecast cache freshness in seconds |
| `ARCHIVE_TTL_S` | `86400` | In-memory archive cache freshness in seconds |
| `NORMAL_START_YEAR` / `NORMAL_END_YEAR` | `1991` / `2020` | Normal/reference climate years |
| `HISTORY_START_YEAR` / `HISTORY_END_YEAR` | `2020` / `2025` | Historical heat-burden period |

The timezone is **Asia/Kolkata**, the outlook is **six days**, and the recent weather lookback is **32 days**. The frontend's replay date and API host are **hardcoded in the HTML**, not controlled by these backend variables.

## Testing

With Python dependencies installed:

```bash
python -m pytest -q
```

**Verification on the supplied ZIP:** 42 tests passed in an offline run using the project's fake Open-Meteo fixtures. This confirms the existing test suite, **not** a browser end-to-end or real-network weather integration test.

## Data quality and scope limitations

- **Census age:** Social vulnerability uses 2011 Census indicators; population estimates are projected using Punjab's 2001–2011 growth rate. `50+` is used as an older-age proxy, not a count of only 60+/65+ residents.
- **District boundaries:** Pathankot and Fazilka use their 2011 parent districts' census values (Gurdaspur and Firozpur respectively). Sangrur's historical figures include what is now Malerkotla. The API's coverage differs from present-day administrative divisions.
- **Spatial resolution:** Weather is evaluated for **one coordinate at the district headquarters**, not for every ward, household, or map polygon. The label “hyper-local” in the frontend should not be read literally.
- **Historical source:** ERA5 reanalysis is modelled/gridded weather and may smooth the hottest station extremes (often by roughly 1–3°C). The baseline normal and percentile comparisons use internally consistent reanalysis values.
- **Thermal approximations:** Natural wet bulb is approximated by a psychrometric calculation; the combined sun-adjusted feels-like value is a project-derived metric, not a direct instrument observation or official IMD product.
- **IMD declaration:** The API checks heat-wave criteria at one point, while official regional declarations use station coverage and persistence requirements; these results are decision-support estimates, **not government alerts**.
- **Data availability:** Initial climate download may be incomplete. API calls require Open-Meteo service access and can fail from rate limiting or connectivity. Until the history cache is complete the burden adjustment may be absent.
- **Operational readiness:** No authenticated operator accounts, alert-recipient registry, SMS gateway, WhatsApp Business API integration, send audit, approval workflow or actual intervention execution are implemented.

## Troubleshooting

| Symptom | Check |
|---|---|
| Map does not render | Confirm `punjabData.js` is beside the HTML file, CDN scripts loaded, and browser console has no JavaScript errors. |
| API requests fail / district shows an error | Confirm FastAPI is listening at `http://127.0.0.1:8000`; visit `/health` and `/docs`. |
| The map appears green everywhere | `/punjab/overview` may have failed; current frontend defaults unknown districts to green, which **does not mean safe**. |
| Map color does not match a district's 2024 chart | Known live-overview / historical-replay mismatch; see [caveats](#frontend-integration-caveats). |
| Heat-burden data is missing | View `/status` and wait for all 22 district histories to be cached; check API logs and Open-Meteo accessibility. |
| Frontend cannot work from a different computer | `127.0.0.1` always means the viewer's own computer; use a reachable backend hostname and set up networking/CORS safely. |
| WhatsApp button does not send a message | It only opens a prefilled `wa.me` URL; the user must actually send, and popup blockers may prevent the new tab. |
| PowerShell refuses venv activation | Use process-scoped execution-policy bypass or run `.venv\Scripts\python.exe` directly. |

## What to improve next

For a production-quality project, prioritize **consistent live-vs-replay mode across all API calls and UI labels**, replace default-green failures with an explicit unknown/error state, display real replay measurements without invented fallbacks, and implement an authenticated, approved alert-dispatch workflow if external notifications are truly needed. Other useful additions are a configurable backend URL, a replay date selector, transparent data timestamps and sources, browser tests, and less permissive CORS.

---

**Original model and backend notes:** `CHANGES.md` documents the historical burden v3 patch; `HowToRun.txt` contains the original abbreviated setup. This README documents the **actual combined backend + newly added frontend** as provided in the project ZIP.
