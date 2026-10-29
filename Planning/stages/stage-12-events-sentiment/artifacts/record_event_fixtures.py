"""Record the Stage 12 event fixtures from the real sources (run once, 2026-10-29).

Writes under tests/fixtures/events/:
- submissions/CIK0000320193.json: AAPL EDGAR submissions, `filings.recent` trimmed to the
  8-K / 10-Q / 10-K filings filed on or after 2018-01-01 (every column kept); `filings.files`
  unchanged. Needs SEC_USER_AGENT.
- fred/release_dates_10.json, fred/release_dates_50.json: `fred/release/dates` (CPI,
  Employment Situation) from realtime_start 2015-01-01 with scheduled dates. Needs FRED_API_KEY.
- fred/initial_CPIAUCSL.json, fred/initial_PAYEMS.json: `fred/series/observations` with
  `output_type=4` (initial release only) from 2014-01-01 (a year before the release dates).
- yahoo_calendar.json: yfinance `Ticker(s).calendar` for AAPL, MSFT, JPM (dates as ISO text).

Secrets come from the environment (source the project .env first); they are never written or
printed. Usage: `uv run python Planning/stages/stage-12-events-sentiment/artifacts/record_event_fixtures.py`
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "tests" / "fixtures" / "events"
FORMS = {"8-K", "10-Q", "10-K"}
SINCE = "2018-01-01"


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


def record_submissions(client: httpx.Client) -> None:
    agent = os.environ["SEC_USER_AGENT"]
    resp = client.get(
        "https://data.sec.gov/submissions/CIK0000320193.json", headers={"User-Agent": agent}
    )
    resp.raise_for_status()
    subs = resp.json()
    recent = subs["filings"]["recent"]
    keep = [
        i
        for i, (form, filed) in enumerate(zip(recent["form"], recent["filingDate"], strict=True))
        if form in FORMS and filed >= SINCE
    ]
    subs["filings"]["recent"] = {k: [v[i] for i in keep] for k, v in recent.items()}
    print(f"AAPL: kept {len(keep)} of {len(recent['form'])} recent filings")
    _write(OUT / "submissions" / "CIK0000320193.json", subs)


def record_fred(client: httpx.Client) -> None:
    key = os.environ["FRED_API_KEY"]
    base = "https://api.stlouisfed.org/fred"
    for release_id in (10, 50):
        params = {
            "release_id": str(release_id),
            "realtime_start": "2015-01-01",
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "limit": "10000",
            "offset": "0",
            "file_type": "json",
        }
        resp = client.get(f"{base}/release/dates", params={**params, "api_key": key})
        resp.raise_for_status()
        data = resp.json()
        print(f"release {release_id}: {data['count']} dates")
        _write(OUT / "fred" / f"release_dates_{release_id}.json", data)
    for series in ("CPIAUCSL", "PAYEMS"):
        params = {
            "series_id": series,
            "observation_start": "2014-01-01",
            "realtime_start": "1776-07-04",
            "realtime_end": "9999-12-31",
            "output_type": "4",
            "limit": "100000",
            "offset": "0",
            "file_type": "json",
        }
        resp = client.get(f"{base}/series/observations", params={**params, "api_key": key})
        resp.raise_for_status()
        data = resp.json()
        print(f"{series}: {data['count']} initial releases")
        _write(OUT / "fred" / f"initial_{series}.json", data)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, float | int | str) or value is None:
        return value
    return str(value)


def record_yahoo() -> None:
    import yfinance as yf

    answers = {}
    for symbol in ("AAPL", "MSFT", "JPM"):
        cal = yf.Ticker(symbol).calendar
        answers[symbol] = {k: _jsonable(v) for k, v in cal.items()}
        print(f"{symbol}: Earnings Date {answers[symbol].get('Earnings Date')}")
    _write(OUT / "yahoo_calendar.json", answers)


def main() -> int:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        record_submissions(client)
        record_fred(client)
    record_yahoo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
