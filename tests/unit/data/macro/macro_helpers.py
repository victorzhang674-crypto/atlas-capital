"""Helpers shared by the Stage 10 macro tests: recorded FRED fixtures, a fixture server and a
small fake FRED that answers real-time windows from an in-memory vintage table."""

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from atlas.core.config import MacroConfig
from atlas.core.storage import Lake
from atlas.data.macro import (
    OBS_COLUMNS,
    FredClient,
    MacroSeries,
    attach_available_at,
    collapse_vintages,
    parse_observations,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "macro"
REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_KEY = "testkey0123456789abcdef0123456789"
API = "https://api.stlouisfed.org/fred"

CPI = MacroSeries(id="CPIAUCSL", kind="release", release_time=time(8, 30), description="CPI")
DGS10 = MacroSeries(id="DGS10", kind="market", description="10-year Treasury yield")

CPI_REQUEST = {
    "observation_start": date(2018, 1, 1),
    "observation_end": date(2021, 6, 1),
    "realtime_start": date(2019, 1, 1),
    "realtime_end": date(2021, 12, 31),
}
"""The request the recorded CPIAUCSL observations answer."""

DGS10_REQUEST = {
    "observation_start": date(2020, 3, 2),
    "observation_end": date(2020, 4, 30),
    "realtime_start": date(2020, 3, 1),
    "realtime_end": date(2020, 5, 31),
}
"""The request the recorded DGS10 observations answer."""


def fixture_json(relative: str) -> Any:
    """A recorded fixture (path relative to `tests/fixtures/macro`)."""
    return json.loads((FIXTURES / relative).read_text())


def _error(message: str) -> httpx.Response:
    return httpx.Response(400, json={"error_code": 400, "error_message": message})


def serve_fixture(request: httpx.Request) -> httpx.Response:
    """respx side effect emulating the FRED API from the recorded fixtures.

    Checks `api_key` and `file_type=json`. `series` returns the recorded metadata;
    `series/vintagedates` filters the recorded vintages to the requested real-time window and
    pages them by `limit`/`offset`; `series/observations` answers only the recorded request
    (same four dates, `output_type=1`, else HTTP 400) and pages by `limit`/`offset`.
    """
    params = request.url.params
    if params.get("api_key") != TEST_KEY:
        return _error("Bad Request.  Variable api_key is not set.")
    if params.get("file_type") != "json":
        return _error("Bad Request.  file_type must be json.")
    endpoint = request.url.path.removeprefix("/fred/")
    sid = params.get("series_id", "")
    folder = {"series": "series", "series/vintagedates": "vintagedates"}.get(
        endpoint, "observations"
    )
    file = FIXTURES / folder / f"{sid}.json"
    if not file.exists():
        return _error("Bad Request.  The series does not exist.")
    data = json.loads(file.read_text())
    offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100_000))
    if endpoint == "series":
        return httpx.Response(200, json=data)
    if endpoint == "series/vintagedates":
        low = params.get("realtime_start", "1776-07-04")
        high = params.get("realtime_end", "9999-12-31")
        dates = [d for d in data["vintage_dates"] if low <= d <= high]
        page = dates[offset : offset + limit]
        body = {**data, "realtime_start": low, "realtime_end": high, "count": len(dates)}
        return httpx.Response(
            200, json={**body, "offset": offset, "limit": limit, "vintage_dates": page}
        )
    if endpoint != "series/observations":
        return httpx.Response(404)
    for key in ("observation_start", "observation_end", "realtime_start", "realtime_end"):
        if params.get(key) != data[key]:
            return _error(f"Bad Request.  The fixture answers {key}={data[key]} only.")
    if params.get("output_type") != "1":
        return _error("Bad Request.  The fixture answers output_type=1 only.")
    rows = data["observations"]
    return httpx.Response(
        200,
        json={
            **data,
            "offset": offset,
            "limit": limit,
            "observations": rows[offset : offset + limit],
        },
    )


class SleepRecorder:
    """A fake `sleep` that records the requested pauses (seconds)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def fred_client(
    lake: Lake | None = None,
    *,
    config: MacroConfig | None = None,
    sleep: SleepRecorder | None = None,
) -> FredClient:
    """A `FredClient` with the test key and a recording fake sleep."""
    return FredClient(
        config if config is not None else MacroConfig(),
        api_key=TEST_KEY,
        lake=lake,
        sleep=sleep if sleep is not None else SleepRecorder(),
    )


class FakeFred:
    """A fake FRED API over an in-memory vintage table, for windowing tests.

    `vintages` maps a vintage date to the observations known from that date on
    (`{obs_date: value}`, `None` for "."). `vintagedates` lists those dates in the requested
    window; `observations` answers `output_type=1` like FRED: one row per observation and
    real-time period of constant value, periods clipped to the requested window, and it
    refuses (HTTP 400) a request whose window covers more than `max_vintages` vintages
    (counting the vintage in effect at the window start). Every request is recorded.
    """

    def __init__(
        self,
        series_id: str,
        vintages: Mapping[date, Mapping[date, float | None]],
        *,
        max_vintages: int,
    ) -> None:
        self.series_id = series_id
        self.vintages = dict(sorted(vintages.items()))
        self.max_vintages = max_vintages
        self.requests: list[httpx.QueryParams] = []

    def _in_effect(self, low: date, high: date) -> list[date]:
        before = [v for v in self.vintages if v <= low]
        inside = [v for v in self.vintages if low < v <= high]
        return before[-1:] + inside

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        self.requests.append(params)
        endpoint = request.url.path.removeprefix("/fred/")
        low = date.fromisoformat(params.get("realtime_start", "1776-07-04"))
        high = date.fromisoformat(params.get("realtime_end", "9999-12-31"))
        if endpoint == "series":
            meta = {
                "id": self.series_id,
                "title": "Fake",
                "frequency_short": "D",
                "units": "Percent",
                "seasonal_adjustment_short": "NSA",
            }
            return httpx.Response(200, json={"seriess": [meta]})
        if endpoint == "series/vintagedates":
            dates = [v.isoformat() for v in self.vintages if low <= v <= high]
            return httpx.Response(
                200, json={"count": len(dates), "offset": 0, "vintage_dates": dates}
            )
        effective = self._in_effect(low, high)
        if len(effective) > self.max_vintages:
            return _error(f"Bad Request.  {len(effective)} vintage dates exceed the maximum.")
        starts = [max(v, low) for v in effective]
        ends = [s - timedelta(days=1) for s in starts[1:]] + [high]
        snaps = [self.vintages[v] for v in effective]
        rows = []
        for obs in sorted({d for s in snaps for d in s}):
            k = 0
            while k < len(snaps):
                if obs not in snaps[k]:
                    k += 1
                    continue
                j = k
                while (
                    j + 1 < len(snaps)
                    and obs in snaps[j + 1]
                    and snaps[j + 1][obs] == snaps[k][obs]
                ):
                    j += 1
                value = snaps[k][obs]
                rows.append(
                    {
                        "realtime_start": starts[k].isoformat(),
                        "realtime_end": ends[j].isoformat(),
                        "date": obs.isoformat(),
                        "value": "." if value is None else str(value),
                    }
                )
                k = j + 1
        return httpx.Response(200, json={"count": len(rows), "offset": 0, "observations": rows})


def window_requests(fake: FakeFred) -> Sequence[tuple[str, str]]:
    """The real-time windows of the observations requests `fake` received, in order."""
    return [(p["realtime_start"], p["realtime_end"]) for p in fake.requests if "output_type" in p]


def stored_rows(series: MacroSeries, config: MacroConfig | None = None) -> pd.DataFrame:
    """The recorded observations of `series` as `macro_observations` rows (as ingest stores
    them). A fresh frame."""
    payload = fixture_json(f"observations/{series.id}.json")
    rows = collapse_vintages(parse_observations(payload, series.id))
    return attach_available_at(rows, series, config if config is not None else MacroConfig())


def make_rows(*specs: tuple[str, date, float | None, date, datetime]) -> pd.DataFrame:
    """Synthetic `macro_observations` rows from `(series_id, obs_date, value, realtime_start,
    available_at)` tuples (`None` value = missing)."""
    frame = pd.DataFrame(
        [(s, o, math.nan if v is None else float(v), r, a) for s, o, v, r, a in specs],
        columns=list(OBS_COLUMNS),
    )
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    return frame
