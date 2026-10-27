"""Stage 07 Task 3: the Alpaca market data adapter, with HTTP mocked by respx."""

import json
import math
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
import respx
from price_helpers import FIXTURES
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, PriceDataConfig, Secrets
from atlas.core.errors import DataError
from atlas.data.providers.alpaca_provider import AlpacaPriceProvider
from atlas.data.providers.base import DAILY_COLUMNS, PriceProvider

BASE = "https://data.alpaca.markets"
KEY, SECRET = "PKTESTKEY123", "s3cr3t-value-never-shown"
START, END = date(2020, 8, 27), date(2020, 9, 2)


def _json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def _bars_handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if params["adjustment"] == "all":
        return httpx.Response(200, json=_json("alpaca_bars_all.json"))
    if params.get("page_token") == "page-2":
        return httpx.Response(200, json=_json("alpaca_bars_raw_p2.json"))
    return httpx.Response(200, json=_json("alpaca_bars_raw_p1.json"))


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/v2/stocks/bars", name="bars").mock(side_effect=_bars_handler)
        router.get("/v1/corporate-actions", name="actions").mock(
            return_value=httpx.Response(200, json=_json("alpaca_corporate_actions.json"))
        )
        yield router


def _provider(sleeps: list[float] | None = None, **cfg: object) -> AlpacaPriceProvider:
    sink = sleeps if sleeps is not None else []
    return AlpacaPriceProvider(
        SecretStr(KEY),
        SecretStr(SECRET),
        PriceDataConfig(**cfg),  # type: ignore[arg-type]
        sleep=sink.append,
    )


def test_is_a_price_provider() -> None:
    provider = _provider()
    assert isinstance(provider, PriceProvider)
    assert provider.name == "alpaca"
    provider.close()


def test_daily_merges_raw_and_adjusted(api: respx.MockRouter) -> None:
    out = _provider().fetch_daily(["AAPL", "SPY"], START, END)
    assert tuple(out.columns) == DAILY_COLUMNS
    assert len(out) == 10
    aapl = out[(out["symbol"] == "AAPL") & (out["date"] == date(2020, 8, 28))].iloc[0]
    assert aapl["close"] == pytest.approx(499.23, abs=1e-3)  # raw, pre-split
    assert aapl["volume"] == 46907500
    assert aapl["adj_close"] == pytest.approx(120.9557, abs=1e-3)  # adjustment=all close
    assert set(out["vendor"]) == {"alpaca"}


def test_requests_carry_key_headers_feed_and_dates(api: respx.MockRouter) -> None:
    _provider().fetch_daily(["AAPL", "SPY"], START, END)
    first = api["bars"].calls[0].request
    assert first.headers["APCA-API-KEY-ID"] == KEY
    assert first.headers["APCA-API-SECRET-KEY"] == SECRET
    params = first.url.params
    assert params["symbols"] == "AAPL,SPY"
    assert params["timeframe"] == "1Day"
    assert params["feed"] == "iex"
    assert params["adjustment"] == "raw"
    assert params["start"] == "2020-08-27T04:00:00Z"  # 00:00 New York
    assert params["end"] == "2020-09-03T03:59:59Z"  # 23:59:59 New York on END
    assert {c.request.url.params["adjustment"] for c in api["bars"].calls} == {"raw", "all"}


def test_follows_next_page_token(api: respx.MockRouter) -> None:
    out = _provider().fetch_daily(["AAPL", "SPY"], START, END)
    raw_calls = [c for c in api["bars"].calls if c.request.url.params["adjustment"] == "raw"]
    assert [c.request.url.params.get("page_token") for c in raw_calls] == [None, "page-2"]
    assert sorted(set(out["symbol"])) == ["AAPL", "SPY"]


def test_retries_429_then_succeeds() -> None:
    with respx.mock(base_url=BASE) as router:
        router.get("/v2/stocks/bars").mock(
            side_effect=[
                httpx.Response(429, json={"message": "too many requests"}),
                httpx.Response(200, json=_json("alpaca_bars_raw_p2.json")),
                httpx.Response(200, json=_json("alpaca_bars_all.json")),
            ]
        )
        sleeps: list[float] = []
        out = _provider(sleeps, backoff_seconds=1.0, min_request_interval_seconds=0).fetch_daily(
            ["SPY"], START, END
        )
    assert len(out) == 5
    assert sleeps == [1.0]


def test_server_error_exhausts_retries() -> None:
    with respx.mock(base_url=BASE) as router:
        route = router.get("/v2/stocks/bars").mock(return_value=httpx.Response(503))
        with pytest.raises(DataError, match="alpaca"):
            _provider(max_retries=2).fetch_daily(["SPY"], START, END)
    assert route.call_count == 3


def test_unauthorized_raises_without_retry_or_secret_in_message() -> None:  # Review focus 5
    with respx.mock(base_url=BASE) as router:
        route = router.get("/v2/stocks/bars").mock(
            return_value=httpx.Response(401, json={"message": "unauthorized."})
        )
        with pytest.raises(DataError, match="401") as info:
            _provider().fetch_daily(["SPY"], START, END)
    assert route.call_count == 1
    assert SECRET not in str(info.value) and KEY not in str(info.value)


def test_transport_error_is_retried() -> None:
    with respx.mock(base_url=BASE) as router:
        router.get("/v2/stocks/bars").mock(
            side_effect=[
                httpx.ConnectError("boom"),
                httpx.Response(200, json=_json("alpaca_bars_raw_p2.json")),
                httpx.Response(200, json=_json("alpaca_bars_all.json")),
            ]
        )
        out = _provider(min_request_interval_seconds=0).fetch_daily(["SPY"], START, END)
    assert len(out) == 5


def test_actions_split_reverse_split_and_dividend(api: respx.MockRouter) -> None:
    out = _provider().fetch_actions(["AAPL", "SPY", "XYZ"], date(2020, 8, 1), date(2020, 9, 30))
    got = {(r.symbol, r.ex_date, r.type): (r.ratio, r.amount) for r in out.itertuples()}
    assert set(got) == {
        ("AAPL", date(2020, 8, 31), "split"),
        ("AAPL", date(2020, 8, 7), "dividend"),
        ("SPY", date(2020, 9, 18), "dividend"),
        ("XYZ", date(2020, 9, 1), "split"),
    }
    assert got[("AAPL", date(2020, 8, 31), "split")][0] == 4.0
    assert got[("XYZ", date(2020, 9, 1), "split")][0] == pytest.approx(0.1)
    assert got[("SPY", date(2020, 9, 18), "dividend")][1] == pytest.approx(1.339)
    assert math.isnan(got[("SPY", date(2020, 9, 18), "dividend")][0])
    params = api["actions"].calls[0].request.url.params
    assert params["types"] == "forward_split,reverse_split,cash_dividend"
    assert params["symbols"] == "AAPL,SPY,XYZ"


def test_actions_outside_window_are_dropped(api: respx.MockRouter) -> None:
    out = _provider().fetch_actions(["AAPL", "SPY"], date(2020, 8, 27), date(2020, 9, 2))
    assert list(zip(out["symbol"], out["type"], strict=True)) == [("AAPL", "split")]


def test_missing_symbol_absent(api: respx.MockRouter) -> None:
    out = _provider().fetch_daily(["AAPL", "SPY", "SIVB"], START, END)
    assert "SIVB" not in set(out["symbol"])


def test_empty_response_gives_empty_frame() -> None:
    with respx.mock(base_url=BASE) as router:
        router.get("/v2/stocks/bars").mock(
            return_value=httpx.Response(200, json={"bars": {}, "next_page_token": None})
        )
        out = _provider().fetch_daily(["SIVB"], START, END)
    assert out.empty and tuple(out.columns) == DAILY_COLUMNS


def test_from_config_none_without_keys() -> None:
    assert AlpacaPriceProvider.from_config(AtlasConfig()) is None
    half = AtlasConfig(secrets=Secrets(alpaca_api_key_id=SecretStr(KEY)))
    assert AlpacaPriceProvider.from_config(half) is None
    full = AtlasConfig(
        secrets=Secrets(alpaca_api_key_id=SecretStr(KEY), alpaca_api_secret_key=SecretStr(SECRET))
    )
    provider = AlpacaPriceProvider.from_config(full)
    assert isinstance(provider, AlpacaPriceProvider)
    assert KEY not in repr(provider) and SECRET not in repr(provider)
    provider.close()
