"""Phase 1 checkpoint (2026-11-02): do the built Alpaca fixtures have the real response shape?

Calls the real Alpaca APIs through Atlas's own adapters, with the requests the fixture-based
tests make, captures each JSON answer, and compares field paths and value types with the
built fixtures. Small page sizes force pagination (so `next_page_token` is seen as a string),
and GE's 1-for-8 reverse split (ex-date 2021-08-02) stands in for the fixture's invented XYZ
one. Prints the comparison and the adapters' output sizes only: no payload, key or
header is printed or saved. Needs the Alpaca keys in the environment (`set -a; . .env`).

Usage: uv run python <this file> <repo root> prices|news
"""

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from atlas.core.config import load_config
from atlas.data.news.alpaca_news import AlpacaNewsProvider
from atlas.data.providers import alpaca_provider
from atlas.data.providers.alpaca_provider import AlpacaPriceProvider


def shape(value: Any, path: str = "$", out: dict[str, set[str]] | None = None) -> dict:
    """Map of JSON path → set of value type names (list items share the path `[]`; the
    symbol keys of Alpaca's `bars` map become `*`)."""
    out = {} if out is None else out
    out.setdefault(path, set()).add(type(value).__name__)
    if isinstance(value, dict):
        for key, item in value.items():
            name = "*" if path == "$.bars" else key
            shape(item, f"{path}.{name}", out)
    elif isinstance(value, list):
        for item in value:
            shape(item, f"{path}[]", out)
    return out


def merge(shapes: list[dict]) -> dict:
    merged: dict[str, set[str]] = {}
    for one in shapes:
        for path, types in one.items():
            merged.setdefault(path, set()).update(types)
    return merged


def compare(label: str, built: dict, real: dict) -> int:
    """Print the differences; return the number of fixture paths absent from the real answer
    or with a type the real answer never has (int and float count as one number type)."""
    num = {"int", "float"}

    def norm(types: set[str]) -> set[str]:
        return {"number" if t in num else t for t in types}

    problems = 0
    print(f"== {label}")
    for path in sorted(built):
        if path not in real:
            print(f"  BUILT ONLY   {path} {sorted(built[path])}")
            problems += 1
        elif not norm(built[path]) - {"NoneType"} <= norm(real[path]):
            print(f"  TYPE DIFFERS {path} built={sorted(built[path])} real={sorted(real[path])}")
            problems += 1
    for path in sorted(set(real) - set(built)):
        print(f"  REAL ONLY    {path} {sorted(real[path])} (unused by the adapter)")
    print(f"  paths: built {len(built)}, real {len(real)}, problems {problems}")
    return problems


def main() -> int:
    root, which = Path(sys.argv[1]), sys.argv[2]
    cfg = load_config(config_dir=root / "config")
    secrets = cfg.secrets
    assert secrets.alpaca_api_key_id is not None and secrets.alpaca_api_secret_key is not None
    captured: dict[str, list[Any]] = {}

    def keep(response: httpx.Response) -> None:
        response.read()
        captured.setdefault(response.url.path, []).append(response.json())

    client = httpx.Client(timeout=30, event_hooks={"response": [keep]})
    fixtures = root / "tests" / "fixtures"
    problems = 0
    if which == "prices":
        alpaca_provider._BAR_PAGE_LIMIT = 3  # force several pages per request
        provider = AlpacaPriceProvider(
            secrets.alpaca_api_key_id, secrets.alpaca_api_secret_key, cfg.data.prices, client=client
        )
        start, end = date(2020, 8, 27), date(2020, 9, 2)
        daily = provider.fetch_daily(["AAPL", "SPY"], start, end)
        actions = provider.fetch_actions(["AAPL", "SPY"], start, end)
        reverse = provider.fetch_actions(["GE"], date(2021, 7, 26), date(2021, 8, 6))
        print(f"GE 2021 actions: {list(zip(reverse['type'], reverse['ex_date'].astype(str), reverse['ratio'], strict=True))}")
        print(f"adapter output: {len(daily)} bars, {len(actions)} actions "
              f"({sorted(set(actions['type']))})")
        bars_files = ["alpaca_bars_raw_p1.json", "alpaca_bars_raw_p2.json", "alpaca_bars_all.json"]
        built_bars = merge([shape(json.loads((fixtures / "prices" / f).read_text()))
                            for f in bars_files])
        problems += compare("/v2/stocks/bars", built_bars,
                            merge([shape(p) for p in captured["/v2/stocks/bars"]]))
        built_actions = shape(json.loads((fixtures / "prices" / "alpaca_corporate_actions.json")
                                         .read_text()))
        problems += compare("/v1/corporate-actions", built_actions,
                            merge([shape(p) for p in captured["/v1/corporate-actions"]]))
    else:
        news_cfg = cfg.data.news.model_copy(update={"page_limit": 3})  # force several pages
        provider = AlpacaNewsProvider(
            secrets.alpaca_api_key_id, secrets.alpaca_api_secret_key, news_cfg, client=client
        )
        start, end = datetime(2020, 3, 2, tzinfo=UTC), datetime(2020, 3, 3, tzinfo=UTC)
        out = provider.fetch_news(["AAPL", "MSFT"], start, end)
        print(f"adapter output: {len(out)} articles")
        built = merge([shape(json.loads((fixtures / "news" / f).read_text()))
                       for f in ("alpaca_news_p1.json", "alpaca_news_p2.json")])
        problems += compare("/v1beta1/news", built,
                            merge([shape(p) for p in captured["/v1beta1/news"]]))
    client.close()
    print(f"RESULT: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
