# Price fixtures (Stage 07)

Used by `tests/unit/data/test_price_*.py`; no test touches the network.

| File | What | Source |
|---|---|---|
| `yfinance_AAPL.csv`, `yfinance_SPY.csv`, `yfinance_SIVB.csv` | One batch `yf.download(["AAPL","SPY","SIVB"], start="2020-08-03", end="2020-10-01", auto_adjust=False, actions=True, group_by="ticker")`, one CSV per symbol, yfinance's own column names. 42 sessions. Yahoo's Close/Open/High/Low, Volume and Dividends are split-adjusted (AAPL's 4:1 split on 2020-08-31). SIVB (SVB Financial, delisted March 2023) came back as all-NaN rows. | **Recorded** from Yahoo with yfinance 1.7.0 on 2026-10-27 |
| `yfinance_splits.csv` | `yf.Ticker(s).splits` for AAPL (5 splits since 1987); SPY has none; SIVB returned `None` | **Recorded** the same day |
| `alpaca_bars_raw_p1.json`, `alpaca_bars_raw_p2.json`, `alpaca_bars_all.json`, `alpaca_corporate_actions.json` | Alpaca market data v2 responses (`/v2/stocks/bars` with `adjustment=raw` in two pages and `adjustment=all`; `/v1/corporate-actions`) for AAPL and SPY, 2020-08-27 → 2020-09-02 | **Built**, not recorded (no Alpaca keys available): documented response shape filled with the recorded Yahoo numbers; XYZ's 1:10 reverse split is invented |

Recorder and builder scripts: `Planning/stages/stage-07-price-data/artifacts/record_yfinance_fixtures.py` and `build_alpaca_fixtures.py`.
