"""Record the Stage 10 macro fixtures from real FRED / ALFRED data (no API key needed).

The FRED API (api.stlouisfed.org) needs FRED_API_KEY, which is not set. So this script reads
the keyless public downloads and writes files in the documented FRED API JSON shapes:

* vintage dates: the vintage list of the ALFRED "Download Data" page
  (https://alfred.stlouisfed.org/series/downloaddata?seid=<ID>) → `fred/series/vintagedates`
  shape (`vintage_dates`), all vintages (realtime 1776-07-04 .. 9999-12-31);
* observations: one ALFRED CSV per vintage
  (https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=<ID>&vintage_date=<D>), i.e. the data
  as known on D, fetched at the request's `realtime_start` and at every vintage date inside the
  request's realtime window; consecutive vintages with the same value for an observation are
  one real-time period → `fred/series/observations` shape with `output_type=1` (Observations by
  Real-Time Period). Periods are clipped to the request window, as the API does; a missing
  value (blank in the CSV) is "." as in the API;
* series metadata: title and the "units, frequency, seasonal adjustment" line of the same
  ALFRED page → `fred/series` shape (only those fields).

Usage (from the repo root): `uv run python Planning/stages/stage-10-macro/artifacts/record_fred_fixtures.py`
"""

import csv
import html
import io
import json
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "tests" / "fixtures" / "macro"
FREQ = {"Daily": "D", "Weekly": "W", "Monthly": "M", "Quarterly": "Q", "Annual": "A"}
SA = {"SA": "Seasonally Adjusted", "NSA": "Not Seasonally Adjusted"}

REQUESTS = {
    # series: (observation_start, observation_end, realtime_start, realtime_end)
    "CPIAUCSL": (date(2018, 1, 1), date(2021, 6, 1), date(2019, 1, 1), date(2021, 12, 31)),
    "DGS10": (date(2020, 3, 2), date(2020, 4, 30), date(2020, 3, 1), date(2020, 5, 31)),
}


def get(client: httpx.Client, url: str) -> str:
    # The ALFRED servers drop requests with a custom User-Agent (HTTP/2 INTERNAL_ERROR / read
    # timeouts, checked 2026-10-29), so pages are fetched with plain curl and its default one.
    # `client` is unused; it is kept so the call sites stay unchanged.
    del client
    for attempt in range(4):
        time.sleep(0.7 * 2**attempt)  # be polite to the St. Louis Fed servers; back off on retry
        done = subprocess.run(
            ["curl", "-sS", "-f", "-m", "60", url], capture_output=True, text=True, check=False
        )
        if done.returncode == 0:
            return done.stdout
        print(f"retry {attempt + 1} after curl exit {done.returncode}: {url}", file=sys.stderr)
    raise SystemExit(f"giving up on {url}")


def page_info(client: httpx.Client, series_id: str) -> tuple[list[date], dict[str, str]]:
    text = get(client, f"https://alfred.stlouisfed.org/series/downloaddata?seid={series_id}")
    vintages = sorted({date.fromisoformat(v) for v in re.findall(r'<option[^>]*value="(\d{4}-\d{2}-\d{2})"', text)})
    title = html.unescape(re.search(r"<title>\s*Download Data for (.*?) \(" + series_id, text, re.S).group(1)).strip()
    desc = html.unescape(re.search(r'name="description" content="Download Data - Series: [^,]+, (.*?), First Vintage', text).group(1))
    units, frequency, sa = [p.strip() for p in desc.rsplit(",", 2)]
    meta = {
        "id": series_id,
        "title": title,
        "frequency": frequency,
        "frequency_short": FREQ[frequency],
        "units": units,
        "seasonal_adjustment": SA[sa],
        "seasonal_adjustment_short": sa,
    }
    return vintages, meta


def snapshot(client: httpx.Client, series_id: str, vintage: date, start: date, end: date) -> dict[date, str]:
    url = (
        "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
        f"?id={series_id}&vintage_date={vintage.isoformat()}&cosd={start.isoformat()}&coed={end.isoformat()}"
    )
    rows = list(csv.reader(io.StringIO(get(client, url))))
    want = f"{series_id}_{vintage:%Y%m%d}"
    if rows[0] != ["observation_date", want]:
        raise SystemExit(f"unexpected CSV header {rows[0]} for {url}")
    # The CSV may ignore cosd/coed (seen when vintage_date is not itself a vintage), so the
    # observation window is applied here as well.
    values = {date.fromisoformat(d): (v if v.strip() else ".") for d, v in rows[1:]}
    return {d: v for d, v in values.items() if start <= d <= end}


def observations(client: httpx.Client, series_id: str, vintages: list[date]) -> dict:
    obs_start, obs_end, rt_start, rt_end = REQUESTS[series_id]
    effective = [rt_start] + [v for v in vintages if rt_start < v <= rt_end]
    snaps = [snapshot(client, series_id, v, obs_start, obs_end) for v in effective]
    ends = [e - timedelta(days=1) for e in effective[1:]] + [rt_end]
    rows = []
    for obs in sorted({d for s in snaps for d in s}):
        k = 0
        while k < len(snaps):
            if obs not in snaps[k]:
                k += 1
                continue
            j = k
            while j + 1 < len(snaps) and snaps[j + 1].get(obs) == snaps[k][obs]:
                j += 1
            rows.append(
                {
                    "realtime_start": effective[k].isoformat(),
                    "realtime_end": ends[j].isoformat(),
                    "date": obs.isoformat(),
                    "value": snaps[k][obs],
                }
            )
            k = j + 1
    return {
        "realtime_start": rt_start.isoformat(),
        "realtime_end": rt_end.isoformat(),
        "observation_start": obs_start.isoformat(),
        "observation_end": obs_end.isoformat(),
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "asc",
        "count": len(rows),
        "offset": 0,
        "limit": 100000,
        "observations": rows,
    }, len(effective)


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n")


def main() -> int:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for series_id in REQUESTS:
            vintages, meta = page_info(client, series_id)
            write(OUT / "series" / f"{series_id}.json", {
                "realtime_start": "1776-07-04", "realtime_end": "9999-12-31",
                "seriess": [{**meta, "realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}],
            })
            write(OUT / "vintagedates" / f"{series_id}.json", {
                "realtime_start": "1776-07-04", "realtime_end": "9999-12-31",
                "order_by": "vintage_date", "sort_order": "asc", "count": len(vintages),
                "offset": 0, "limit": 10000, "vintage_dates": [v.isoformat() for v in vintages],
            })
            payload, fetched = observations(client, series_id, vintages)
            write(OUT / "observations" / f"{series_id}.json", payload)
            print(f"{series_id}: {meta['title']!r} {meta['units']!r} {meta['frequency_short']} "
                  f"{meta['seasonal_adjustment_short']}; vintages={len(vintages)} "
                  f"({vintages[0]} .. {vintages[-1]}); snapshots fetched={fetched}; "
                  f"observation rows={payload['count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
