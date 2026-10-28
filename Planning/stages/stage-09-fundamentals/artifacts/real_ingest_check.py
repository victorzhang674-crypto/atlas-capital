"""Real-data check of the Stage 09 pipeline against live data.sec.gov (run twice).

Uses the descriptive project User-Agent (data.sec.gov accepts it; www.sec.gov, which serves
company_tickers.json, does not without contact details, so the three CIKs are given here).
Usage: uv run python real_ingest_check.py <empty lake directory>
"""

import sys
from datetime import date
from pathlib import Path

from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.fundamentals import CikMap, EdgarClient, Fundamentals, ingest_fundamentals

UA = "AtlasCapital/0.1 (research and paper-trading simulator)"
cfg = AtlasConfig()
lake = Lake(Path(sys.argv[1]))
cik_map = CikMap({"AAPL": 320193, "MSFT": 789019, "JPM": 19617})
for run in (1, 2):
    with EdgarClient(cfg.data.fundamentals, user_agent=UA, lake=lake) as client:
        r = ingest_fundamentals(lake, client, ["AAPL", "MSFT", "JPM"], cik_map=cik_map)
    print(f"run {run}: rows={r.rows} ok={r.symbols_ok} failed={r.symbols_failed} "
          f"fallback_rows={r.fallback_rows}")
reader = Fundamentals(lake, cfg)
aapl = reader.pit("AAPL", date(2023, 11, 3))
print(f"AAPL as of 2023-11-03: revenue_ttm={aapl['revenue_ttm']:,.0f} "
      f"net_income_ttm={aapl['net_income_ttm']:,.0f} eps={aapl['eps_diluted_ttm']} "
      f"total_debt={aapl['total_debt']:,.0f} gross_margin={aapl['gross_margin']:.4f}")
print(f"AAPL as of 2023-11-02: revenue_ttm={reader.pit('AAPL', date(2023, 11, 2))['revenue_ttm']:,.0f}")
for day in (date(2026, 10, 27),):
    for sym in ("AAPL", "MSFT", "JPM"):
        d = reader.pit_detail(sym, day).set_index("metric")
        row = d.loc["revenue_ttm"]
        print(f"{sym} as of {day}: revenue_ttm={row['value']:,.0f} to {row['period_end']} "
              f"({row['source']}, accn {row['accn']}); roe={d.loc['roe', 'value']:.4f}; "
              f"NaN metrics={sorted(d.index[d['value'].isna()])}")
