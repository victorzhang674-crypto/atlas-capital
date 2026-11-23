"""Stage 12 Task 3: earnings dates from EDGAR filing timestamps (8-K item 2.02, 10-Q/10-K)."""

import json
from datetime import date, datetime
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
from events_helpers import FIXTURES, NoSleep, config_with, serve_sec, utc
from structlog.testing import capture_logs

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.events import EVENT_COLUMNS, EVENTS, EventsIngestReport
from atlas.data.events.earnings import (
    earnings_events,
    fetch_filings,
    ingest_earnings,
    parse_filings,
)
from atlas.data.fundamentals.edgar_client import CikMap, CikOverride, EdgarClient, FormerCik

CAL = TradingCalendar()
AAPL_CIK = 320193
AGENT = "Atlas tests test@example.com"

# AAPL 8-K item 2.02 acceptance dates (New York), 2018-01-01 .. 2026-10-29 (EDGAR, recorded).
AAPL_EARNINGS = [
    "2018-02-01", "2018-05-01", "2018-07-31", "2018-11-01", "2019-01-02", "2019-01-29",
    "2019-04-30", "2019-07-30", "2019-10-30", "2020-01-28", "2020-04-30", "2020-07-30",
    "2020-10-29", "2021-01-27", "2021-04-28", "2021-07-27", "2021-10-28", "2022-01-27",
    "2022-04-28", "2022-07-28", "2022-10-27", "2023-02-02", "2023-05-04", "2023-08-03",
    "2023-11-02", "2024-02-01", "2024-05-02", "2024-08-01", "2024-10-31", "2025-01-30",
    "2025-05-01", "2025-07-31", "2025-10-30", "2026-01-29", "2026-04-30", "2026-07-30",
]  # fmt: skip


def _recent() -> dict[str, Any]:
    subs = json.loads((FIXTURES / "submissions" / "CIK0000320193.json").read_text())
    return dict(subs["filings"]["recent"])


def _table(rows: list[tuple[str, str, str, str, str]]) -> dict[str, list[str]]:
    """A submissions column table from (accn, form, filed, accepted, items) rows."""
    return {
        "accessionNumber": [r[0] for r in rows],
        "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": ["" for _ in rows],
        "acceptanceDateTime": [r[3] for r in rows],
        "items": [r[4] for r in rows],
    }


def _client(http: httpx.Client) -> EdgarClient:
    return EdgarClient(
        AtlasConfig().data.fundamentals, user_agent=AGENT, http=http, sleep=NoSleep()
    )


def test_parse_filings_columns() -> None:
    out = parse_filings(_recent())
    assert list(out.columns) == ["accn", "form", "filed", "report_date", "accepted_at", "items"]
    assert str(out["accepted_at"].dtype) == "datetime64[us, UTC]"
    row = out.loc[out["accn"] == "0000320193-23-000075"].iloc[0]
    assert (row["form"], row["filed"], row["items"]) == ("8-K", date(2023, 8, 3), "2.02,9.01")
    assert row["report_date"] == date(2023, 8, 3)
    assert row["accepted_at"] == pd.Timestamp("2023-08-03T20:30:21Z")


def test_parse_filings_optional_columns_and_errors() -> None:
    table = _table([("a-1", "8-K", "2020-01-02", "2020-01-02T21:30:00.000Z", "2.02")])
    del table["items"], table["reportDate"]
    out = parse_filings(table)
    assert out["items"].tolist() == [""] and out["report_date"].tolist() == [None]
    ragged = _table([("a-1", "8-K", "2020-01-02", "2020-01-02T21:30:00.000Z", "2.02")])
    ragged["form"] = []
    with pytest.raises(DataError, match="differ in length"):
        parse_filings(ragged)
    with pytest.raises(DataError, match="lacks"):
        parse_filings({"form": ["8-K"]})


def test_acceptance_aapl_known_earnings_dates() -> None:
    events, skipped = earnings_events(parse_filings(_recent()), "AAPL", CAL, periodic_match_days=60)
    assert skipped == 0
    assert tuple(events.columns) == EVENT_COLUMNS
    assert [d.isoformat() for d in events["event_date"]] == AAPL_EARNINGS
    assert set(events["source"]) == {"sec_8k"}
    assert set(events["time_of_day"]) == {"AMC"}
    assert (events["event_time"] == events["available_at"]).all()
    late = events.loc[events["event_date"] == date(2020, 7, 30)].iloc[0]
    assert late["event_time"] == pd.Timestamp("2020-07-30T22:55:04Z")
    assert late["event_id"] == "sec-0000320193-20-000060"


def test_guidance_cut_8k_is_event() -> None:
    events, _ = earnings_events(parse_filings(_recent()), "AAPL", CAL, periodic_match_days=60)
    assert "sec-0000320193-19-000002" in set(events["event_id"])  # 2019-01-02 revenue warning


def test_non_earnings_8k_ignored() -> None:
    table = _table(
        [
            ("a-1", "8-K", "2021-02-24", "2021-02-24T21:31:21.000Z", "5.07"),
            ("a-2", "8-K", "2021-08-05", "2021-08-05T20:31:44.000Z", "8.01,9.01"),
            ("a-3", "8-K/A", "2021-08-06", "2021-08-06T20:31:44.000Z", "2.02"),
            ("a-4", "8-K", "2021-08-07", "2021-08-07T20:31:44.000Z", "12.02"),
        ]
    )
    events, _ = earnings_events(parse_filings(table), "AAPL", CAL, periodic_match_days=60)
    assert events.empty


def test_periodic_filing_is_event_only_without_8k() -> None:
    table = _table(
        [
            ("q-alone", "10-Q", "2021-05-05", "2021-05-05T20:05:00.000Z", ""),
            ("k-8", "8-K", "2021-07-27", "2021-07-27T20:30:00.000Z", "2.02,9.01"),
            ("q-next", "10-Q", "2021-07-28", "2021-07-28T22:00:00.000Z", ""),
            ("k-far", "10-K", "2021-09-27", "2021-09-27T12:00:00.000Z", ""),
        ]
    )
    events, _ = earnings_events(parse_filings(table), "XYZ", CAL, periodic_match_days=60)
    by_id = events.set_index("event_id")
    assert list(by_id.index) == ["sec-q-alone", "sec-k-8", "sec-k-far"]
    assert by_id.loc["sec-q-alone", "source"] == "sec_10q"
    assert by_id.loc["sec-k-far", "source"] == "sec_10k"  # 62 days after the 2.02
    assert by_id.loc["sec-k-far", "time_of_day"] == "BMO"  # 08:00 EDT


def test_filing_without_acceptance_skipped() -> None:
    table = _table(
        [
            ("a-1", "8-K", "2021-07-27", "", "2.02"),
            ("a-2", "8-K", "2021-10-28", "2021-10-28T20:30:00.000Z", "2.02"),
            ("a-3", "4", "2021-10-29", "", ""),
        ]
    )
    events, skipped = earnings_events(parse_filings(table), "AAPL", CAL, periodic_match_days=60)
    assert skipped == 1
    assert events["event_id"].tolist() == ["sec-a-2"]


def test_fetch_filings_reads_needed_pages_only() -> None:
    subs = {
        "filings": {
            "recent": _table([("r-1", "8-K", "2017-02-01", "2017-02-01T21:30:00.000Z", "2.02")]),
            "files": [
                {"name": "CIK0000000001-submissions-001.json", "filingFrom": "2010-01-01",
                 "filingTo": "2014-12-31"},
                {"name": "CIK0000000001-submissions-002.json", "filingFrom": "2015-01-01",
                 "filingTo": "2016-12-31"},
            ],
        }
    }  # fmt: skip
    page = _table(
        [
            ("p-1", "8-K", "2016-02-01", "2016-02-01T21:30:00.000Z", "2.02"),
            ("p-0", "8-K", "2014-12-30", "2014-12-30T21:30:00.000Z", "2.02"),
            ("r-1", "8-K", "2017-02-01", "2017-02-01T21:30:00.000Z", "2.02"),
        ]
    )
    with respx.mock(assert_all_called=False) as mock:
        main = mock.get("https://data.sec.gov/submissions/CIK0000000001.json").respond(json=subs)
        old = mock.get("https://data.sec.gov/submissions/CIK0000000001-submissions-001.json")
        new = mock.get(
            "https://data.sec.gov/submissions/CIK0000000001-submissions-002.json"
        ).respond(json=page)
        with httpx.Client() as http:
            out = fetch_filings(_client(http), 1, since=date(2015, 1, 1))
    assert main.called and new.called and not old.called
    assert sorted(out["accn"]) == ["p-1", "r-1"]  # filed before `since` dropped; one r-1


def _ingest(lake: Lake, symbols: Any, **events: Any) -> Any:
    cik_map = CikMap({"AAPL": AAPL_CIK})
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith="https://data.sec.gov/submissions/").mock(side_effect=serve_sec)
        with httpx.Client() as http:
            return ingest_earnings(
                lake, _client(http), symbols, cik_map=cik_map, config=config_with(**events)
            )


def test_ingest_earnings_writes_table(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, ["aapl"], history_start=date(2018, 1, 1))
    assert report.symbols_ok == ("AAPL",) and report.symbols_failed == ()
    assert report.rows == len(AAPL_EARNINGS) and report.skipped_filings == 0
    stored = tmp_lake.read_table(EVENTS.name)
    assert len(stored) == len(AAPL_EARNINGS)
    assert set(stored["symbol"]) == {"AAPL"}


def test_ingest_earnings_idempotent(tmp_lake: Lake) -> None:
    _ingest(tmp_lake, ["AAPL"], history_start=date(2018, 1, 1))
    first = tmp_lake.read_table(EVENTS.name)
    _ingest(tmp_lake, ["AAPL"], history_start=date(2018, 1, 1))
    pd.testing.assert_frame_equal(tmp_lake.read_table(EVENTS.name), first)


def test_ingest_reads_history_page_when_needed(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, ["AAPL"], history_start=date(2015, 1, 1))
    assert report.symbols_ok == ("AAPL",)  # page 001 (to 2015-10-01) served by Stage 09's fixture


def test_ingest_records_symbol_without_cik(tmp_lake: Lake) -> None:
    report = _ingest(tmp_lake, ["MSFT", "AAPL"], history_start=date(2018, 1, 1))
    assert report.symbols_ok == ("AAPL",)
    assert report.symbols_failed == ("MSFT",)
    assert "no CIK" in report.failure_reasons["MSFT"]


def test_ingest_records_edgar_failure(tmp_lake: Lake) -> None:
    cik_map = CikMap({"AAPL": AAPL_CIK})
    with respx.mock() as mock:
        mock.get(url__startswith="https://data.sec.gov/").respond(404)
        with httpx.Client() as http:
            report = ingest_earnings(tmp_lake, _client(http), ["AAPL"], cik_map=cik_map)
    assert report.symbols_failed == ("AAPL",) and report.rows == 0
    assert not tmp_lake.has_table(EVENTS.name)


def test_ingest_validates_request_first(tmp_lake: Lake) -> None:
    with respx.mock() as mock, httpx.Client() as http:
        with pytest.raises(ValueError, match="sequence"):
            ingest_earnings(tmp_lake, _client(http), "AAPL", cik_map=CikMap({}))
        with pytest.raises(ValueError):
            ingest_earnings(tmp_lake, _client(http), ["^GSPC"], cik_map=CikMap({}))
        assert not mock.calls


def test_event_time_is_acceptance_instant() -> None:
    events, _ = earnings_events(parse_filings(_recent()), "AAPL", CAL, periodic_match_days=60)
    first = events.iloc[0]
    assert first["event_time"].to_pydatetime() == utc(2018, 2, 1, 21, 30, 17)
    assert isinstance(first["event_time"].to_pydatetime(), datetime)


def test_ingest_reads_former_cik_events_until_its_last_day(tmp_lake: Lake) -> None:
    """Stage 29 follow-up 4: a symbol whose issuer moved to a new CIK gets the former CIK's
    earnings events filed up to `until` and the current CIK's filed from `since`. AAPL's
    recorded submissions play the former CIK; CIK 2 is the new issuer."""
    current = {
        "cik": "2",
        "filings": {
            "recent": _table(
                [
                    ("new-old", "8-K", "2021-10-28", "2021-10-28T20:30:00.000Z", "2.02"),
                    ("new-1", "8-K", "2022-01-27", "2022-01-27T21:30:00.000Z", "2.02,9.01"),
                ]
            ),
            "files": [],
        },
    }
    override = CikOverride(
        symbol="AAPL", cik=2, name="New Apple", evidence="e", since=date(2022, 1, 1),
        former=(FormerCik(cik=AAPL_CIK, until=date(2021, 12, 31), name="Apple"),),
    )  # fmt: skip
    cik_map = CikMap({}).with_overrides((override,))
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://data.sec.gov/submissions/CIK0000000002.json").respond(json=current)
        mock.get(url__startswith="https://data.sec.gov/submissions/").mock(side_effect=serve_sec)
        with httpx.Client() as http:
            report = ingest_earnings(
                tmp_lake, _client(http), ["AAPL"], cik_map=cik_map,
                config=config_with(history_start=date(2018, 1, 1)),
            )  # fmt: skip
    stored = tmp_lake.read_table(EVENTS.name)
    days = sorted(str(d) for d in stored["event_date"])
    assert days == [d for d in AAPL_EARNINGS if d <= "2021-12-31"] + ["2022-01-27"]
    assert report.rows == len(stored) and "sec-new-old" not in set(stored["event_id"])


def test_ingest_reads_each_filing_once_and_archived_answers_with_refresh_false(
    tmp_lake: Lake,
) -> None:
    """A filing listed under two CIKs of the symbol (co-registrants) gives one event, and with
    `refresh=False` the archived submissions of every CIK are read without a request."""
    override = CikOverride(
        symbol="AAPL", cik=AAPL_CIK, name="Apple", evidence="e",
        former=(FormerCik(cik=2, until=date(2099, 1, 1), name="Apple co-registrant"),),
    )  # fmt: skip
    cik_map = CikMap({}).with_overrides((override,))
    twin = (FIXTURES / "submissions" / "CIK0000320193.json").read_bytes()
    with respx.mock(assert_all_called=False) as mock:
        routes = (
            mock.get("https://data.sec.gov/submissions/CIK0000000002.json").respond(content=twin),
            mock.get(url__startswith="https://data.sec.gov/submissions/").mock(
                side_effect=serve_sec
            ),
        )
        with httpx.Client() as http:
            client = EdgarClient(
                AtlasConfig().data.fundamentals, user_agent=AGENT, http=http, sleep=NoSleep(),
                lake=tmp_lake,
            )  # fmt: skip
            cfg = config_with(history_start=date(2018, 1, 1))
            first = ingest_earnings(tmp_lake, client, ["AAPL"], cik_map=cik_map, config=cfg)
            calls = sum(r.call_count for r in routes)
            again = ingest_earnings(
                tmp_lake, client, ["AAPL"], cik_map=cik_map, config=cfg, refresh=False
            )
            cached = sum(r.call_count for r in routes)
            ingest_earnings(tmp_lake, client, ["AAPL"], cik_map=cik_map, config=cfg)
            refreshed = sum(r.call_count for r in routes) - cached
    assert first.rows == again.rows == len(AAPL_EARNINGS)
    assert calls >= 2 and cached == calls and refreshed == calls


def test_an_edgar_failure_does_not_stop_the_next_symbol(tmp_lake: Lake) -> None:
    cik_map = CikMap({"BAD": 999, "AAPL": AAPL_CIK})
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith="https://data.sec.gov/submissions/").mock(side_effect=serve_sec)
        with httpx.Client() as http:
            report = ingest_earnings(
                tmp_lake, _client(http), ["BAD", "AAPL"], cik_map=cik_map,
                config=config_with(history_start=date(2018, 1, 1)),
            )  # fmt: skip
    assert report.symbols_failed == ("BAD",) and report.symbols_ok == ("AAPL",)
    assert report.rows == len(AAPL_EARNINGS)


# --- Stage 29 follow-up 5: the old lines mutation testing left untested ------------------------


def _twins(
    lake: Lake, subs: dict[str, Any] | None = None, **kw: Any
) -> tuple[EventsIngestReport, list[dict[str, Any]]]:
    """`ingest_earnings` of two tickers of AAPL's CIK (both get every event), with `subs` (AAPL's
    submissions JSON) served instead of the recorded file when given; the captured logs."""
    cik_map = CikMap({"AAPL": AAPL_CIK, "AAPLX": AAPL_CIK})
    with respx.mock(assert_all_called=False) as mock, capture_logs() as logs:
        if subs is not None:
            mock.get("https://data.sec.gov/submissions/CIK0000320193.json").respond(json=subs)
        mock.get(url__startswith="https://data.sec.gov/submissions/").mock(side_effect=serve_sec)
        with httpx.Client() as http:
            kw.setdefault("config", config_with(history_start=date(2018, 1, 1)))
            report = ingest_earnings(lake, _client(http), ["AAPL", "AAPLX"], cik_map=cik_map, **kw)
    return report, logs


def _unaccepted(n: int) -> dict[str, Any]:
    """AAPL's recorded submissions with the acceptance time of the first `n` 8-K item 2.02
    filings blank."""
    subs = json.loads((FIXTURES / "submissions" / "CIK0000320193.json").read_text())
    recent = subs["filings"]["recent"]
    blanked = 0
    for i, (form, items) in enumerate(zip(recent["form"], recent["items"], strict=True)):
        if blanked < n and form == "8-K" and "2.02" in items:
            recent["acceptanceDateTime"][i] = ""
            blanked += 1
    return subs


def test_rows_and_skipped_filings_add_up_over_symbols_and_are_logged(tmp_lake: Lake) -> None:
    subs = _unaccepted(2)
    _, alone = earnings_events(
        parse_filings(subs["filings"]["recent"]), "AAPL", CAL, periodic_match_days=60
    )
    assert alone > 0
    report, logs = _twins(tmp_lake, subs)
    assert report.symbols_ok == ("AAPL", "AAPLX")
    stored = tmp_lake.read_table(EVENTS.name)
    per = stored.groupby("symbol").size().to_dict()
    assert per["AAPL"] == per["AAPLX"] > 0 and report.rows == per["AAPL"] * 2
    assert report.skipped_filings == 2 * alone
    done = [e for e in logs if e["event"] == "events.ingest_earnings"]
    assert done == [
        {
            "event": "events.ingest_earnings",
            "rows": report.rows,
            "symbols_ok": 2,
            "symbols_failed": 0,
            "skipped_filings": report.skipped_filings,
            "log_level": "info",
        }
    ]


def test_failure_reasons_are_exact_and_logged(tmp_lake: Lake) -> None:
    cik_map = CikMap({"AAPL": AAPL_CIK})
    with respx.mock() as mock, capture_logs() as logs:
        mock.get(url__startswith="https://data.sec.gov/").respond(404)
        with httpx.Client() as http:
            report = ingest_earnings(
                tmp_lake, _client(http), ["MSFT", "AAPL"], cik_map=cik_map,
                config=config_with(history_start=date(2018, 1, 1)),
            )  # fmt: skip
    reasons = dict(report.failure_reasons)
    assert reasons["MSFT"] == "no CIK (not in company_tickers.json or config/cik_overrides.yaml)"
    assert isinstance(reasons["AAPL"], str) and "HTTP 404" in reasons["AAPL"]
    failed = [e for e in logs if e["event"] == "events.symbol_failed"]
    assert failed == [
        {
            "event": "events.symbol_failed",
            "symbol": "AAPL",
            "error": reasons["AAPL"],
            "log_level": "warning",
        }
    ]


def test_a_given_calendar_or_the_config_s_calendar_dates_the_events(tmp_lake: Lake) -> None:
    """The calendar given, else the config's (bounds ending in 2016 put the filings since 2018
    outside it), places each event in its session."""
    with pytest.raises(ValueError, match="outside the calendar"):
        _twins(tmp_lake, calendar=TradingCalendar(date(2000, 1, 1), date(2016, 1, 1)))
    bounded = config_with(history_start=date(2018, 1, 1)).model_copy(
        update={
            "calendar": AtlasConfig.model_validate({"calendar": {"end": "2016-01-01"}}).calendar
        }
    )
    with pytest.raises(ValueError, match="outside the calendar"):
        _twins(tmp_lake, config=bounded)
