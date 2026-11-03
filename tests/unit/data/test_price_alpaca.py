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
from atlas.data.providers.base import ACTION_COLUMNS, DAILY_COLUMNS, PriceProvider

BASE = "https://data.alpaca.markets"
KEY, SECRET = "PKTESTKEY123", "s3cr3t-value-never-shown"
START, END = date(2020, 8, 27), date(2020, 9, 2)


def _json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


# The fixtures are real Alpaca answers (recorded 2026-11-02, IEX feed); page 1 links to page 2
# with this opaque token.
PAGE_2 = _json("alpaca_bars_raw_p1.json")["next_page_token"]  # type: ignore[index]


def _bars_handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if params["adjustment"] == "all":
        return httpx.Response(200, json=_json("alpaca_bars_all.json"))
    if params.get("page_token") == PAGE_2:
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
    assert aapl["close"] == pytest.approx(499.345, abs=1e-3)  # raw, pre-split (IEX)
    assert aapl["volume"] == 455748  # raw IEX volume, not the adjusted (x4) one
    assert aapl["adj_close"] == pytest.approx(120.98, abs=1e-3)  # adjustment=all close
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
    assert isinstance(PAGE_2, str) and PAGE_2
    assert [c.request.url.params.get("page_token") for c in raw_calls] == [None, PAGE_2]
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


def test_actions_split_and_dividend(api: respx.MockRouter) -> None:
    out = _provider().fetch_actions(["AAPL", "SPY"], date(2020, 8, 1), date(2020, 9, 30))
    got = {(r.symbol, r.ex_date, r.type): (r.ratio, r.amount) for r in out.itertuples()}
    assert set(got) == {  # AAPL's 2020-11-06 dividend is in the answer, outside the window
        ("AAPL", date(2020, 8, 31), "split"),
        ("AAPL", date(2020, 8, 7), "dividend"),
        ("SPY", date(2020, 9, 18), "dividend"),
    }
    assert got[("AAPL", date(2020, 8, 31), "split")][0] == 4.0
    assert got[("AAPL", date(2020, 8, 7), "dividend")][1] == pytest.approx(0.82)  # pre-split
    assert got[("SPY", date(2020, 9, 18), "dividend")][1] == pytest.approx(1.339224)
    assert math.isnan(got[("SPY", date(2020, 9, 18), "dividend")][0])
    params = api["actions"].calls[0].request.url.params
    assert params["types"] == "forward_split,reverse_split,cash_dividend"
    assert params["symbols"] == "AAPL,SPY"


def test_actions_reverse_split() -> None:
    # GE's 1-for-8 reverse split, ex-date 2021-08-02 (real answer, recorded 2026-11-02).
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/corporate-actions").mock(
            return_value=httpx.Response(200, json=_json("alpaca_corporate_actions_ge.json"))
        )
        out = _provider().fetch_actions(["GE"], date(2021, 7, 26), date(2021, 8, 6))
    assert list(zip(out["symbol"], out["ex_date"], out["type"], strict=True)) == [
        ("GE", date(2021, 8, 2), "split")
    ]
    assert out["ratio"].iloc[0] == pytest.approx(0.125)


def test_action_listed_once_per_cusip_is_counted_once() -> None:
    # Phase 1 checkpoint, Tier 2: the real answer lists GE's 2021-06-25 dividend twice, once
    # per CUSIP (the reverse split changed it); counted twice it doubled the dividend.
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/corporate-actions").mock(
            return_value=httpx.Response(200, json=_json("alpaca_corporate_actions_ge.json"))
        )
        out = _provider().fetch_actions(["GE"], date(2021, 6, 1), date(2021, 6, 30))
    assert list(zip(out["ex_date"], out["type"], out["amount"], strict=True)) == [
        (date(2021, 6, 25), "dividend", pytest.approx(0.01))
    ]


def test_dividends_on_one_ex_date_are_summed() -> None:
    # A regular and a special dividend with the same ex-date share the table's key
    # (symbol, ex_date, type): one row with the total, as Yahoo reports them.
    def dividend(rate: float, cusip: str, ident: str) -> dict[str, object]:
        return {"id": ident, "symbol": "XYZ", "cusip": cusip, "rate": rate,
                "ex_date": "2020-09-01", "process_date": "2020-09-15"}  # fmt: skip

    def split(ident: str) -> dict[str, object]:
        return {"id": ident, "symbol": "XYZ", "new_rate": 2, "old_rate": 1,
                "ex_date": "2020-09-01", "process_date": "2020-09-01"}  # fmt: skip

    body = {
        "corporate_actions": {
            "cash_dividends": [dividend(0.5, "1", "a"), dividend(1.0, "1", "b")],
            "forward_splits": [split("s1"), split("s2")],  # the same split listed twice
        },
        "next_page_token": None,
    }
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/corporate-actions").mock(return_value=httpx.Response(200, json=body))
        out = _provider().fetch_actions(["XYZ"], date(2020, 9, 1), date(2020, 9, 1))
    got = {r.type: (r.ratio, r.amount) for r in out.itertuples()}
    assert len(out) == 2
    assert got["dividend"][1] == pytest.approx(1.5)
    assert got["split"][0] == 2.0


def test_actions_outside_window_are_dropped(api: respx.MockRouter) -> None:
    out = _provider().fetch_actions(["AAPL", "SPY"], date(2020, 8, 27), date(2020, 9, 2))
    assert list(zip(out["symbol"], out["type"], strict=True)) == [("AAPL", "split")]


def test_no_action_in_window_gives_empty_frame(api: respx.MockRouter) -> None:
    out = _provider().fetch_actions(["AAPL", "SPY"], date(2020, 9, 21), date(2020, 9, 25))
    assert out.empty and tuple(out.columns) == ACTION_COLUMNS


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
