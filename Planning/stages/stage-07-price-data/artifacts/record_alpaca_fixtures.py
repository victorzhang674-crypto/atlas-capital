"""Record the Alpaca price fixtures from real responses (Phase 1 checkpoint, Tier 2, 2026-11-02).

Replaces `build_alpaca_fixtures.py` (built shapes filled with Yahoo numbers, used while no keys
were available). The requests are the adapter's own (`AlpacaPriceProvider`, IEX feed), made
through an httpx response hook that keeps each JSON body; request headers (the keys) are never
stored. Writes into `tests/fixtures/prices/`:

- `alpaca_bars_raw_p1.json`, `alpaca_bars_raw_p2.json`: `adjustment=raw` for AAPL+SPY,
  2020-08-27 → 2020-09-02, with 5 bars per page so the answer has two pages;
- `alpaca_bars_all.json`: `adjustment=all` for the same request, one page;
- `alpaca_corporate_actions.json`: AAPL+SPY, ex-dates 2020-08-01 → 2020-09-30 (the adapter
  queries 90 days further for dividends' processing dates);
- `alpaca_corporate_actions_ge.json`: GE, 2021-07-26 → 2021-08-06 (1-for-8 reverse split).

Usage (keys in the environment): uv run python <this file> <repo root>
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from atlas.core.config import load_config
from atlas.data.providers import alpaca_provider
from atlas.data.providers.alpaca_provider import AlpacaPriceProvider

START, END = date(2020, 8, 27), date(2020, 9, 2)


def record(root: Path, call: Any, page_limit: int | None = None) -> list[tuple[dict, Any]]:
    """`(query params, JSON body)` of every response `call(provider)` triggers."""
    cfg = load_config(config_dir=root / "config")
    key, secret = cfg.secrets.alpaca_api_key_id, cfg.secrets.alpaca_api_secret_key
    assert key is not None and secret is not None, "Alpaca keys not set"
    seen: list[tuple[dict, Any]] = []

    def keep(response: httpx.Response) -> None:
        response.read()
        response.raise_for_status()
        seen.append((dict(response.request.url.params), response.json()))

    default = alpaca_provider._BAR_PAGE_LIMIT
    if page_limit is not None:
        alpaca_provider._BAR_PAGE_LIMIT = page_limit
    client = httpx.Client(timeout=30, event_hooks={"response": [keep]})
    try:
        call(AlpacaPriceProvider(key, secret, cfg.data.prices, client=client))
    finally:
        client.close()
        alpaca_provider._BAR_PAGE_LIMIT = default
    return seen


def write(path: Path, body: Any) -> None:
    path.write_text(json.dumps(body, indent=2) + "\n")
    print(f"wrote {path.name}")


def main() -> int:
    root = Path(sys.argv[1])
    out = root / "tests" / "fixtures" / "prices"
    paged = record(root, lambda p: p.fetch_daily(["AAPL", "SPY"], START, END), page_limit=5)
    raw = [body for params, body in paged if params["adjustment"] == "raw"]
    print(f"raw pages: {len(raw)}; tokens: {[b.get('next_page_token') for b in raw]}")
    assert len(raw) == 2, "expected two raw pages"
    write(out / "alpaca_bars_raw_p1.json", raw[0])
    write(out / "alpaca_bars_raw_p2.json", raw[1])
    whole = record(root, lambda p: p.fetch_daily(["AAPL", "SPY"], START, END))
    adjusted = [body for params, body in whole if params["adjustment"] == "all"]
    assert len(adjusted) == 1, "expected one adjusted page"
    write(out / "alpaca_bars_all.json", adjusted[0])
    actions = record(root, lambda p: p.fetch_actions(["AAPL", "SPY"], date(2020, 8, 1),
                                                     date(2020, 9, 30)))
    assert len(actions) == 1, "expected one actions page"
    write(out / "alpaca_corporate_actions.json", actions[0][1])
    ge = record(root, lambda p: p.fetch_actions(["GE"], date(2021, 7, 26), date(2021, 8, 6)))
    assert len(ge) == 1, "expected one GE actions page"
    write(out / "alpaca_corporate_actions_ge.json", ge[0][1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
