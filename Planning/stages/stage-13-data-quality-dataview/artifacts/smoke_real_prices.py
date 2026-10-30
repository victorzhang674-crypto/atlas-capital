"""Stage 13 smoke run on real prices (Yahoo via Stage 07): quality report and DataView.

Usage: python smoke_real_prices.py <scratch-lake-dir>. Read-only on the repository; writes
only the scratch lake. Prints issue counts, the blocked set and a few DataView answers.
"""

import sys
from datetime import date
from pathlib import Path

from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.prices import ingest_prices
from atlas.data.providers.yfinance_provider import YFinanceProvider
from atlas.data.quality import quality_report
from atlas.data.view import DataView

SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "JNJ", "PG",
           "KO", "WMT", "BRK.B", "TSLA", "AVGO", "SPY"]  # fmt: skip
lake = Lake(Path(sys.argv[1]), registry=DEFAULT_REGISTRY.copy())
report = ingest_prices(lake, YFinanceProvider(), SYMBOLS, date(2023, 1, 1), date(2024, 9, 30))
print("ingest:", report.rows, "rows,", report.action_rows, "actions, failed:", report.symbols_failed)
for day in (date(2023, 12, 1), date(2024, 6, 3), date(2024, 6, 14), date(2024, 7, 1), date(2024, 9, 30)):
    issues = quality_report(lake, day)
    counts = issues.groupby(["check", "severity"]).size().to_dict() if len(issues) else {}
    print(f"\n{day}: {len(issues)} issues {counts}")
    if len(issues):
        print(issues[["symbol", "date", "check", "severity", "detail"]].to_string(index=False))
    view = DataView(lake, day)
    print("blocked:", sorted(view.blocked))
    ret = view.returns(["NVDA", "AVGO", "AAPL"], 5)
    print("returns (last 5 sessions):\n" + ret.round(4).to_string())
    adv = view.adv(["NVDA", "AAPL"])
    print("adv:\n" + adv.round(0).to_string())
