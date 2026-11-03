"""Stage 11 Task 3: the Alpaca (Benzinga) news adapter, with HTTP mocked by respx."""

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from news_helpers import FIXTURES, utc
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, NewsConfig, Secrets
from atlas.core.errors import DataError
from atlas.data.news import ARTICLE_COLUMNS, NewsProvider
from atlas.data.news.alpaca_news import AlpacaNewsProvider

URL = "https://data.alpaca.markets/v1beta1/news"
KEY, SECRET = "PKNEWSKEY123", "n3ws-s3cr3t-never-shown"
START, END = utc(2020, 3, 2), utc(2020, 3, 3)


def _page(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _news_handler(request: httpx.Request) -> httpx.Response:
    """Serve the two fixture pages, keeping only items tagged with a requested symbol."""
    params = request.url.params
    page = _page(
        "alpaca_news_p2.json" if params.get("page_token") == "page-2" else "alpaca_news_p1.json"
    )
    if "symbols" in params:
        wanted = set(params["symbols"].split(","))
        page["news"] = [n for n in page["news"] if wanted & set(n["symbols"])]
    return httpx.Response(200, json=page)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(URL, name="news").mock(side_effect=_news_handler)
        yield router


def _provider(sleeps: list[float] | None = None, **cfg: Any) -> AlpacaNewsProvider:
    sink = sleeps if sleeps is not None else []
    return AlpacaNewsProvider(
        SecretStr(KEY), SecretStr(SECRET), NewsConfig(**cfg), sleep=sink.append
    )


def test_is_a_news_provider() -> None:
    provider = _provider()
    assert isinstance(provider, NewsProvider)
    assert provider.name == "alpaca"


def test_fetch_follows_pages(api: respx.MockRouter) -> None:
    out = _provider().fetch_news(["AAPL", "MSFT"], START, END)
    assert api["news"].call_count == 2
    assert tuple(out.columns) == ARTICLE_COLUMNS
    assert list(out["article_id"]) == [
        "alpaca-15000001",
        "alpaca-15000002",
        "alpaca-15000003",
        "alpaca-15000004",
    ]
    assert set(out["source"]) == {"benzinga"} and set(out["provider"]) == {"alpaca"}
    assert out.loc[0, "published_at"] == utc(2020, 3, 2, 13, 5)
    assert out.loc[1, "updated_at"] == utc(2020, 3, 2, 14, 45)
    assert len(out.loc[1, "symbols"]) == 12
    assert out.loc[2, "symbols"] == ("MSFT",)  # the index tag ^DJI is dropped
    assert out.loc[0, "url"] == "https://www.benzinga.com/news/20/03/15000001/story"


def test_request_parameters(api: respx.MockRouter) -> None:
    _provider().fetch_news(["AAPL", "MSFT"], START, END)
    first, second = (call.request for call in api["news"].calls)
    params = first.url.params
    assert params["symbols"] == "AAPL,MSFT"
    assert params["limit"] == "50"
    assert params["sort"] == "asc"
    assert params["include_content"] == "false"
    assert params["start"] == "2020-03-02T00:00:00Z"
    assert params["end"] == "2020-03-03T00:00:00Z"
    assert "page_token" not in params
    assert second.url.params["page_token"] == "page-2"
    assert first.headers["APCA-API-KEY-ID"] == KEY
    assert first.headers["APCA-API-SECRET-KEY"] == SECRET


def test_market_wide_request_has_no_symbols(api: respx.MockRouter) -> None:
    out = _provider().fetch_news(None, START, END)
    assert "symbols" not in api["news"].calls[0].request.url.params
    assert len(out) == 4


def test_symbols_batched(api: respx.MockRouter) -> None:
    sleeps: list[float] = []
    _provider(sleeps, symbols_per_request=1).fetch_news(["AAPL", "MSFT"], START, END)
    requested = [c.request.url.params["symbols"] for c in api["news"].calls]
    assert requested == ["AAPL", "AAPL", "MSFT", "MSFT"]
    assert sleeps == [0.3, 0.3, 0.3]  # throttle between requests, none before the first


def test_alpaca_article_in_two_batches_stored_once(api: respx.MockRouter) -> None:
    out = _provider(symbols_per_request=1).fetch_news(["AAPL", "MSFT"], START, END)
    assert list(out["article_id"]).count("alpaca-15000002") == 1
    assert out["article_id"].is_unique


def test_results_outside_window_dropped(api: respx.MockRouter) -> None:
    out = _provider().fetch_news(["AAPL"], START, utc(2020, 3, 2, 16))
    assert "alpaca-15000004" not in set(out["article_id"])  # 16:00 is the exclusive end
    assert "alpaca-15000005" not in set(_provider().fetch_news(["AAPL"], START, END)["article_id"])


def test_content_never_stored(api: respx.MockRouter) -> None:
    out = _provider().fetch_news(["AAPL", "MSFT"], START, END)
    text = out.astype(str).to_string()
    assert "FULL ARTICLE BODY" not in text
    assert "content" not in out.columns


def test_empty_symbol_list_makes_no_request(api: respx.MockRouter) -> None:
    out = _provider().fetch_news([], START, END)
    assert out.empty and api["news"].call_count == 0


def test_retries_then_data_error(api: respx.MockRouter) -> None:
    api["news"].mock(return_value=httpx.Response(503, text="busy"))
    sleeps: list[float] = []
    with pytest.raises(DataError, match="4 attempt"):
        _provider(sleeps, min_request_interval_seconds=0).fetch_news(["AAPL"], START, END)
    assert sleeps == [1.0, 2.0, 4.0]


def test_transport_error_retried(api: respx.MockRouter) -> None:
    api["news"].mock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(200, json=_page("alpaca_news_p2.json")),
        ]
    )
    out = _provider().fetch_news(["AAPL"], START, END)
    assert list(out["article_id"]) == ["alpaca-15000004"]


def test_http_400_is_data_error(api: respx.MockRouter) -> None:
    api["news"].mock(return_value=httpx.Response(422, json={"message": "invalid symbols"}))
    with pytest.raises(DataError, match="HTTP 422"):
        _provider().fetch_news(["AAPL"], START, END)
    assert api["news"].call_count == 1


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"news": {}},
        {"news": [{"headline": "no id", "created_at": "2020-03-02T13:00:00Z"}]},
        {"news": [{"id": 1, "headline": "no time"}]},
        {"news": ["not an object"]},
    ],
)
def test_bad_body_is_data_error(api: respx.MockRouter, body: object) -> None:
    api["news"].mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(DataError):
        _provider().fetch_news(["AAPL"], START, END)


def test_non_json_is_data_error(api: respx.MockRouter) -> None:
    api["news"].mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(DataError, match="not JSON"):
        _provider().fetch_news(["AAPL"], START, END)


def test_keys_never_in_repr_logs_or_errors(
    api: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    api["news"].mock(return_value=httpx.Response(403, text="forbidden"))
    provider = _provider()
    with caplog.at_level(logging.DEBUG), pytest.raises(DataError) as info:
        provider.fetch_news(["AAPL"], START, END)
    for text in (repr(provider), str(info.value), caplog.text):
        assert KEY not in text and SECRET not in text


def test_from_config_without_keys_is_none() -> None:
    assert AlpacaNewsProvider.from_config(AtlasConfig()) is None
    empty = AtlasConfig(secrets=Secrets(alpaca_api_key_id="", alpaca_api_secret_key=""))
    assert AlpacaNewsProvider.from_config(empty) is None
    cfg = AtlasConfig(secrets=Secrets(alpaca_api_key_id=KEY, alpaca_api_secret_key=SECRET))
    provider = AlpacaNewsProvider.from_config(cfg)
    assert isinstance(provider, AlpacaNewsProvider)
    provider.close()


def test_parses_recorded_answer() -> None:
    # Phase 1 checkpoint, Tier 2: a real answer (recorded 2026-11-02; summaries and images
    # removed). Real 2020 items have an empty `source`, empty `summary` and `content`, and
    # roundups tagged with up to 39 tickers.
    page = _page("alpaca_news_aapl_recorded.json")
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, json=page))
        out = _provider().fetch_news(["AAPL"], START, END)
    assert tuple(out.columns) == ARTICLE_COLUMNS
    assert len(out) == len(page["news"]) == 8
    assert list(out["article_id"]) == [f"alpaca-{n['id']}" for n in page["news"]]
    assert out["published_at"].min() >= START and out["published_at"].max() < END
    assert set(out["source"]) == {"alpaca"}  # the empty vendor source falls back
    assert all("AAPL" in tags for tags in out["symbols"])
    assert max(len(tags) for tags in out["symbols"]) == 39
    assert out["headline"].str.len().gt(0).all()
