"""Stage 07 verification extra (not a test): ingest REAL Yahoo data for the fixture window into
a throwaway lake and print what the acceptance criteria care about. Needs network access.

    uv run python Planning/stages/stage-07-price-data/artifacts/live_ingest_check.py
"""

import tempfile
import warnings
from datetime import date
from pathlib import Path

from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.prices import adjustment_factors, ingest_prices
from atlas.data.providers.yfinance_provider import YFinanceProvider

warnings.simplefilter("ignore")
START, END = date(2020, 8, 3), date(2020, 9, 30)
with tempfile.TemporaryDirectory() as tmp:
    lake = Lake(Path(tmp) / "lake", registry=DEFAULT_REGISTRY.copy())
    for run in (1, 2):
        report = ingest_prices(lake, YFinanceProvider(), ["AAPL", "SPY", "SIVB"], START, END)
        print(f"run {run}: rows={report.rows} action_rows={report.action_rows} "
              f"ok={report.symbols_ok} failed={report.symbols_failed} "
              f"mismatches={len(report.mismatches)}")
        print(f"  reasons: {dict(report.failure_reasons)}")
    prices = lake.read_table("prices_daily")
    actions = lake.read_table("corporate_actions")
    aapl = prices[prices["symbol"] == "AAPL"].reset_index(drop=True)
    adj = adjustment_factors(aapl, actions).set_index("date")
    for day in (date(2020, 8, 28), date(2020, 8, 31)):
        row = adj.loc[day]
        print(f"AAPL {day}: raw close={row['close']:.4f} split_adj_close={row['split_adj_close']:.4f} "
              f"vendor adj_close={row['adj_close']:.4f} available_at={row['available_at']}")
    print("actions:")
    print(actions.to_string(index=False))
