"""Helpers shared by the Stage 09 fundamentals tests: recorded EDGAR fixtures and fakes."""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import FundamentalsConfig
from atlas.core.storage import Lake
from atlas.data.fundamentals import (
    FACT_COLUMNS,
    ConceptMap,
    EdgarClient,
    attach_available_at,
    load_concept_map,
    parse_companyfacts,
    parse_submissions,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "fundamentals"
AAPL, MSFT, JPM = 320193, 789019, 19617
TEST_USER_AGENT = "Atlas test suite tests@example.invalid"
REPO_ROOT = Path(__file__).resolve().parents[4]


def fixture_json(relative: str) -> Any:
    """A recorded fixture file (path relative to `tests/fixtures/fundamentals`)."""
    return json.loads((FIXTURES / relative).read_text())


def companyfacts(cik: int) -> dict[str, Any]:
    """The recorded (trimmed) companyfacts JSON of `cik`."""
    data: dict[str, Any] = fixture_json(f"companyfacts/CIK{cik:010d}.json")
    return data


def serve_fixture(request: httpx.Request) -> httpx.Response:
    """respx side effect: answer an EDGAR URL from the recorded fixtures (404 if absent)."""
    path = request.url.path
    if path == "/files/company_tickers.json":
        file = FIXTURES / "company_tickers.json"
    elif path.startswith("/api/xbrl/companyfacts/"):
        file = FIXTURES / "companyfacts" / path.rsplit("/", 1)[-1]
    elif path.startswith("/submissions/"):
        file = FIXTURES / "submissions" / path.rsplit("/", 1)[-1]
    else:
        return httpx.Response(404)
    if not file.exists():
        return httpx.Response(404)
    return httpx.Response(200, content=file.read_bytes())


class SleepRecorder:
    """A fake `sleep` that records the requested pauses (seconds)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def edgar_client(
    lake: Lake | None = None,
    *,
    config: FundamentalsConfig | None = None,
    sleep: SleepRecorder | None = None,
) -> EdgarClient:
    """An `EdgarClient` with the test User-Agent and a recording fake sleep."""
    return EdgarClient(
        config if config is not None else FundamentalsConfig(),
        user_agent=TEST_USER_AGENT,
        lake=lake,
        sleep=sleep if sleep is not None else SleepRecorder(),
    )


@cache
def repo_concept_map() -> ConceptMap:
    """The repo's `config/xbrl_map.yaml`."""
    return load_concept_map(REPO_ROOT / "config" / "xbrl_map.yaml")


@cache
def _fixture_facts(cik: int, symbol: str) -> pd.DataFrame:
    tables = [fixture_json(f"submissions/CIK{cik:010d}.json")["filings"]["recent"]]
    tables += [
        json.loads(p.read_text())
        for p in sorted((FIXTURES / "submissions").glob(f"CIK{cik:010d}-submissions-*.json"))
    ]
    index = pd.concat([parse_submissions(t) for t in tables], ignore_index=True)
    raw = parse_companyfacts(companyfacts(cik), symbol=symbol, tags=repo_concept_map().tags())
    return attach_available_at(raw, index, TradingCalendar())


def fixture_facts(cik: int, symbol: str) -> pd.DataFrame:
    """The recorded facts of `cik` as `fundamentals_facts` rows (acceptance times from the
    recorded filing index), as `ingest_fundamentals` would store them. A fresh copy."""
    return _fixture_facts(cik, symbol).copy()


_ACCN = iter(range(1, 1_000_000))


def make_facts(*specs: Mapping[str, Any]) -> pd.DataFrame:
    """Synthetic `fundamentals_facts` rows. Each spec needs `tag`, `value`, `end` (date) and
    `filed` (date); optional `start` (date; omitted = instant), `available_at` (aware datetime;
    default 21:00 UTC on `filed`), `unit` ("USD"), `taxonomy` ("us-gaap"), `accn` (unique),
    `form` ("10-Q")."""
    rows = []
    for spec in specs:
        start: date | None = spec.get("start")
        end: date = spec["end"]
        filed: date = spec["filed"]
        available = spec.get("available_at", datetime.combine(filed, time(21), tzinfo=UTC))
        rows.append(
            {
                "symbol": "TEST",
                "cik": 1,
                "taxonomy": spec.get("taxonomy", "us-gaap"),
                "tag": spec["tag"],
                "unit": spec.get("unit", "USD"),
                "value": float(spec["value"]),
                "period_start": start,
                "period_end": end,
                "duration_days": 0 if start is None else (end - start).days + 1,
                "fy": None,
                "fp": None,
                "form": spec.get("form", "10-Q"),
                "filed": filed,
                "accn": spec.get("accn", f"0000000001-00-{next(_ACCN):06d}"),
                "accepted_at": available,
                "available_at": available,
            }
        )
    df = pd.DataFrame(rows, columns=list(FACT_COLUMNS))
    df["fy"] = df["fy"].astype("Int64")
    for col in ("accepted_at", "available_at"):
        df[col] = pd.to_datetime(df[col], utc=True).astype("datetime64[us, UTC]")
    return df
