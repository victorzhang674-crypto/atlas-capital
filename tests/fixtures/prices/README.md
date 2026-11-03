# Price fixtures (Stage 07)

Used by `tests/unit/data/test_price_*.py`; no test touches the network.

| File | What | Source |
|---|---|---|
| `yfinance_AAPL.csv`, `yfinance_SPY.csv`, `yfinance_SIVB.csv` | One batch `yf.download(["AAPL","SPY","SIVB"], start="2020-08-03", end="2020-10-01", auto_adjust=False, actions=True, group_by="ticker")`, one CSV per symbol, yfinance's own column names. 42 sessions. Yahoo's Close/Open/High/Low, Volume and Dividends are split-adjusted (AAPL's 4:1 split on 2020-08-31). SIVB (SVB Financial, delisted March 2023) came back as all-NaN rows. | **Recorded** from Yahoo with yfinance 1.7.0 on 2026-10-27 |
| `yfinance_splits.csv` | `yf.Ticker(s).splits` for AAPL (5 splits since 1987); SPY has none; SIVB returned `None` | **Recorded** the same day |
| `alpaca_bars_raw_p1.json`, `alpaca_bars_raw_p2.json`, `alpaca_bars_all.json`, `alpaca_corporate_actions.json` | Alpaca market data responses to the adapter's own requests (IEX feed): `/v2/stocks/bars` with `adjustment=raw` for AAPL+SPY 2020-08-27 → 2020-09-02 at 5 bars per page (page 1 = AAPL, page 2 = SPY, linked by Alpaca's opaque `next_page_token`), the same with `adjustment=all` in one page; `/v1/corporate-actions` for AAPL+SPY, ex-dates 2020-08-01 → 2020-09-30 (AAPL 4:1 split, dividends; AAPL's 2020-11-06 dividend comes back too and falls outside the window). IEX prices and volumes differ from Yahoo's consolidated ones | **Recorded** from Alpaca on 2026-11-02 (Phase 1 checkpoint; built until then) |
| `alpaca_corporate_actions_ge.json` | `/v1/corporate-actions` for GE, 2021-07-26 → 2021-08-06: the 1-for-8 reverse split of 2021-08-02, plus the 2021-06-25 dividend listed twice (once per CUSIP) | **Recorded** the same day |

Recorder scripts: `Planning/stages/stage-07-price-data/artifacts/record_yfinance_fixtures.py` and `record_alpaca_fixtures.py` (`build_alpaca_fixtures.py` built the earlier Alpaca files).
