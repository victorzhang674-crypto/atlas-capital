"""Stage 12 smoke run against the real sources (2026-10-29): download the LM lexicon into the
configured `data/reference/lm_lexicon.csv`, ingest AAPL earnings (EDGAR) and the macro calendar
(FRED + YAML) into a scratch lake, then query point in time. Secrets come from .env."""

import sys
import tempfile
from datetime import date
from pathlib import Path

from atlas.core.clock import LiveClock, use_clock
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.events import (
    Events,
    build_upcoming_provider,
    ingest_earnings,
    ingest_macro_calendar,
    ingest_upcoming_events,
)
from atlas.data.fundamentals.edgar_client import CikMap, build_edgar_client
from atlas.data.macro.fred_client import build_fred_client
from atlas.data.sentiment import download_lm_lexicon, load_lexicon, score_text


def main() -> int:
    cfg = load_config("dev")
    path = download_lm_lexicon(cfg.data.sentiment)
    lexicon = load_lexicon(path)
    print(f"lexicon {path}: version {lexicon.version}, negative {len(lexicon.negative)}, "
          f"positive {len(lexicon.positive)}, uncertainty {len(lexicon.uncertainty)}")
    print(f"  BENEFIT positive in 2019: {'BENEFIT' in lexicon.for_year(2019).positive}, "
          f"in 2021: {'BENEFIT' in lexicon.for_year(2021).positive}")
    for text in ("Apple beats estimates as iPhone sales surge to a record",
                 "Intel warns of weak demand and further losses; shares plunge",
                 "Fed officials uncertain whether inflation will ease"):
        print(f"  {score_text(text, lexicon)}  <- {text!r}")
    with tempfile.TemporaryDirectory() as tmp:
        lake = Lake(Path(tmp) / "lake")
        with build_edgar_client(cfg) as edgar:
            report = ingest_earnings(lake, edgar, ["AAPL"], cik_map=CikMap({"AAPL": 320193}),
                                     config=cfg)
        print(f"earnings: {report}")
        with use_clock(LiveClock()), build_fred_client(cfg) as fred:
            print(f"macro events: {ingest_macro_calendar(lake, fred, config=cfg)} rows")
            provider = build_upcoming_provider(cfg)
            if provider is not None:
                print(f"scheduled rows: {ingest_upcoming_events(lake, provider, ['AAPL'])}")
        events = Events(lake, cfg)
        print("past AAPL at 2024-02-02:")
        print(events.past("AAPL", date(2024, 2, 2), n=3).to_string())
        print("upcoming at 2024-01-29, 14 days:")
        print(events.upcoming(date(2024, 1, 29), horizon_days=14).to_string())
        print("upcoming at 2024-04-22, 14 days (no Yahoo snapshot then: estimated AAPL date):")
        print(events.upcoming(date(2024, 4, 22), horizon_days=14).to_string())
        print("upcoming at 2025-10-06, 30 days (shutdown window: releases not known ahead):")
        print(events.upcoming(date(2025, 10, 6), horizon_days=30).to_string())
        print("upcoming at 2015-12-10, 10 days (2015 FOMC now covered):")
        print(events.upcoming(date(2015, 12, 10), horizon_days=10).to_string())
        print("upcoming at 2020-03-10, 10 days (cancelled meeting still scheduled):")
        print(events.upcoming(date(2020, 3, 10), horizon_days=10).to_string())
        print("upcoming at 2020-03-16, 10 days (after the cancellation):")
        print(events.upcoming(date(2020, 3, 16), horizon_days=10).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
