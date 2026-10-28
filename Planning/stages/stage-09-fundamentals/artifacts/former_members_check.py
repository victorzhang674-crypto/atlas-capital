"""Real-data check (Stage 09 addendum): former S&P 500 members load through build_cik_map.

Live EDGAR with SEC_USER_AGENT; one as-of date while each name was still in the index.
Usage (repo root): uv run python Planning/stages/stage-09-fundamentals/artifacts/former_members_check.py <empty lake dir>
"""

import sys
from datetime import date
from pathlib import Path

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals import Fundamentals, build_cik_map, build_edgar_client, ingest_fundamentals

CASES = {"CELG": date(2019, 6, 3), "ATVI": date(2022, 6, 1), "APC": date(2019, 3, 1),
         "STI": date(2019, 6, 3), "SIVB": date(2022, 12, 1), "FRC": date(2022, 6, 1)}  # fmt: skip
cfg = load_config("dev")
lake = Lake(Path(sys.argv[1]))
with build_edgar_client(cfg, lake) as client:
    cik_map = build_cik_map(client, cfg)
    report = ingest_fundamentals(lake, client, list(CASES), cik_map=cik_map, config=cfg)
print(f"ok={report.symbols_ok} failed={dict(report.failure_reasons)} rows={report.rows} "
      f"fallback_rows={report.fallback_rows}")
reader = Fundamentals(lake, cfg)
for sym, day in CASES.items():
    if sym in report.symbols_ok:
        d = reader.pit_detail(sym, day).set_index("metric")
        r = d.loc["revenue_ttm"]
        print(f"{sym} cik={cik_map.cik(sym)} as of {day}: revenue_ttm={r['value']:,.0f} "
              f"to {r['period_end']} ({r['source']}, accn {r['accn']}); "
              f"net_income_ttm={d.loc['net_income_ttm', 'value']:,.0f}")
