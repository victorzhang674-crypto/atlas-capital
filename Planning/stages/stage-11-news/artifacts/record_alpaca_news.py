"""Record a real Alpaca news answer as a fixture (Phase 1 checkpoint, Tier 2, 2026-11-02).

The built pages `alpaca_news_p1/p2.json` stay: they hold designed edge cases (a roundup, an
invalid tag, article bodies) that a real answer cannot guarantee. This adds
`tests/fixtures/news/alpaca_news_aapl_recorded.json`: the adapter's own request
(`AlpacaNewsProvider`, `include_content=false`) for AAPL, 2020-03-02 00:00 → 03-03 00:00 UTC, one
page. Before writing, each `summary` is replaced by a marker and `images` are emptied
(copyright and size; headlines are kept, as in the recorded Yahoo fixture); request headers
(the keys) are never stored.

Usage (keys in the environment): uv run python <this file> <repo root>
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from atlas.core.config import load_config
from atlas.data.news.alpaca_news import AlpacaNewsProvider

START = datetime(2020, 3, 2, tzinfo=UTC)
END = datetime(2020, 3, 3, tzinfo=UTC)
MARKER = "(summary removed from the recording)"


def main() -> int:
    root = Path(sys.argv[1])
    cfg = load_config(config_dir=root / "config")
    key, secret = cfg.secrets.alpaca_api_key_id, cfg.secrets.alpaca_api_secret_key
    assert key is not None and secret is not None, "Alpaca keys not set"
    pages: list[Any] = []

    def keep(response: httpx.Response) -> None:
        response.read()
        response.raise_for_status()
        pages.append(response.json())

    client = httpx.Client(timeout=30, event_hooks={"response": [keep]})
    try:
        out = AlpacaNewsProvider(key, secret, cfg.data.news, client=client).fetch_news(
            ["AAPL"], START, END
        )
    finally:
        client.close()
    assert len(pages) == 1, f"expected one page, got {len(pages)}"
    body = pages[0]
    for item in body["news"]:
        item["summary"] = MARKER if item.get("summary") else item.get("summary")
        item["images"] = []
    path = root / "tests" / "fixtures" / "news" / "alpaca_news_aapl_recorded.json"
    path.write_text(json.dumps(body, indent=2) + "\n")
    print(f"wrote {path.name}: {len(body['news'])} items; adapter kept {len(out)} articles; "
          f"next_page_token={body.get('next_page_token')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
