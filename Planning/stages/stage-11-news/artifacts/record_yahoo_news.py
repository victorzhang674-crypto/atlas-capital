"""Record one real Yahoo Finance news answer (yfinance `Search(...).news`) as a test fixture.

Usage: uv run python Planning/stages/stage-11-news/artifacts/record_yahoo_news.py OUT.json
Writes the raw list of news items Yahoo's search API returns for "AAPL", unchanged except
for the `thumbnail` field (image links, never stored by Atlas), which is removed.
yfinance's `Ticker.get_news` endpoint (`/xhr/ncp`) answered HTTP 404 on 2026-10-29.
"""

import json
import sys

import yfinance as yf

items = yf.Search("AAPL", news_count=20, max_results=1).news
for item in items:
    item.pop("thumbnail", None)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(items, fh, indent=2, sort_keys=True)
    fh.write("\n")
print(f"{len(items)} items")
