"""Stage 31 real-lake check (read-only): the news tools' prefetch on the main lake for several
symbols and days, with the item counts per kind, the coverage texts and the time taken.

    uv run python Planning/stages/stage-31-news-analyst/artifacts/main_lake_check.py
"""

import time
from collections import Counter
from datetime import date
from pathlib import Path

from atlas.agents.tools.news_tools import company_article_count, prefetch
from atlas.core.config import load_config
from atlas.core.errors import AtlasError
from atlas.core.storage import Lake
from atlas.data.view import DataView

ROOT = Path(__file__).resolve().parents[4]
LAKE = Path.home() / "Projects" / "atlas-capital" / "data"
CASES = [
    ("AAPL", date(2024, 5, 1), "the recorded fixture's day (earnings tomorrow)"),
    ("AAPL", date(2020, 3, 16), "a crash day"),
    ("AAPL", date(2015, 1, 5), "the first days of the news history"),
    ("AAPL", date(2025, 5, 1), "a later year (a scheduled Yahoo date may exist)"),
    ("AAPL", date(2026, 11, 24), "the lake's last session"),
    ("KVUE", date(2023, 6, 1), "a young listing"),
    ("SMCI", date(2024, 3, 1), "a volatile mid-cap"),
    ("SIVB", date(2023, 3, 9), "a failing bank (a former member)"),
    ("GEHC", date(2023, 1, 5), "before the listing"),
    ("MSFT", date(2022, 12, 30), "a holiday week"),
]


def main() -> None:
    config = load_config(
        "dev", overrides={"data": {"lake_path": str(LAKE)}}, config_dir=ROOT / "config"
    )
    lake = Lake(LAKE)
    for symbol, day, why in CASES:
        print(f"\n=== {symbol} on {day}: {why}")
        started = time.monotonic()
        try:
            view = DataView(lake, day, config)
            items = prefetch(view, symbol)
            articles = company_article_count(view, symbol)
        except AtlasError as exc:
            print(f"  {type(exc).__name__}: {exc}")
            continue
        kinds = Counter(i.id.split(":")[0] for i in items)
        print(
            f"  {len(items)} items in {time.monotonic() - started:.1f} s: {dict(kinds)}; company articles {articles}"
        )
        for item in items:
            if item.id.split(":")[0] in {"ncov", "evt", "rxs", "gap"}:
                print(f"  [{item.id}] {item.text[:330]}")
        late = [i.id for i in items if i.as_of >= view.as_of.cutoff]
        print(f"  items timed at or after the cutoff: {late}")


if __name__ == "__main__":
    main()
