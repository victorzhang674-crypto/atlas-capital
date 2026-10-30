"""Stage 12 Task 4: the optional provider of scheduled (upcoming) earnings dates."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import pytest
from events_helpers import FIXTURES, NoSleep, config_with, utc

from atlas.core.clock import SimClock
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.events import EVENTS
from atlas.data.events.earnings import (
    EarningsCalendarProvider,
    YahooEarningsCalendar,
    build_upcoming_provider,
    ingest_upcoming_earnings,
    ingest_upcoming_events,
)


def _recorded() -> dict[str, dict[str, Any]]:
    """The recorded `Ticker.calendar` answers, with dates back as `date` objects (as yfinance
    returns them)."""
    raw = json.loads((FIXTURES / "yahoo_calendar.json").read_text())
    out: dict[str, dict[str, Any]] = {}
    for symbol, answer in raw.items():
        cal = dict(answer)
        cal["Earnings Date"] = [date.fromisoformat(d) for d in answer["Earnings Date"]]
        cal["Ex-Dividend Date"] = date.fromisoformat(answer["Ex-Dividend Date"])
        out[symbol] = cal
    return out


class FakeCalendar:
    """`calendar_fn`: answers from `answers` by vendor symbol; records each call."""

    def __init__(self, answers: Mapping[str, Mapping[str, Any]], fail: int = 0) -> None:
        self.answers = answers
        self.calls: list[str] = []
        self.fail = fail

    def __call__(self, symbol: str) -> Mapping[str, Any]:
        self.calls.append(symbol)
        if self.fail:
            self.fail -= 1
            raise RuntimeError("yahoo is down")
        return self.answers.get(symbol, {})


def _provider(fn: FakeCalendar) -> YahooEarningsCalendar:
    return YahooEarningsCalendar(calendar_fn=fn, sleep=NoSleep())


def test_is_a_calendar_provider() -> None:
    assert isinstance(_provider(FakeCalendar({})), EarningsCalendarProvider)
    assert YahooEarningsCalendar.name == "yahoo"


def test_yahoo_parses_calendar() -> None:
    out = _provider(FakeCalendar(_recorded())).fetch_upcoming(["AAPL", "MSFT", "JPM"])
    assert list(out.columns) == ["symbol", "event_type", "event_date"]
    earnings = out.loc[out["event_type"] == "earnings"]
    assert dict(zip(earnings["symbol"], earnings["event_date"], strict=True)) == {
        "AAPL": date(2026, 11, 2),
        "MSFT": date(2027, 1, 27),
        "JPM": date(2027, 1, 14),
    }


def test_yahoo_returns_ex_dividend() -> None:
    out = _provider(FakeCalendar(_recorded())).fetch_upcoming(["AAPL", "MSFT", "JPM"])
    dividends = out.loc[out["event_type"] == "ex_dividend"]
    assert dict(zip(dividends["symbol"], dividends["event_date"], strict=True)) == {
        "AAPL": date(2026, 8, 9),
        "MSFT": date(2026, 11, 18),
        "JPM": date(2026, 10, 5),
    }
    only_div = FakeCalendar({"KO": {"Ex-Dividend Date": datetime(2026, 11, 30, 0, 0)}})
    out = _provider(only_div).fetch_upcoming(["KO"])
    assert out.to_dict("records") == [
        {"symbol": "KO", "event_type": "ex_dividend", "event_date": date(2026, 11, 30)}
    ]


def test_yahoo_two_dates_keeps_earliest() -> None:
    fn = FakeCalendar({"AAPL": {"Earnings Date": [date(2026, 11, 5), date(2026, 11, 2)]}})
    out = _provider(fn).fetch_upcoming(["AAPL"])
    assert out["event_date"].tolist() == [date(2026, 11, 2)]


@pytest.mark.parametrize(
    "answer",
    [{}, {"Earnings Date": []}, {"Earnings Date": None}, {"Earnings Date": ["soon"]}],
)
def test_yahoo_missing_date_skipped(answer: dict[str, Any]) -> None:
    out = _provider(FakeCalendar({"AAPL": answer})).fetch_upcoming(["AAPL"])
    assert out.empty


def test_yahoo_accepts_datetimes() -> None:
    fn = FakeCalendar({"AAPL": {"Earnings Date": [datetime(2026, 11, 2, 16, 30)]}})
    assert _provider(fn).fetch_upcoming(["AAPL"])["event_date"].tolist() == [date(2026, 11, 2)]


def test_yahoo_vendor_spelling() -> None:
    fn = FakeCalendar({"BRK-B": {"Earnings Date": [date(2026, 11, 1)]}})
    out = _provider(fn).fetch_upcoming(["brk.b"])
    assert fn.calls == ["BRK-B"]
    assert out["symbol"].tolist() == ["BRK.B"]


def test_yahoo_retries_then_data_error() -> None:
    sleep = NoSleep()
    fn = FakeCalendar({}, fail=10)
    provider = YahooEarningsCalendar(calendar_fn=fn, sleep=sleep)
    with pytest.raises(DataError, match="yahoo calendar AAPL"):
        provider.fetch_upcoming(["AAPL"])
    assert len(fn.calls) == 4
    assert sleep.calls == [1.0, 2.0, 4.0]


def test_yahoo_programming_error_not_retried() -> None:
    sleep = NoSleep()
    calls: list[str] = []

    def broken(symbol: str) -> Mapping[str, Any]:
        calls.append(symbol)
        raise TypeError("bug in the calendar function")

    with pytest.raises(TypeError, match="bug"):
        YahooEarningsCalendar(calendar_fn=broken, sleep=sleep).fetch_upcoming(["AAPL"])
    assert calls == ["AAPL"] and sleep.calls == []


def test_yahoo_rejects_bare_string() -> None:
    with pytest.raises(ValueError, match="sequence"):
        _provider(FakeCalendar({})).fetch_upcoming("AAPL")


def test_ingest_upcoming_uses_clock(tmp_lake: Lake, sim_clock: SimClock) -> None:
    fn = FakeCalendar({"AAPL": {"Earnings Date": [date(2024, 5, 2)]}})
    assert ingest_upcoming_earnings(tmp_lake, _provider(fn), ["AAPL"]) == 1
    row = tmp_lake.read_table(EVENTS.name).iloc[0]
    assert row["available_at"].to_pydatetime() == sim_clock.now()
    assert row["event_id"] == "yahoo-AAPL-2024-05-02"
    assert (row["source"], row["time_of_day"]) == ("yahoo", "unknown")
    assert row["event_type"] == "earnings" and row["event_date"] == date(2024, 5, 2)
    assert row["event_time"] is None or str(row["event_time"]) == "NaT"


def test_ingest_upcoming_keeps_each_snapshot(tmp_lake: Lake) -> None:
    fn = FakeCalendar({"AAPL": {"Earnings Date": [date(2024, 5, 2)]}})
    ingest_upcoming_earnings(tmp_lake, _provider(fn), ["AAPL"], known_at=utc(2024, 4, 1, 12))
    ingest_upcoming_earnings(tmp_lake, _provider(fn), ["AAPL"], known_at=utc(2024, 4, 2, 12))
    stored = tmp_lake.read_table(EVENTS.name)
    assert [t.to_pydatetime() for t in stored["available_at"]] == [
        utc(2024, 4, 1, 12),
        utc(2024, 4, 2, 12),
    ]


def test_ingest_upcoming_stores_ex_dividend(tmp_lake: Lake) -> None:
    fn = FakeCalendar(
        {"AAPL": {"Earnings Date": [date(2024, 5, 2)], "Ex-Dividend Date": date(2024, 5, 10)}}
    )
    assert ingest_upcoming_events(tmp_lake, _provider(fn), ["AAPL"], known_at=utc(2024, 4, 1)) == 2
    stored = tmp_lake.read_table(EVENTS.name).set_index("event_id")
    row = stored.loc["yahoo-div-AAPL-2024-05-10"]
    assert (row["event_type"], row["source"]) == ("ex_dividend", "yahoo")
    assert stored.loc["yahoo-AAPL-2024-05-02", "event_type"] == "earnings"
    assert ingest_upcoming_earnings is ingest_upcoming_events  # the Stage 12 name still works


def test_ingest_upcoming_drops_past_dates(tmp_lake: Lake) -> None:
    fn = FakeCalendar(
        {
            "AAPL": {"Earnings Date": [date(2024, 3, 31)]},
            "MSFT": {"Earnings Date": [date(2024, 4, 1)]},
        }
    )
    known = utc(2024, 4, 1, 12)
    assert ingest_upcoming_earnings(tmp_lake, _provider(fn), ["AAPL", "MSFT"], known_at=known) == 1
    assert tmp_lake.read_table(EVENTS.name)["symbol"].tolist() == ["MSFT"]


def test_ingest_upcoming_rejects_naive_known_at(tmp_lake: Lake) -> None:
    with pytest.raises(ValueError, match="naive"):
        ingest_upcoming_earnings(
            tmp_lake, _provider(FakeCalendar({})), ["AAPL"], known_at=datetime(2024, 4, 1)
        )


def test_build_upcoming_provider() -> None:
    assert isinstance(build_upcoming_provider(AtlasConfig()), YahooEarningsCalendar)
    assert build_upcoming_provider(config_with(upcoming_earnings="none")) is None
