"""Record the Stage 08 Wikipedia fixtures (run from the repo root; needs network).

Fetches two fixed article revisions by `oldid`, cuts out the constituents table
(`table#constituents`) and the change log (`table#changes`) verbatim, and writes each into
`tests/fixtures/universe/` inside a minimal HTML document with a provenance comment.

    uv run python Planning/stages/stage-08-universe/artifacts/record_wikipedia_fixtures.py
"""

import time
from pathlib import Path

import httpx
import lxml.html

USER_AGENT = "AtlasCapital/0.1 (research and paper-trading simulator)"
BASE = "https://en.wikipedia.org/w/index.php"
SOURCES = (
    ("List_of_S%26P_500_companies", 1382178819, "constituents", "wikipedia_sp500_constituents.html"),
    (
        "Historical_components_of_the_S%26P_500",
        1379179365,
        "changes",
        "wikipedia_sp500_changes.html",
    ),
)
OUT = Path("tests/fixtures/universe")


def fetch(url: str) -> httpx.Response:
    """One fresh request per page: a second request on a shared httpx.Client got 403 (robot
    policy) every time on 2026-10-27, while single requests were served."""
    for wait in (0, 10, 30, 60):  # Wikimedia throttles back-to-back requests (403)
        time.sleep(wait)
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        if resp.status_code != 403:
            break
    resp.raise_for_status()
    return resp


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for title, oldid, table_id, filename in SOURCES:
        url = f"{BASE}?title={title}&oldid={oldid}"
        if (OUT / filename).exists():
            print(f"{filename}: already recorded, kept")
            continue
        tree = lxml.html.fromstring(fetch(url).text)
        (table,) = tree.xpath(f'//table[@id="{table_id}"]')
        body = lxml.html.tostring(table, encoding="unicode")
        text = (
            "<!DOCTYPE html>\n"
            f"<!-- Source: {url} (table#{table_id}), Wikipedia, CC BY-SA 4.0. "
            "Recorded 2026-10-27 by record_wikipedia_fixtures.py; table cut verbatim. -->\n"
            '<html><head><meta charset="utf-8"></head><body>\n'
            f"{body}\n</body></html>\n"
        )
        (OUT / filename).write_text(text, encoding="utf-8")
        print(f"{filename}: {len(text)} chars from revision {oldid}")


if __name__ == "__main__":
    main()
