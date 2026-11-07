import os

os.environ["PREFETCH_ON_START"] = "0"   # never download real data during tests

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import assessment  # noqa: E402
from app.services.weather import OpenMeteoClient  # noqa: E402
from tests.fake_open_meteo import FakeOpenMeteo  # noqa: E402


def _client(tmp_path, scenario):
    fake = FakeOpenMeteo(scenario)
    assessment._NORMALS_CACHE.clear()
    assessment._BURDEN_CACHE.clear()
    with TestClient(app) as c:
        app.state.weather = OpenMeteoClient(
            client=httpx.AsyncClient(transport=httpx.MockTransport(fake)), cache_dir=tmp_path, rate_limit=False)
        c.fake = fake
        yield c


@pytest.fixture
def hot_api(tmp_path):
    yield from _client(tmp_path, "hot")


@pytest.fixture
def mild_api(tmp_path):
    yield from _client(tmp_path, "mild")


@pytest.fixture
def early_api(tmp_path):
    yield from _client(tmp_path, "early")
