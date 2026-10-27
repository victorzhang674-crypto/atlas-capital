"""One-off builder for tests/fixtures/prices/alpaca_*.json (Stage 07). Run from the repo root:

    uv run python Planning/stages/stage-07-price-data/artifacts/build_alpaca_fixtures.py

No Alpaca keys were available, so these are not recordings: they follow the documented Alpaca
market data v2 response shapes (`/v2/stocks/bars`, `/v1/corporate-actions`) and reuse the
recorded yfinance numbers (raw = Yahoo's split-adjusted value x 4 before 2020-08-31; the
`adjustment=all` close = Yahoo's Adj Close). XYZ's 1:10 reverse split is invented.
"""

import json
from pathlib import Path

import pandas as pd

FIX = Path("tests/fixtures/prices")
DAYS = ["2020-08-27", "2020-08-28", "2020-08-31", "2020-09-01", "2020-09-02"]


def bars(symbol: str, adjusted: bool) -> list[dict[str, object]]:
    frame = pd.read_csv(FIX / f"yfinance_{symbol}.csv", index_col="Date")
    out = []
    for day in DAYS:
        row = frame.loc[day]
        f = 4.0 if symbol == "AAPL" and day < "2020-08-31" else 1.0
        if adjusted:
            k = row["Adj Close"] / row["Close"]
            o, h, lo, c = (round(row[x] * k, 4) for x in ("Open", "High", "Low", "Close"))
            v = row["Volume"]
        else:
            o, h, lo, c = (round(row[x] * f, 4) for x in ("Open", "High", "Low", "Close"))
            v = row["Volume"] / f
        out.append(
            {"t": f"{day}T04:00:00Z", "o": o, "h": h, "l": lo, "c": c, "v": int(v),
             "n": 1000, "vw": c}
        )
    return out


def dump(name: str, body: object) -> None:
    (FIX / name).write_text(json.dumps(body, indent=2) + "\n")


dump("alpaca_bars_raw_p1.json", {"bars": {"AAPL": bars("AAPL", False)}, "next_page_token": "page-2"})
dump("alpaca_bars_raw_p2.json", {"bars": {"SPY": bars("SPY", False)}, "next_page_token": None})
dump(
    "alpaca_bars_all.json",
    {"bars": {"AAPL": bars("AAPL", True), "SPY": bars("SPY", True)}, "next_page_token": None},
)
dump(
    "alpaca_corporate_actions.json",
    {
        "corporate_actions": {
            "forward_splits": [
                {"id": "fs-1", "symbol": "AAPL", "cusip": "037833100", "new_rate": 4,
                 "old_rate": 1, "process_date": "2020-08-31", "ex_date": "2020-08-31",
                 "record_date": "2020-08-24", "payable_date": "2020-08-28"}
            ],
            "reverse_splits": [
                {"id": "rs-1", "symbol": "XYZ", "cusip": "000000000", "new_rate": 1,
                 "old_rate": 10, "process_date": "2020-09-01", "ex_date": "2020-09-01",
                 "record_date": "2020-09-01", "payable_date": "2020-09-01"}
            ],
            "cash_dividends": [
                {"id": "cd-1", "symbol": "AAPL", "cusip": "037833100", "rate": 0.82,
                 "special": False, "foreign": False, "process_date": "2020-08-13",
                 "ex_date": "2020-08-07", "record_date": "2020-08-10",
                 "payable_date": "2020-08-13"},
                {"id": "cd-2", "symbol": "SPY", "cusip": "78462F103", "rate": 1.339,
                 "special": False, "foreign": False, "process_date": "2020-10-30",
                 "ex_date": "2020-09-18", "record_date": "2020-09-21",
                 "payable_date": "2020-10-30"},
            ],
        },
        "next_page_token": None,
    },
)
print("written")
