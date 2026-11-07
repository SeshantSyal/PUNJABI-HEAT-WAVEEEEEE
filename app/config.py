"""Settings. Values can be overridden with environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = Path(os.getenv("ROPAR_CACHE_DIR", BASE_DIR.parent / ".cache"))

FORECAST_URL = os.getenv("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
ARCHIVE_URL = os.getenv("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
TIMEZONE = "Asia/Kolkata"
HTTP_TIMEOUT_S = float(os.getenv("HTTP_TIMEOUT_S", "30"))
FORECAST_TTL_S = int(os.getenv("FORECAST_TTL_S", "900"))      # 15 min
ARCHIVE_TTL_S = int(os.getenv("ARCHIVE_TTL_S", "86400"))      # 1 day

# Climatological normal period (IMD uses 30-year normals)
NORMAL_START_YEAR = int(os.getenv("NORMAL_START_YEAR", "1991"))
NORMAL_END_YEAR = int(os.getenv("NORMAL_END_YEAR", "2020"))
NORMAL_WINDOW_DAYS = 7        # +/- days around each calendar day when averaging

# History analysis period requested for the project
HISTORY_START_YEAR = int(os.getenv("HISTORY_START_YEAR", "2020"))
HISTORY_END_YEAR = int(os.getenv("HISTORY_END_YEAR", "2025"))

FORECAST_DAYS = 6             # today + 5 days
PAST_DAYS = 32                # recent days fetched for the acclimatisation check (3 + 30 - 1, +margin)

# Vulnerability classes (composite index, Punjab average = 100)
VULN_HIGH = 105.0
VULN_LOW = 95.0

# Download all districts' climate history in the background when the server starts
PREFETCH_ON_START = os.getenv("PREFETCH_ON_START", "1") == "1"
