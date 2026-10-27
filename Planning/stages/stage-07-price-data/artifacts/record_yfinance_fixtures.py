"""One-off recorder for tests/fixtures/prices/ (Stage 07). Run from the repo root:

    uv run python Planning/stages/stage-07-price-data/artifacts/record_yfinance_fixtures.py

Writes exactly what yfinance returned: one CSV per symbol from a batch `yf.download` (vendor
column names, split-adjusted Close/Volume/Dividends as Yahoo serves them) and each symbol's
full split history from `yf.Ticker(symbol).splits`. Needs network access to Yahoo.
"""

import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT = Path("tests/fixtures/prices")
SYMBOLS = ["AAPL", "SPY", "SIVB"]  # SIVB: SVB Financial, delisted March 2023
START, END_EXCLUSIVE = "2020-08-03", "2020-10-01"

warnings.simplefilter("ignore")
frame = yf.download(
    SYMBOLS, start=START, end=END_EXCLUSIVE, auto_adjust=False, actions=True,
    group_by="ticker", threads=False, progress=False,
)
for sym in SYMBOLS:
    frame[sym].to_csv(OUT / f"yfinance_{sym}.csv", index_label="Date")
rows = []
for sym in SYMBOLS:
    try:
        splits = yf.Ticker(sym).splits
    except Exception as exc:  # recorder only: note and continue
        print(f"{sym}: splits failed: {exc!r}")
        continue
    if splits is None:  # yfinance returns None for a delisted symbol
        print(f"{sym}: no split history (None)")
        continue
    for ts, ratio in splits.items():
        rows.append({"symbol": sym, "date": ts.date().isoformat(), "ratio": float(ratio)})
pd.DataFrame(rows, columns=["symbol", "date", "ratio"]).to_csv(
    OUT / "yfinance_splits.csv", index=False
)
print(frame.shape, len(rows), "split rows")
