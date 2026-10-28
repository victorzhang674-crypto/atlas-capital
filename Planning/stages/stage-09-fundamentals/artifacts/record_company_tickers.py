"""Record tests/fixtures/fundamentals/company_tickers.json from the real EDGAR file.

Needs SEC_USER_AGENT in .env (www.sec.gov answers 403 without contact details). Keeps the
entries of the six registrants the tests use, with their original keys, order and fields.
The User-Agent is never printed. Usage (repo root):
uv run python Planning/stages/stage-09-fundamentals/artifacts/record_company_tickers.py
"""

import json
from pathlib import Path

from atlas.core.config import load_config
from atlas.data.fundamentals import build_edgar_client

CIKS = {320193, 789019, 19617, 1067983, 1652044, 1326801}  # AAPL MSFT JPM BRK GOOGL META
OUT = Path("tests/fixtures/fundamentals/company_tickers.json")

with build_edgar_client(load_config("dev")) as client:
    full = client.company_tickers(refresh=True)
kept = {k: v for k, v in full.items() if v["cik_str"] in CIKS}
OUT.write_text(json.dumps(kept, separators=(",", ":")) + "\n")
print(f"full file: {len(full)} entries; kept {len(kept)}: {[v['ticker'] for v in kept.values()]}")
