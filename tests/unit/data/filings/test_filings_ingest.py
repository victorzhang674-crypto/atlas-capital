"""Stage 29 follow-up 2 (F1, F3): the filing-text settings, the EDGAR client's filing
documents, the filing list of a submissions table and `ingest_filing_text` (offline, with a
mocked EDGAR)."""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
from pydantic import ValidationError
from structlog.testing import capture_logs

from atlas.core.calendar import TradingCalendar
from atlas.core.config import (
    AtlasConfig,
    DataConfig,
    FilingTextConfig,
    FundamentalsConfig,
    RedFlagsConfig,
    SyncChunks,
    load_config,
)
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.filings.mda import MdaExtract, extract_exhibit_mda, extract_mda, html_to_text
from atlas.data.filings.text import (
    FILING_TEXT,
    LIST_COLUMNS,
    FilingTextIngestReport,
    _unless_later,
    filing_list,
    ingest_filing_text,
    parse_filing_list,
)
from atlas.data.fundamentals import CikMap, EdgarClient

REPO_ROOT = Path(__file__).resolve().parents[4]
UA = "Atlas test suite tests@example.invalid"
BODY = "Revenue grew because demand was strong. " * 40
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


# --- settings -----------------------------------------------------------------------------------


def test_filing_text_defaults() -> None:
    cfg = AtlasConfig()
    assert cfg.data.filing_text == FilingTextConfig()
    assert cfg.data.filing_text.forms == ("10-K", "10-Q")
    assert cfg.data.filing_text.since == date(2015, 1, 1)
    assert cfg.data.filing_text.min_mda_chars == 1000
    assert cfg.data.filing_text.short_mda_chars == 15000
    assert cfg.data.sync.chunk_size.filing_text == 10
    assert cfg.agents.fundamental.red_flags.mda_max_age_days == 460
    f = cfg.data.fundamentals
    assert f.filing_index_url == f"{ARCHIVE}/{{cik}}/{{folder}}/{{accn}}-index.htm"
    assert f.filing_document_url == f"{ARCHIVE}/{{cik}}/{{folder}}/{{name}}"


def test_fund_yaml_matches_the_defaults() -> None:
    loaded = load_config("dev", config_dir=REPO_ROOT / "config")
    assert loaded.data.filing_text == FilingTextConfig()
    assert loaded.data.fundamentals == FundamentalsConfig()
    assert loaded.data.sync.chunk_size == SyncChunks()
    assert loaded.agents.fundamental.red_flags == RedFlagsConfig()


@pytest.mark.parametrize(
    "bad",
    [
        {"forms": []},
        {"forms": [""]},
        {"min_mda_chars": 0},
        {"short_mda_chars": 0},
        {"unknown_key": 1},
    ],
)
def test_bad_filing_text_settings_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FilingTextConfig.model_validate(bad)


@pytest.mark.parametrize(
    ("key", "url"),
    [
        ("filing_index_url", f"{ARCHIVE}/{{cik}}/{{accn}}-index.htm"),
        ("filing_index_url", f"{ARCHIVE}/{{cik}}/{{folder}}/index.htm"),
        ("filing_document_url", f"{ARCHIVE}/{{cik}}/{{folder}}/doc.htm"),
        ("filing_document_url", f"{ARCHIVE}/{{folder}}/{{name}}"),
    ],
)
def test_filing_urls_need_their_placeholders(key: str, url: str) -> None:
    with pytest.raises(ValidationError, match=key):
        FundamentalsConfig.model_validate({key: url})


def test_mda_max_age_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RedFlagsConfig(mda_max_age_days=0)
    assert DataConfig().filing_text.forms == ("10-K", "10-Q")


# --- a mocked EDGAR -----------------------------------------------------------------------------

CIK = 815097


def subs_table(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    keys = ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "reportDate")
    out: dict[str, list[str]] = {k: [r.get(k, "") for r in rows] for k in keys}
    out["primaryDocument"] = [r.get("primaryDocument", "") for r in rows]
    return out


def ten_k_html(item7: str) -> bytes:
    return (
        "<html><body><p>Item 1. Business</p><p>We sail.</p>"
        "<p>Item 7. Management\u2019s Discussion and Analysis</p>"
        f"<p>{item7}</p><p>Item 8. Financial Statements</p><p>Numbers.</p></body></html>"
    ).encode()


TEN_Q = (
    "<html><body><p>PART I</p><p>Item 2. Management\u2019s Discussion and Analysis</p>"
    f"<p>{BODY}</p><p>Item 4. Controls and Procedures</p><p>Effective.</p></body></html>"
).encode()
POINTER = (
    "The information required by Item 7 is shown in Exhibit 13 and is incorporated by reference."
)
EX13 = (
    "<html><body><p>MANAGEMENT\u2019S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION</p>"
    f"<p>{BODY}</p><p>REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM</p>"
    "<p>Opinion.</p></body></html>"
).encode()
INDEX = (
    b'<html><body><table class="tableFile"><tr><th>Seq</th><th>Description</th>'
    b"<th>Document</th><th>Type</th><th>Size</th></tr>"
    b'<tr><td>1</td><td>10-K</td><td><a href="/ix?doc=/Archives/edgar/data/815097/'
    b'000081509721000027/ccl-20201130.htm">ccl-20201130.htm</a> iXBRL</td><td>10-K</td>'
    b"<td>3817608</td></tr>"
    b'<tr><td>8</td><td>EX-13</td><td><a href="/Archives/edgar/data/815097/000081509721000027/'
    b'a2020ex-13.htm">a2020ex-13.htm</a></td><td>EX-13</td><td>1875301</td></tr>'
    b"</table></body></html>"
)

RECENT = [
    {  # a 10-K with its MD&A in Item 7
        "accessionNumber": "0000815097-24-000011",
        "form": "10-K",
        "filingDate": "2024-01-26",
        "acceptanceDateTime": "2024-01-26T16:15:20.000Z",
        "reportDate": "2023-11-30",
        "primaryDocument": "ccl-20231130.htm",
    },
    {  # a 10-Q: read by default (follow-up 3), not by the 10-K-only helper below
        "accessionNumber": "0000815097-23-000090",
        "form": "10-Q",
        "filingDate": "2023-09-29",
        "acceptanceDateTime": "2023-09-29T10:00:00.000Z",
        "reportDate": "2023-08-31",
        "primaryDocument": "ccl-20230831.htm",
    },
    {  # Item 7 points to Exhibit 13; no acceptance time
        "accessionNumber": "0000815097-21-000027",
        "form": "10-K",
        "filingDate": "2021-01-26",
        "acceptanceDateTime": "",
        "reportDate": "2020-11-30",
        "primaryDocument": "ccl-20201130.htm",
    },
]
OLD_PAGE = [
    {  # on a history page, before `since`: not read
        "accessionNumber": "0000815097-14-000001",
        "form": "10-K",
        "filingDate": "2014-01-28",
        "acceptanceDateTime": "2014-01-28T09:00:00.000Z",
        "reportDate": "2013-11-30",
        "primaryDocument": "ccl-2013.htm",
    },
]


def submissions(recent: list[dict[str, str]], pages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "cik": str(CIK),
        "filings": {
            "recent": subs_table(recent),
            "files": [{"name": f"CIK{CIK:010d}-submissions-001.json", **p} for p in pages],
        },
    }


class Edgar:
    """A respx router answering the mocked EDGAR; `docs` maps an archive path to its body."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.subs = submissions(RECENT, [{"filingFrom": "2010-01-01", "filingTo": "2014-12-31"}])
        self.page = subs_table(OLD_PAGE)
        self.docs: dict[str, bytes] = {
            f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm": ten_k_html(BODY),
            f"/Archives/edgar/data/{CIK}/000081509721000027/ccl-20201130.htm": ten_k_html(POINTER),
            f"/Archives/edgar/data/{CIK}/000081509721000027/0000815097-21-000027-index.htm": INDEX,
            f"/Archives/edgar/data/{CIK}/000081509721000027/a2020ex-13.htm": EX13,
            f"/Archives/edgar/data/{CIK}/000081509723000090/ccl-20230831.htm": TEN_Q,
        }
        self.seen: list[str] = []
        router.route(host="data.sec.gov").mock(side_effect=self.data)
        router.route(host="www.sec.gov").mock(side_effect=self.www)

    def data(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.url.path)
        if request.url.path == f"/submissions/CIK{CIK:010d}.json":
            return httpx.Response(200, json=self.subs)
        if request.url.path.endswith("-submissions-001.json"):
            return httpx.Response(200, json=self.page)
        return httpx.Response(404)

    def www(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.url.path)
        assert request.headers["User-Agent"] == UA
        body = self.docs.get(request.url.path)
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)


@pytest.fixture
def edgar() -> Iterator[Edgar]:
    with respx.mock(assert_all_called=False) as router:
        yield Edgar(router)


def client(lake: Lake | None = None) -> EdgarClient:
    return EdgarClient(FundamentalsConfig(), user_agent=UA, lake=lake, sleep=lambda s: None)


CIKS = CikMap({"CCL": CIK, "AAPL": 320193})


TEN_K_ONLY = AtlasConfig.model_validate({"data": {"filing_text": {"forms": ["10-K"]}}})
"""The tests written for 10-Ks (before follow-up 3 added 10-Qs to the default forms)."""


def ingest(lake: Lake, symbols: list[str], **kw: Any) -> FilingTextIngestReport:
    kw.setdefault("config", TEN_K_ONLY)
    return ingest_filing_text(lake, client(lake), symbols, cik_map=CIKS, **kw)


# --- client -------------------------------------------------------------------------------------


def test_filing_documents_lists_type_and_file_name(edgar: Edgar) -> None:
    docs = client().filing_documents(CIK, "0000815097-21-000027")
    assert docs == (("10-K", "ccl-20201130.htm"), ("EX-13", "a2020ex-13.htm"))


def test_filing_document_returns_the_bytes_and_a_missing_one_is_a_data_error(edgar: Edgar) -> None:
    assert client().filing_document(CIK, "0000815097-21-000027", "a2020ex-13.htm") == EX13
    with pytest.raises(DataError, match="HTTP 404"):
        client().filing_document(CIK, "0000815097-21-000027", "gone.htm")


def test_a_file_name_with_a_path_is_refused(edgar: Edgar) -> None:
    with pytest.raises(DataError, match="invalid filing document name"):
        client().filing_document(CIK, "0000815097-21-000027", "../x.htm")
    assert edgar.seen == []


# --- parse_filing_list --------------------------------------------------------------------------


def test_parse_filing_list() -> None:
    frame = parse_filing_list(subs_table(RECENT))
    assert list(frame.columns) == [
        "accn",
        "form",
        "filed",
        "accepted_at",
        "period_end",
        "primary_document",
    ]
    assert frame["period_end"].tolist() == [
        date(2023, 11, 30),
        date(2023, 8, 31),
        date(2020, 11, 30),
    ]
    assert frame["primary_document"].iloc[0] == "ccl-20231130.htm"
    assert pd.isna(frame["accepted_at"].iloc[2])


def test_parse_filing_list_blank_report_date_and_bad_tables() -> None:
    table = subs_table([{**RECENT[0], "reportDate": ""}])
    assert parse_filing_list(table)["period_end"].tolist() == [None]
    with pytest.raises(DataError, match="primaryDocument"):
        parse_filing_list({k: v for k, v in subs_table(RECENT).items() if k != "primaryDocument"})
    ragged = subs_table(RECENT)
    ragged["primaryDocument"] = ragged["primaryDocument"][:1]
    with pytest.raises(DataError, match="differ in length"):
        parse_filing_list(ragged)
    with pytest.raises(DataError, match="reportDate"):
        parse_filing_list(subs_table([{**RECENT[0], "reportDate": "not a date"}]))


# --- ingest_filing_text -------------------------------------------------------------------------


def test_ingest_stores_each_10k_mda_with_its_point_in_time_and_source(
    tmp_path: Path, edgar: Edgar
) -> None:
    lake = Lake(tmp_path)
    report = ingest(lake, ["CCL"])
    assert report == FilingTextIngestReport(
        rows=2,
        symbols_ok=("CCL",),
        symbols_failed=(),
        failure_reasons=report.failure_reasons,
        statuses=report.statuses,
    )
    assert dict(report.failure_reasons) == {}
    assert dict(report.statuses) == {"ok": 2}
    rows = lake.read_table(FILING_TEXT.name).sort_values("filed").reset_index(drop=True)
    assert list(rows.columns) == [c.name for c in FILING_TEXT.columns]
    old, new = rows.iloc[0], rows.iloc[1]
    assert (new["symbol"], new["cik"], new["accn"], new["form"]) == (
        "CCL",
        CIK,
        "0000815097-24-000011",
        "10-K",
    )
    assert (new["period_end"], new["filed"]) == (date(2023, 11, 30), date(2024, 1, 26))
    assert new["accepted_at"] == pd.Timestamp("2024-01-26T16:15:20", tz="UTC")
    assert new["available_at"] == new["accepted_at"]
    assert (new["section"], new["source"], new["document"], new["status"]) == (
        "mda",
        "primary",
        "ccl-20231130.htm",
        "ok",
    )
    assert new["url"] == f"{ARCHIVE}/{CIK}/000081509724000011/ccl-20231130.htm"
    assert new["sha256"] == hashlib.sha256(ten_k_html(BODY)).hexdigest()
    assert (
        new["text"].startswith("Item 7. Management\u2019s Discussion")
        and BODY.strip() in new["text"]
    )
    assert new["chars"] == len(new["text"])
    # Item 7 pointed to Exhibit 13: the MD&A comes from the exhibit
    assert (old["source"], old["document"], old["status"]) == ("EX-13", "a2020ex-13.htm", "ok")
    assert old["sha256"] == hashlib.sha256(EX13).hexdigest()
    assert old["text"].startswith("MANAGEMENT\u2019S DISCUSSION AND ANALYSIS")
    # no acceptance time: the open of the first session after the filing date
    cal = TradingCalendar()
    assert pd.isna(old["accepted_at"])
    assert old["available_at"] == pd.Timestamp(
        cal.session_open(cal.next_session(date(2021, 1, 26)))
    )
    # neither the 10-Q nor the 10-K filed before `since` was fetched
    assert not any("20230831" in p or "ccl-2013" in p for p in edgar.seen)


def test_a_rerun_fetches_no_document_again(tmp_path: Path, edgar: Edgar) -> None:
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    edgar.seen.clear()
    again = ingest(lake, ["CCL"])
    assert (again.rows, again.symbols_ok, dict(again.statuses)) == (0, ("CCL",), {})
    assert [p for p in edgar.seen if p.startswith("/Archives/")] == []
    assert len(lake.read_table(FILING_TEXT.name)) == 2


def test_a_new_filing_is_added_on_the_next_run(tmp_path: Path, edgar: Edgar) -> None:
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    newer = {
        **RECENT[0],
        "accessionNumber": "0000815097-25-000007",
        "filingDate": "2025-01-27",
        "acceptanceDateTime": "2025-01-27T16:00:00.000Z",
        "reportDate": "2024-11-30",
        "primaryDocument": "ccl-20241130.htm",
    }
    edgar.subs = submissions([newer, *RECENT], [])
    edgar.docs[f"/Archives/edgar/data/{CIK}/000081509725000007/ccl-20241130.htm"] = ten_k_html(BODY)
    report = ingest(lake, ["CCL"])
    assert report.rows == 1
    assert len(lake.read_table(FILING_TEXT.name)) == 3


def test_refresh_false_reuses_the_archived_submissions(tmp_path: Path, edgar: Edgar) -> None:
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    edgar.seen.clear()
    ingest(lake, ["CCL"], refresh=False)
    assert not any(p.startswith("/submissions/") for p in edgar.seen)


def test_statuses_are_stored_when_no_mda_is_found(tmp_path: Path, edgar: Edgar) -> None:
    edgar.docs[f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"] = ten_k_html(
        "Not applicable."
    )
    edgar.docs[f"/Archives/edgar/data/{CIK}/000081509721000027/a2020ex-13.htm"] = b"<p>Nothing.</p>"
    lake = Lake(tmp_path)
    report = ingest(lake, ["CCL"])
    assert dict(report.statuses) == {"not_found": 1, "by_reference": 1}
    rows = lake.read_table(FILING_TEXT.name).set_index("accn")
    assert tuple(rows.loc["0000815097-24-000011", ["status", "source"]]) == ("not_found", "primary")
    gone = rows.loc["0000815097-21-000027"]
    assert (gone["status"], gone["source"], gone["document"]) == (
        "by_reference",
        "primary",
        "ccl-20201130.htm",
    )
    assert pd.isna(gone["text"]) and gone["chars"] == 0


def test_failures_are_per_symbol_and_never_stop_the_batch(tmp_path: Path, edgar: Edgar) -> None:
    del edgar.docs[f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"]
    lake = Lake(tmp_path)
    report = ingest(lake, ["NOPE", "CCL", "AAPL"])
    assert report.symbols_ok == ()
    assert report.symbols_failed == ("NOPE", "CCL", "AAPL")
    reasons = dict(report.failure_reasons)
    assert reasons["NOPE"].startswith("no CIK")
    assert "HTTP 404" in reasons["CCL"]
    assert "HTTP 404" in reasons["AAPL"]  # no submissions for AAPL in the mock
    assert not lake.has_table(FILING_TEXT.name)


def test_a_symbol_without_10ks_since_is_ok_and_empty(tmp_path: Path, edgar: Edgar) -> None:
    edgar.subs = submissions([RECENT[1]], [])
    report = ingest(Lake(tmp_path), ["CCL"])
    assert (report.rows, report.symbols_ok, report.symbols_failed) == (0, (), ("CCL",))
    assert dict(report.failure_reasons) == {"CCL": "no 10-K filed since 2015-01-01"}


def test_forms_and_since_come_from_the_config(tmp_path: Path, edgar: Edgar) -> None:
    cfg = AtlasConfig.model_validate(
        {"data": {"filing_text": {"forms": ["10-K", "10-Q"], "since": "2023-01-01"}}}
    )
    edgar.docs[f"/Archives/edgar/data/{CIK}/000081509723000090/ccl-20230831.htm"] = ten_k_html(BODY)
    lake = Lake(tmp_path)
    report = ingest(lake, ["CCL"], config=cfg)
    assert report.rows == 2
    assert set(lake.read_table(FILING_TEXT.name)["form"]) == {"10-K", "10-Q"}


def test_the_request_is_checked_before_any_download(tmp_path: Path, edgar: Edgar) -> None:
    with pytest.raises(ValueError, match="not one string"):
        ingest(Lake(tmp_path), "CCL")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ingest(Lake(tmp_path), ["not a ticker!"])
    assert edgar.seen == []


def test_symbols_are_normalised_and_deduplicated(tmp_path: Path, edgar: Edgar) -> None:
    report = ingest(Lake(tmp_path), ["ccl", "CCL"])
    assert report.symbols_ok == ("CCL",)


def test_the_table_schema() -> None:
    assert FILING_TEXT.name == "filing_text"
    assert FILING_TEXT.primary_key == ("symbol", "accn", "section")
    assert FILING_TEXT.partition_by == ("symbol",)
    assert json.dumps([c.name for c in FILING_TEXT.columns]) == json.dumps(
        [
            "symbol",
            "cik",
            "accn",
            "form",
            "period_end",
            "filed",
            "accepted_at",
            "available_at",
            "section",
            "source",
            "document",
            "url",
            "sha256",
            "status",
            "chars",
            "text",
        ]
    )


# --- second pass: what mutation testing showed untested ------------------------------------------


def test_a_history_page_reaching_since_is_read_and_duplicates_kept_once(
    tmp_path: Path, edgar: Edgar
) -> None:
    """A page whose range ends on `since` is read (inclusive), a filing on `since` is kept,
    and a filing listed twice (recent and a page) is read once; columns and order are fixed."""
    on_since = {
        **OLD_PAGE[0],
        "accessionNumber": "0000815097-15-000002",
        "filingDate": "2015-01-01",
    }
    edgar.subs = submissions(RECENT, [{"filingFrom": "2010-01-01", "filingTo": "2015-01-01"}])
    edgar.page = subs_table([*OLD_PAGE, on_since, RECENT[0]])
    frame = filing_list(client(), CIK, forms=("10-K",), since=date(2015, 1, 1), refresh=True)
    assert list(frame.columns) == list(LIST_COLUMNS)
    assert list(frame.index) == [0, 1, 2]
    assert frame["accn"].tolist() == [
        "0000815097-15-000002",
        "0000815097-21-000027",
        "0000815097-24-000011",
    ]
    assert any(p.endswith("-submissions-001.json") for p in edgar.seen)


def test_filing_list_errors_are_exact(edgar: Edgar) -> None:
    edgar.subs = {"cik": str(CIK), "filings": {}}
    with pytest.raises(DataError, match=rf"^submissions of CIK {CIK}: no filings.recent table$"):
        filing_list(client(), CIK, forms=("10-K",), since=date(2015, 1, 1), refresh=True)
    edgar.subs = {"cik": str(CIK)}
    with pytest.raises(DataError, match=r"no filings\.recent table"):
        filing_list(client(), CIK, forms=("10-K",), since=date(2015, 1, 1), refresh=True)
    edgar.subs = submissions(RECENT, [{"filingTo": "not a date"}])
    with pytest.raises(DataError, match=rf"^submissions of CIK {CIK}: bad history entry "):
        filing_list(client(), CIK, forms=("10-K",), since=date(2015, 1, 1), refresh=True)
    with pytest.raises(DataError, match=r"^submissions table lacks reportDate or primaryDocument$"):
        parse_filing_list({k: v for k, v in subs_table(RECENT).items() if k != "reportDate"})


def test_two_tickers_of_one_issuer_each_get_their_rows(tmp_path: Path, edgar: Edgar) -> None:
    """GOOG and GOOGL (here CCL and CUK) share a CIK and its filings: what one stored does not
    count as stored for the other, and the report adds both."""
    lake = Lake(tmp_path)
    report = ingest_filing_text(
        lake, client(lake), ["CCL", "CUK"], cik_map=CikMap({"CCL": CIK, "CUK": CIK})
    )
    assert (report.rows, report.symbols_ok, dict(report.statuses)) == (6, ("CCL", "CUK"), {"ok": 6})
    stored = lake.read_table(FILING_TEXT.name)
    assert sorted(stored.groupby("symbol").size().items()) == [("CCL", 3), ("CUK", 3)]


def test_a_symbol_without_filings_does_not_stop_the_next(tmp_path: Path, edgar: Edgar) -> None:
    only_10q = submissions([RECENT[1]], [])
    original = edgar.data

    def data(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/submissions/CIK0000320193.json":
            return httpx.Response(200, json=only_10q)
        return original(request)

    edgar.router.route(host="data.sec.gov").mock(side_effect=data)
    cfg = AtlasConfig.model_validate({"data": {"filing_text": {"forms": ["10-K", "10-K405"]}}})
    report = ingest(Lake(tmp_path), ["AAPL", "CCL"], config=cfg)
    assert report.symbols_ok == ("CCL",)
    assert dict(report.failure_reasons) == {"AAPL": "no 10-K/10-K405 filed since 2015-01-01"}


def test_a_given_calendar_dates_filings_without_an_acceptance_time(
    tmp_path: Path, edgar: Edgar
) -> None:
    class Calendar:
        def next_session(self, day: date) -> date:
            return date(2030, 1, 2)

        def session_open(self, day: date) -> datetime:
            return datetime(2030, 1, 2, 14, 30, tzinfo=UTC)

    lake = Lake(tmp_path)
    ingest(lake, ["CCL"], calendar=Calendar())
    rows = lake.read_table(FILING_TEXT.name).set_index("accn")
    assert rows.loc["0000815097-21-000027", "available_at"] == pd.Timestamp("2030-01-02 14:30Z")


def test_the_log_events(tmp_path: Path, edgar: Edgar) -> None:
    del edgar.docs[f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"]
    with capture_logs() as logs:
        ingest(Lake(tmp_path), ["CCL"])
    failed = [e for e in logs if e["event"] == "filing_text.symbol_failed"]
    assert len(failed) == 1 and failed[0]["symbol"] == "CCL" and "HTTP 404" in failed[0]["error"]
    done = [e for e in logs if e["event"] == "filing_text.ingest"]
    assert len(done) == 1
    assert {k: done[0][k] for k in ("rows", "symbols_ok", "symbols_failed", "statuses")} == {
        "rows": 0,
        "symbols_ok": 0,
        "symbols_failed": 1,
        "statuses": {},
    }


def test_submissions_without_history_pages_and_a_bare_string(tmp_path: Path, edgar: Edgar) -> None:
    edgar.subs = {"cik": str(CIK), "filings": {"recent": subs_table(RECENT)}}
    frame = filing_list(client(), CIK, forms=("10-K",), since=date(2015, 1, 1), refresh=True)
    assert len(frame) == 2
    with pytest.raises(
        ValueError, match=r"^symbols must be a sequence of tickers, not one string$"
    ):
        ingest(Lake(tmp_path), "CCL")  # type: ignore[arg-type]


def test_a_10k_without_item_7_headings_falls_back_to_its_mda_heading(
    tmp_path: Path, edgar: Edgar
) -> None:
    """Large filers lay out the 10-K as an annual report with a cross-reference index and no
    "Item 7" heading (JPMorgan, Bank of America, GE): the MD&A heading is used instead."""
    annual_report = (
        "<html><body><p>Form 10-K Cross-Reference Index</p><p>Item 7 ... page 40</p>"
        "<p>Management\u2019s discussion and analysis</p>"
        f"<p>{BODY}</p><p>Consolidated balance sheets</p><p>Assets.</p></body></html>"
    ).encode()
    edgar.docs[f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"] = annual_report
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-24-000011"]
    assert (row["status"], row["source"]) == ("ok", "primary")
    assert row["text"].startswith("Management\u2019s discussion and analysis")
    assert "Assets." not in row["text"]


# --- follow-up 3: 10-Qs by default, EX-99 annual reports, an MD&A later in the 10-K --------------


def test_10qs_are_read_by_default_with_their_part_i_item_2(tmp_path: Path, edgar: Edgar) -> None:
    lake = Lake(tmp_path)
    report = ingest_filing_text(lake, client(lake), ["CCL"], cik_map=CIKS)
    assert (report.rows, dict(report.statuses)) == (3, {"ok": 3})
    q = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-23-000090"]
    assert (q["form"], q["source"], q["period_end"]) == ("10-Q", "primary", date(2023, 8, 31))
    assert (
        q["text"].startswith("Item 2. Management\u2019s Discussion")
        and "Effective." not in q["text"]
    )


def test_an_annual_report_filed_as_exhibit_99_is_read(tmp_path: Path, edgar: Edgar) -> None:
    """Clorox files its annual report, with the MD&A, as Exhibit 99.1."""
    folder = f"/Archives/edgar/data/{CIK}/000081509721000027"
    edgar.docs[f"{folder}/0000815097-21-000027-index.htm"] = INDEX.replace(b"EX-13", b"EX-99.1")
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-21-000027"]
    assert (row["status"], row["source"], row["document"]) == ("ok", "EX-99", "a2020ex-13.htm")


def test_an_mda_later_in_the_10k_is_read_when_no_exhibit_has_one(
    tmp_path: Path, edgar: Edgar
) -> None:
    """AEP, Mosaic, Time Warner, Windstream: Item 7 points to an annual-report section later
    in the same document, not to an exhibit."""
    folder = f"/Archives/edgar/data/{CIK}/000081509721000027"
    edgar.docs[f"{folder}/ccl-20201130.htm"] = (
        "<html><body><p>Item 7. Management\u2019s Discussion and Analysis</p>"
        f"<p>{POINTER}</p><p>Item 8. Financial Statements</p><p>See the annual report.</p>"
        "<p>Management\u2019s Discussion and Analysis of Results of Operations</p>"
        f"<p>{BODY}</p><p>Report of Independent Registered Public Accounting Firm</p>"
        "<p>Opinion.</p></body></html>"
    ).encode()
    edgar.docs[f"{folder}/a2020ex-13.htm"] = b"<p>Nothing here.</p>"
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-21-000027"]
    assert (row["status"], row["source"], row["document"]) == ("ok", "primary", "ccl-20201130.htm")
    assert row["text"].startswith("Management\u2019s Discussion and Analysis of Results")
    assert "Opinion." not in row["text"]


# --- follow-up 3 (H4 review): a short MD&A pointing later in the document -----------------------

COMBINED = (
    "<p>Combined Management\u2019s Discussion and Analysis of Financial Condition</p>"
    f"<p>{BODY * 3}</p><p>Report of Independent Registered Public Accounting Firm</p>"
    "<p>Opinion.</p>"
)


def test_a_short_mda_gives_way_to_a_longer_one_later_in_the_document(
    tmp_path: Path, edgar: Edgar
) -> None:
    """Southern Company's Item 7 is a page of pointers to the combined MD&A later in the 10-K:
    an MD&A shorter than `short_mda_chars` is compared with the longest section under a later
    MD&A heading (not an "Item" line), and the longer one is kept."""
    pointers = "See the Combined MD&A of each registrant on page 50. " * 25  # ~1,300 characters
    doc = f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"
    edgar.docs[doc] = ten_k_html(pointers).replace(b"</body>", COMBINED.encode() + b"</body>")
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-24-000011"]
    assert (row["status"], row["source"]) == ("ok", "primary")
    assert row["text"].startswith("Combined Management\u2019s Discussion")
    assert "Opinion." not in row["text"]
    # a pointer at least `short_mda_chars` long is kept as the MD&A
    keep = AtlasConfig.model_validate(
        {"data": {"filing_text": {"forms": ["10-K"], "short_mda_chars": 1000}}}
    )
    lake2 = Lake(tmp_path / "keep")
    ingest(lake2, ["CCL"], config=keep)
    kept = lake2.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-24-000011"]
    assert kept["text"].startswith("Item 7. Management\u2019s Discussion")


def test_a_short_mda_with_no_longer_one_later_is_kept(tmp_path: Path, edgar: Edgar) -> None:
    doc = f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"
    short_later = (
        "<p>Management\u2019s Discussion and Analysis (continued)</p><p>A short note here.</p>"
        "<p>Report of Independent Registered Public Accounting Firm</p>"
    )
    edgar.docs[doc] = ten_k_html(BODY).replace(b"</body>", short_later.encode() + b"</body>")
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-24-000011"]
    assert row["text"].startswith("Item 7. Management\u2019s Discussion")


def test_a_pointer_found_by_the_heading_fallback_gives_way_too(
    tmp_path: Path, edgar: Edgar
) -> None:
    """Entergy's Item 7 says only "Refer to MANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS":
    the Item 7 section is too short, the heading fallback first takes the "Item 7" line, and
    the registrant's MD&A later in the 10-K is the longer section."""
    doc = f"/Archives/edgar/data/{CIK}/000081509724000011/ccl-20231130.htm"
    edgar.docs[doc] = (
        "<html><body><p>Item 7. Management\u2019s Discussion and Analysis</p>"
        "<p>Refer to the financial discussion of each registrant.</p>"
        f"<p>Item 8. Financial Statements</p><p>{'Refer to the statements. ' * 50}</p>"
        "<p>Consolidated Balance Sheets</p><p>Assets.</p>"
        "<p>ENTERGY CORPORATION AND SUBSIDIARIES</p>"
        f"<p>MANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS</p><p>{BODY * 2}</p>"
        "<p>CONSOLIDATED STATEMENTS OF INCOME</p><p>Revenue.</p></body></html>"
    ).encode()
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-24-000011"]
    assert (row["status"], row["source"]) == ("ok", "primary")
    assert row["text"].startswith("MANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS")
    assert "Revenue." not in row["text"]


def test_a_short_mda_pointing_to_the_annual_report_reads_its_exhibit(
    tmp_path: Path, edgar: Edgar
) -> None:
    """Total System Services' Item 7 is a page "incorporated by reference" from the annual
    report: a short MD&A that points elsewhere gives way to the exhibit's MD&A."""
    folder = f"/Archives/edgar/data/{CIK}/000081509721000027"
    edgar.docs[f"{folder}/ccl-20201130.htm"] = ten_k_html(f"{POINTER} " * 12)  # ~1,100 chars
    lake = Lake(tmp_path)
    ingest(lake, ["CCL"])
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-21-000027"]
    assert (row["status"], row["source"], row["document"]) == ("ok", "EX-13", "a2020ex-13.htm")
    # with no exhibit MD&A, the pointer is kept
    edgar.docs[f"{folder}/a2020ex-13.htm"] = b"<p>Nothing here.</p>"
    lake2 = Lake(tmp_path / "none")
    ingest(lake2, ["CCL"])
    kept = lake2.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-21-000027"]
    assert (kept["status"], kept["source"]) == ("ok", "primary")
    assert kept["text"].startswith("Item 7. Management\u2019s Discussion")


def test_the_short_bound_and_ties_keep_what_was_found() -> None:
    """An MD&A of exactly `short_mda_chars` is not short; a later section only as long as it
    does not replace it."""
    text = (
        f"Management\u2019s Discussion and Analysis (combined)\n{BODY}\nConsolidated Balance Sheets"
    )
    later = extract_exhibit_mda(text, min_chars=1, skip_items=True).text
    assert later is not None
    settings = FilingTextConfig(short_mda_chars=10)
    assert _unless_later(MdaExtract("ok", "x" * 10), text, settings).text == "x" * 10
    assert _unless_later(MdaExtract("ok", "x" * 9), text, settings).text == later
    tie = FilingTextConfig(short_mda_chars=len(later) + 1)
    assert _unless_later(MdaExtract("ok", "y" * len(later)), text, tie).text == "y" * len(later)
    assert _unless_later(MdaExtract("ok", "y" * (len(later) - 1)), text, tie).text == later


def test_a_pointer_at_the_short_bound_or_tied_with_the_exhibit_is_kept(
    tmp_path: Path, edgar: Edgar
) -> None:
    folder = f"/Archives/edgar/data/{CIK}/000081509721000027"
    primary = ten_k_html(f"{POINTER} " * 12)
    edgar.docs[f"{folder}/ccl-20201130.htm"] = primary
    pointer = extract_mda(html_to_text(primary), min_chars=1000).text
    assert pointer is not None
    # exactly `short_mda_chars` long: not short, so no exhibit is read
    at_bound = AtlasConfig.model_validate(
        {"data": {"filing_text": {"forms": ["10-K"], "short_mda_chars": len(pointer)}}}
    )
    lake = Lake(tmp_path / "bound")
    ingest(lake, ["CCL"], config=at_bound)
    row = lake.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-21-000027"]
    assert (row["source"], row["text"]) == ("primary", pointer)
    # an exhibit MD&A exactly as long as the pointer does not replace it
    heading = "MANAGEMENT\u2019S DISCUSSION AND ANALYSIS"
    filler = "x" * (len(pointer) - len(heading) - 1)
    edgar.docs[f"{folder}/a2020ex-13.htm"] = (
        f"<p>{heading}</p><p>{filler}</p><p>CONSOLIDATED BALANCE SHEETS</p>".encode()
    )
    lake2 = Lake(tmp_path / "tie")
    ingest(lake2, ["CCL"])
    tied = lake2.read_table(FILING_TEXT.name).set_index("accn").loc["0000815097-21-000027"]
    assert (tied["source"], tied["text"]) == ("primary", pointer)
