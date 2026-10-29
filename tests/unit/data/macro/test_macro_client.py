"""Stage 10 Task 2: the FRED client (requests, pagination, rate limit, retries, raw cache)."""

import io
import logging
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
import respx
from macro_helpers import (
    API,
    CPI_REQUEST,
    TEST_KEY,
    SleepRecorder,
    fred_client,
    serve_fixture,
)
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, MacroConfig, Secrets
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.storage import Lake
from atlas.data.macro import FredClient, build_fred_client

SERIES_URL = f"{API}/series"


@pytest.fixture
def fred() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.stlouisfed.org").mock(side_effect=serve_fixture)
        yield router


def test_series_metadata_request(fred: respx.MockRouter, tmp_lake: Lake) -> None:
    client = fred_client(tmp_lake)
    data = client.series("CPIAUCSL")
    assert data["seriess"][0]["frequency_short"] == "M"
    request = fred.calls.last.request
    assert request.url.path == "/fred/series"
    assert request.url.params["series_id"] == "CPIAUCSL"
    assert request.url.params["api_key"] == TEST_KEY
    assert request.url.params["file_type"] == "json"
    cached = tmp_lake.raw_path("fred", "series") / "CPIAUCSL.json"
    assert cached.exists()
    assert TEST_KEY not in cached.read_text()
    client.series("CPIAUCSL")
    assert fred.calls.call_count == 1  # served from the raw archive
    client.series("CPIAUCSL", refresh=True)
    assert fred.calls.call_count == 2
    assert all(TEST_KEY not in p.name for p in tmp_lake.raw_dir.rglob("*"))


def test_vintage_dates_paginates_and_filters(fred: respx.MockRouter) -> None:
    client = fred_client(config=MacroConfig(page_limit=2))
    dates = client.vintage_dates(
        "CPIAUCSL", realtime_start=date(2020, 1, 1), realtime_end=date(2020, 12, 31)
    )
    assert len(dates) == 13
    assert dates[0] == date(2020, 1, 14) and dates[-1] == date(2020, 12, 10)
    assert date(2020, 2, 11) in dates and date(2020, 4, 10) in dates
    assert fred.calls.call_count == 7  # 13 dates in pages of 2


def test_observations_request_params(fred: respx.MockRouter) -> None:
    data = fred_client().observations("CPIAUCSL", **CPI_REQUEST)
    params = fred.calls.last.request.url.params
    assert params["output_type"] == "1"
    assert params["observation_start"] == "2018-01-01"
    assert params["realtime_end"] == "2021-12-31"
    assert params["limit"] == "100000" and params["offset"] == "0"
    assert data["count"] == len(data["observations"]) == 114
    first = {"realtime_start": "2020-04-10", "date": "2020-03-01", "value": "257.953"}
    assert any(first.items() <= row.items() for row in data["observations"])


def test_observations_paginates(fred: respx.MockRouter) -> None:
    data = fred_client(config=MacroConfig(page_limit=50)).observations("CPIAUCSL", **CPI_REQUEST)
    assert fred.calls.call_count == 3
    assert [c.request.url.params["offset"] for c in fred.calls] == ["0", "50", "100"]
    assert len(data["observations"]) == 114


def test_observations_short_count_raises() -> None:
    with respx.mock() as router:
        router.get(f"{API}/series/observations").mock(
            return_value=httpx.Response(200, json={"count": 3, "observations": [], "offset": 0})
        )
        with pytest.raises(DataError, match="3"):
            fred_client().observations("CPIAUCSL", **CPI_REQUEST)


def test_client_throttles(fred: respx.MockRouter) -> None:
    sleep = SleepRecorder()
    client = fred_client(sleep=sleep)
    client.series("CPIAUCSL")
    assert sleep.calls == []
    client.series("DGS10")
    client.observations("CPIAUCSL", **CPI_REQUEST)
    assert sleep.calls == [pytest.approx(0.5), pytest.approx(0.5)]


def test_client_retries_429_and_5xx() -> None:
    sleep = SleepRecorder()
    with respx.mock() as router:
        route = router.get(SERIES_URL)
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json={"seriess": []}),
        ]
        data = fred_client(sleep=sleep).series("CPIAUCSL")
    assert data == {"seriess": []}
    assert route.call_count == 3
    assert sleep.calls == [1.0, 0.5, 2.0, 0.5]  # backoff, throttle, backoff, throttle


def test_client_gives_up_after_retries() -> None:
    config = MacroConfig(max_retries=2, backoff_seconds=0.0)
    with respx.mock() as router:
        route = router.get(SERIES_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DataError, match="3 attempt"):
            fred_client(config=config).series("CPIAUCSL")
    assert route.call_count == 3


def test_http_400_reports_fred_message(fred: respx.MockRouter) -> None:
    with pytest.raises(DataError, match="series does not exist") as err:
        fred_client().series("NOSUCH")
    assert fred.calls.call_count == 1  # a 400 is not retried
    assert "NOSUCH" in str(err.value)


def test_other_http_status_raises() -> None:
    with respx.mock() as router:
        route = router.get(SERIES_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(DataError, match="404"):
            fred_client().series("CPIAUCSL")
    assert route.call_count == 1


def test_bad_json_raises() -> None:
    with respx.mock() as router:
        router.get(SERIES_URL).mock(return_value=httpx.Response(200, content=b"<html>"))
        with pytest.raises(DataError, match="JSON"):
            fred_client().series("CPIAUCSL")
        router.get(SERIES_URL).mock(return_value=httpx.Response(200, json=[1, 2]))
        with pytest.raises(DataError, match="object"):
            fred_client().series("CPIAUCSL")


def test_blank_key_rejected() -> None:
    with pytest.raises(ConfigError, match="FRED_API_KEY"):
        FredClient(MacroConfig(), api_key="  ")


def test_build_fred_client_requires_key() -> None:
    with pytest.raises(ConfigError, match="FRED_API_KEY"):
        build_fred_client(AtlasConfig())
    cfg = AtlasConfig(secrets=Secrets(fred_api_key=SecretStr(TEST_KEY)))
    with build_fred_client(cfg) as client:
        assert isinstance(client, FredClient)


def test_api_key_never_logged_or_raised() -> None:
    stream = io.StringIO()
    configure_logging(level="DEBUG", console=stream)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    config = MacroConfig(max_retries=1, backoff_seconds=0.0)
    try:
        with respx.mock() as router:
            router.get(SERIES_URL).mock(
                side_effect=httpx.ConnectError(f"cannot reach {SERIES_URL}?api_key={TEST_KEY}")
            )
            with pytest.raises(DataError) as err:
                fred_client(config=config).series("CPIAUCSL")
            router.get(SERIES_URL).mock(
                return_value=httpx.Response(
                    400, json={"error_code": 400, "error_message": f"bad key {TEST_KEY}"}
                )
            )
            with pytest.raises(DataError) as err400:
                fred_client(config=config).series("CPIAUCSL")
    finally:
        reset_logging()
    text = stream.getvalue()
    assert "macro.request_failed" in text
    assert TEST_KEY not in text
    for exc in (err.value, err400.value):
        chain: BaseException | None = exc
        while chain is not None:
            assert TEST_KEY not in str(chain)
            chain = chain.__cause__ or chain.__context__
