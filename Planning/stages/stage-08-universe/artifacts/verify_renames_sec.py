"""Check the `manual` ticker-rename dates in config/ticker_changes.yaml against SEC filings.

For each entry: EDGAR full-text search (efts.sec.gov) for 8-K filings (then any form) that
mention both tickers within +-45 days of the configured date, then fetch each hit's document
from sec.gov and print the sentences that name the old and the new ticker together with
"ticker"/"symbol".
With arguments, only those old tickers are checked.
Read-only; one request at a time (SEC fair-access limit is 10/s). Run from the repo root:

    uv run python Planning/stages/stage-08-universe/artifacts/verify_renames_sec.py
"""

import html
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import httpx

from atlas.data.reference import load_ticker_map

USER_AGENT = "AtlasCapital/0.1 (research and paper-trading simulator)"
SEARCH = "https://efts.sec.gov/LATEST/search-index"
TAG_RE = re.compile(r"<[^>]+>")
SENTENCE_RE = re.compile(r"[^.]*\.")


def get(url: str, **params: str) -> httpx.Response:
    for wait in (0.3, 2, 5, 10):
        time.sleep(wait)
        try:
            resp = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
        except httpx.TransportError:  # transient TLS/connection errors were seen
            continue
        if resp.status_code < 500:
            break
    resp.raise_for_status()
    return resp


def mentions(sentence: str, ticker: str) -> bool:
    return re.search(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])", sentence) is not None


def main() -> None:
    tmap = load_ticker_map(Path("config/ticker_changes.yaml"))
    only = set(sys.argv[1:])  # optional: old tickers to check, e.g. WLP UA
    for change in [c for c in tmap.changes if c.source == "manual"]:
        if only and change.old not in only:
            continue
        lo, hi = change.effective - timedelta(days=45), change.effective + timedelta(days=45)
        print(f"\n=== {change.old} -> {change.new}  configured effective {change.effective}")
        hits: list[dict] = []
        # Query shapes that EDGAR answers (two quoted tickers plus a date range gave HTTP 500).
        for query, forms in (
            (f'"{change.new}" symbol', "8-K"),
            (f'"ticker symbol" {change.new}', "8-K"),
            (f'"ticker symbol" {change.new}', None),
            (f'"{change.new}" ticker', None),
        ):
            params = {
                "q": query,
                "dateRange": "custom",
                "startdt": lo.isoformat(),
                "enddt": hi.isoformat(),
            }
            if forms:
                params["forms"] = forms
            try:
                hits = get(SEARCH, **params).json()["hits"]["hits"]
            except httpx.HTTPStatusError as exc:
                print(f"  search {query!r} forms={forms}: HTTP {exc.response.status_code}")
                hits = []
            if hits:
                break
        shown = 0
        for hit in hits[:15]:
            src = hit["_source"]
            adsh, doc = hit["_id"].split(":", 1)
            cik = src["ciks"][0].lstrip("0")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{doc}"
            text = html.unescape(TAG_RE.sub(" ", get(url).text))
            text = re.sub(r"\s+", " ", text)
            # A window of text around each mention of the new ticker near "ticker"/"symbol"
            # (sentence splitting broke on abbreviations such as "Dec.").
            found = []
            for m in re.finditer(rf"(?<![A-Za-z]){re.escape(change.new)}(?![A-Za-z])", text):
                window = text[max(0, m.start() - 260) : m.end() + 160]
                if re.search(r"ticker|symbol", window, re.I) and window not in found:
                    found.append(window)
            if found:
                names = src.get("display_names") or ["?"]
                print(f"  {src['form']} filed {src['file_date']} {adsh} ({names[0]})")
                for w in found[:2]:
                    print(f"    > ...{w}...")
                shown += 1
            if shown >= 3:
                break
        if shown == 0:
            print("  no matching sentence found in the first hits")


if __name__ == "__main__":
    main()
