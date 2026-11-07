# v3 patch: historical data now feeds the risk

Apply on top of v2 (the version with `/districts/...` endpoints).

## How to apply (inside your `ropar_heat_backend` folder)
1. Stop the server (Ctrl+C).
2. Extract this zip **into the `ropar_heat_backend` folder** and choose **Replace** for every file.
3. Nothing to delete.
4. Start again: `python -m uvicorn app.main:app --reload`
5. Open http://127.0.0.1:8000/status and wait until `"heat_burden_ready": true` (about 8 minutes the first time).
6. Optional: `python -m pytest -q` should print **42 passed**.

Tip: while the first download runs, avoid saving code files. `--reload` restarts the server, and the download then resumes from where it stopped.

## Files in this patch
| File | Status | What changed |
|---|---|---|
| `app/core/burden.py` | NEW | Historical heat-burden index (7 yearly figures, ranked across 22 districts) |
| `app/core/climatology.py` | edited | Adds `longest_heatwave_spell_days`, `annual_max_tmax_c`, `yearly_metrics` and 6-year `averages` |
| `app/core/risk.py` | edited | `classify_days(..., burden_class)`: High heat burden raises the level like High vulnerability (still max +1 in total) |
| `app/services/assessment.py` | edited | Computes the index, passes it into the alert, background `prefetch_all_history`, `download_status` |
| `app/services/weather.py` | edited | Open-Meteo call weighting + rate limiter (stays under 600/min and 5,000/h); clear message on HTTP 429 |
| `app/main.py` | edited | Background download at start; new `/status` and `/punjab/heat-burden`; `heat_burden_class` in `/calculate` |
| `app/config.py` | 1 line | `PREFETCH_ON_START` (set the environment variable to `0` to switch the background download off) |
| `tests/conftest.py`, `tests/test_api.py` | edited | 42 tests; tests never download real data |
| `README.md`, `CHANGES.md` | edited | Docs |

## Why the rate limiter
Open-Meteo counts one request of 35 years x 2 variables as about 183 calls. Downloading all 22 districts at once (about 4,000 calls) would break the 600 calls/minute free limit and return HTTP 429. v2's `/punjab/overview` could hit this on its first run. Requests are now spaced automatically.

## New in `/districts/{id}/risk`
- `heat_burden`: `{index, class, rank, rank_of, period, averages}`. While history is still downloading it is `null`, and `notes` says "being prepared".
- In `days[].reasons` you will see, for example: "High historical heat burden (2020-2025) with above-normal heat -> raised to Red".
