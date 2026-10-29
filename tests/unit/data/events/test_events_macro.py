"""Stage 12 Task 6: the FOMC / CPI / NFP calendar (static YAML + FRED release calendars)."""

import json
import logging
from datetime import date, time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
from events_helpers import FIXTURES, REPO_ROOT, NoSleep, et, utc

from atlas.core.config import ConfigError, MacroConfig
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.events import MACRO_EVENT_COLUMNS, MACRO_EVENTS
from atlas.data.events.macro_calendar import (
    MacroCalendar,
    fomc_events,
    ingest_macro_calendar,
    load_macro_calendar,
    release_events,
    scheduled_available_at,
)
from atlas.data.macro.fred_client import FredClient

KEY = "testkey0123456789abcdef0123456789"
API = "https://api.stlouisfed.org/fred"
CALENDAR = REPO_ROOT / "config" / "macro_calendar.yaml"


def _json(name: str) -> Any:
    return json.loads((FIXTURES / "fred" / name).read_text())


def _dates(name: str) -> tuple[date, ...]:
    return tuple(date.fromisoformat(r["date"]) for r in _json(name)["release_dates"])


def _initial(series: str) -> tuple[date, ...]:
    rows = _json(f"initial_{series}.json")["observations"]
    return tuple(sorted({date.fromisoformat(r["realtime_start"]) for r in rows}))


def serve_fred(request: httpx.Request) -> httpx.Response:
    """respx side effect: FRED release dates and initial releases from the recorded files."""
    params = request.url.params
    if request.url.path.endswith("/release/dates"):
        name = f"release_dates_{params['release_id']}.json"
    elif params.get("output_type") == "4":
        name = f"initial_{params['series_id']}.json"
    else:
        return httpx.Response(404)
    return httpx.Response(200, content=(FIXTURES / "fred" / name).read_bytes())


def _client(http: httpx.Client | None = None) -> FredClient:
    return FredClient(MacroConfig(), api_key=KEY, http=http, sleep=NoSleep())


@pytest.fixture
def calendar() -> MacroCalendar:
    return load_macro_calendar(CALENDAR)


# --- the YAML ---------------------------------------------------------------------------------


def test_load_macro_calendar_repo_file(calendar: MacroCalendar) -> None:
    years = [d.year for d in calendar.fomc.meetings]
    assert {y: years.count(y) for y in set(years)} == {y: 8 for y in range(2018, 2028)}
    assert len(calendar.fomc.meetings) == 80
    assert date(2020, 3, 18) in calendar.fomc.meetings
    assert [c.date for c in calendar.fomc.cancelled] == [date(2020, 3, 18)]
    assert [(u.date, u.time) for u in calendar.fomc.unscheduled] == [
        (date(2020, 3, 3), time(10, 0)),
        (date(2020, 3, 15), time(17, 0)),
    ]
    assert date(2019, 10, 4) not in calendar.fomc.meetings
    assert date(2025, 8, 22) not in calendar.fomc.meetings
    assert calendar.fomc.statement_time == time(14, 0)
    assert calendar.releases["cpi"].fred_release_id == 10
    assert calendar.releases["nfp"].headline_series == "PAYEMS"
    assert calendar.releases["nfp"].release_time == time(8, 30)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "macro_calendar.yaml"
    path.write_text(text, encoding="utf-8")
    return path


_RELEASES = (
    'releases: {cpi: {fred_release_id: 10, headline_series: CPIAUCSL, release_time: "08:30"}}\n'
)


@pytest.mark.parametrize(
    ("fomc", "pattern"),
    [
        ('{statement_time: "14:00", meetings: {2020: [2020-01-29, 2020-01-29]}}', "duplicate"),
        ('{statement_time: "14:00", meetings: {2020: [2021-01-27]}}', "2021-01-27"),
        (
            '{statement_time: "14:00", meetings: {2020: [2020-03-18]}, '
            'cancelled: [{date: 2020-03-18, cancelled_at: "2020-03-15T17:00:00"}]}',
            "timezone",
        ),
        (
            '{statement_time: "14:00", meetings: {2020: [2020-01-29]}, '
            'cancelled: [{date: 2020-03-18, cancelled_at: "2020-03-15T17:00:00-04:00"}]}',
            "not a scheduled meeting",
        ),
        (
            '{statement_time: "14:00", meetings: {2020: [2020-03-03]}, '
            'unscheduled: [{date: 2020-03-03, time: "10:00"}]}',
            "duplicate",
        ),
    ],
)
def test_macro_calendar_rejects(tmp_path: Path, fomc: str, pattern: str) -> None:
    path = _write(tmp_path, f"fomc: {fomc}\n{_RELEASES}")
    with pytest.raises(ConfigError, match=pattern):
        load_macro_calendar(path)


def test_macro_calendar_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_macro_calendar(tmp_path / "nope.yaml")


# --- FOMC events --------------------------------------------------------------------------------


def test_scheduled_available_at() -> None:
    assert scheduled_available_at(date(2024, 7, 31)) == utc(2024, 1, 1, 5)


def test_fomc_event_times(calendar: MacroCalendar) -> None:
    out = fomc_events(calendar).set_index("event_id")
    assert tuple(out.reset_index().columns) == MACRO_EVENT_COLUMNS
    jan = out.loc["fomc-2024-01-31"]
    assert jan["event_time"].to_pydatetime() == utc(2024, 1, 31, 19)  # 14:00 EST
    assert jan["available_at"].to_pydatetime() == utc(2024, 1, 1, 5)
    assert (jan["event_type"], jan["source"]) == ("fomc", "fomc_calendar")
    assert pd.isna(jan["withdrawn_at"])
    jul = out.loc["fomc-2024-07-31"]
    assert jul["event_time"].to_pydatetime() == utc(2024, 7, 31, 18)  # 14:00 EDT
    sunday = out.loc["fomc-2020-03-15"]
    assert sunday["event_time"].to_pydatetime() == utc(2020, 3, 15, 21)
    assert sunday["available_at"] == sunday["event_time"]  # known only when announced
    cancelled = out.loc["fomc-2020-03-18"]
    assert cancelled["withdrawn_at"].to_pydatetime() == utc(2020, 3, 15, 21)
    assert cancelled["available_at"].to_pydatetime() == utc(2020, 1, 1, 5)
    assert len(out) == 82


# --- release events -----------------------------------------------------------------------------


def test_release_events_drop_revision_days(calendar: MacroCalendar) -> None:
    cpi = release_events(
        "cpi", calendar.releases["cpi"], _dates("release_dates_10.json"), _initial("CPIAUCSL")
    )
    days = set(cpi["event_date"])
    assert date(2021, 2, 8) not in days and date(2021, 2, 10) in days
    assert date(2024, 2, 9) not in days and date(2024, 2, 13) in days
    assert date(2015, 1, 16) in days  # December 2014 CPI
    assert date(2026, 11, 10) in days and date(2026, 12, 10) in days  # scheduled
    nfp = release_events(
        "nfp", calendar.releases["nfp"], _dates("release_dates_50.json"), _initial("PAYEMS")
    )
    days = set(nfp["event_date"])
    assert date(2024, 8, 21) not in days and date(2024, 8, 2) in days
    assert date(2020, 5, 11) not in days and date(2020, 5, 8) in days
    assert date(2024, 1, 10) not in days and date(2024, 1, 5) in days
    by_month = pd.Series([d.strftime("%Y-%m") for d in nfp["event_date"]]).value_counts()
    assert by_month.max() == 1


def test_release_event_time(calendar: MacroCalendar) -> None:
    cpi = release_events(
        "cpi", calendar.releases["cpi"], (date(2024, 1, 11),), (date(2024, 1, 11),)
    )
    row = cpi.iloc[0]
    assert row["event_id"] == "cpi-2024-01-11"
    assert row["event_time"].to_pydatetime() == et(2024, 1, 11, 8, 30)
    assert row["available_at"].to_pydatetime() == utc(2024, 1, 1, 5)
    assert (row["event_type"], row["source"]) == ("cpi", "fred_release")


def test_release_events_need_initial_dates(calendar: MacroCalendar) -> None:
    with pytest.raises(DataError, match="CPIAUCSL"):
        release_events("cpi", calendar.releases["cpi"], (date(2024, 1, 11),), ())


# --- FRED client endpoints ------------------------------------------------------------------------


def test_fred_release_dates_request() -> None:
    with respx.mock() as mock:
        route = mock.get(f"{API}/release/dates").mock(side_effect=serve_fred)
        with httpx.Client() as http:
            got = _client(http).release_dates(10, realtime_start=date(2015, 1, 1))
    params = route.calls[0].request.url.params
    assert params["release_id"] == "10" and params["realtime_start"] == "2015-01-01"
    assert params["include_release_dates_with_no_data"] == "true"
    assert params["sort_order"] == "asc"
    assert got == _dates("release_dates_10.json")
    assert got[0] == date(2015, 1, 16)


def test_fred_release_dates_pages() -> None:
    pages = [
        {"count": 3, "release_dates": [{"release_id": 10, "date": "2024-01-11"},
                                       {"release_id": 10, "date": "2024-02-13"}]},
        {"count": 3, "release_dates": [{"release_id": 10, "date": "2024-03-12"}]},
    ]  # fmt: skip
    with respx.mock() as mock:
        route = mock.get(f"{API}/release/dates").mock(
            side_effect=[httpx.Response(200, json=p) for p in pages]
        )
        with httpx.Client() as http:
            got = _client(http).release_dates(10, realtime_start=date(2024, 1, 1))
    assert [c.request.url.params["offset"] for c in route.calls] == ["0", "2"]
    assert got == (date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12))


def test_fred_initial_release_dates_request() -> None:
    with respx.mock() as mock:
        route = mock.get(f"{API}/series/observations").mock(side_effect=serve_fred)
        with httpx.Client() as http:
            got = _client(http).initial_release_dates(
                "CPIAUCSL", observation_start=date(2014, 1, 1)
            )
    params = route.calls[0].request.url.params
    assert params["output_type"] == "4" and params["series_id"] == "CPIAUCSL"
    assert params["observation_start"] == "2014-01-01"
    assert (params["realtime_start"], params["realtime_end"]) == ("1776-07-04", "9999-12-31")
    assert got == _initial("CPIAUCSL")
    assert date(2020, 4, 10) in got  # March 2020 CPI


@pytest.mark.parametrize(
    "body",
    [
        {"count": 1},
        {"release_dates": [{"date": "x"}], "count": 1},
        {"release_dates": [], "count": 2},
    ],
)
def test_fred_release_dates_bad_body(body: dict[str, Any]) -> None:
    with respx.mock() as mock:
        mock.get(f"{API}/release/dates").respond(json=body)
        with httpx.Client() as http, pytest.raises(DataError):
            _client(http).release_dates(10, realtime_start=date(2024, 1, 1))


def test_fred_calendar_key_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with respx.mock() as mock:
        mock.get(f"{API}/release/dates").respond(503)
        with httpx.Client() as http, pytest.raises(DataError) as info:
            _client(http).release_dates(10, realtime_start=date(2024, 1, 1))
    assert KEY not in str(info.value)
    assert KEY not in caplog.text


# --- ingestion ----------------------------------------------------------------------------------


def _ingest(lake: Lake, client: FredClient | None) -> int:
    return ingest_macro_calendar(lake, client)


def test_ingest_macro_calendar(tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    with respx.mock() as mock:
        mock.get(url__startswith=API).mock(side_effect=serve_fred)
        with httpx.Client() as http:
            rows = _ingest(tmp_lake, _client(http))
    stored = tmp_lake.read_table(MACRO_EVENTS.name)
    assert rows == len(stored)
    counts = stored["event_type"].value_counts().to_dict()
    assert counts["fomc"] == 82
    assert counts["cpi"] == 143 and counts["nfp"] == 143


def test_ingest_without_client_keeps_fomc_only(
    tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert _ingest(tmp_lake, None) == 82
    assert set(tmp_lake.read_table(MACRO_EVENTS.name)["source"]) == {"fomc_calendar"}


def test_reingest_replaces_rebuilt_sources(tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    moved = {"count": 1, "release_dates": [{"release_id": 10, "date": "2030-01-15"}]}
    with respx.mock() as mock:
        mock.get(url__startswith=API).mock(side_effect=serve_fred)
        with httpx.Client() as http:
            _ingest(tmp_lake, _client(http))
    before = tmp_lake.read_table(MACRO_EVENTS.name)
    assert _ingest(tmp_lake, None) == 82  # FOMC rebuilt; releases kept
    pd.testing.assert_frame_equal(tmp_lake.read_table(MACRO_EVENTS.name), before)
    with respx.mock() as mock:
        mock.get(f"{API}/release/dates").respond(json=moved)
        mock.get(f"{API}/series/observations").mock(side_effect=serve_fred)
        with httpx.Client() as http:
            _ingest(tmp_lake, _client(http))
    cpi = tmp_lake.read_table(MACRO_EVENTS.name, filters=[("event_type", "=", "cpi")])
    assert cpi["event_id"].tolist() == ["cpi-2030-01-15"]  # the old CPI rows are gone


def test_ingest_fred_failure_writes_nothing(
    tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    with respx.mock() as mock:
        mock.get(url__startswith=API).respond(400, json={"error_message": "Bad release"})
        with httpx.Client() as http, pytest.raises(DataError):
            _ingest(tmp_lake, _client(http))
    assert not tmp_lake.has_table(MACRO_EVENTS.name)
