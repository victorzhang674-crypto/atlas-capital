"""Stage 29 follow-up 4: check the former CIKs of the symbols whose fundamentals start long after
their S&P 500 window (an issuer that moved to a new CIK, or a ticker reused by a later company).
For each pair, the SEC's names for both CIKs and the first and last 10-K/10-Q of each, so that
`until` (the last filing date taken from the former CIK) can be set to the day before the
current CIK's first 10-K/10-Q. Network: data.sec.gov (submissions JSON, archived in a scratch
lake's raw/sec/). Run from the worktree root:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/former_ciks_followup4.py
"""

import os
from datetime import date
from pathlib import Path

import atlas.cli.app  # noqa: F401
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.filings.text import filing_list
from atlas.data.fundamentals import build_edgar_client

ROOT = Path(__file__).resolve().parents[4]
SCRATCH = Path(os.environ["TMPDIR"]) / "s29f4" / "cik-lake"
PAIRS = {  # symbol: (current CIK, [former CIKs, newest first])
    "XOM": (2115436, [34088]),
    "SKYD": (2041610, [813828]),
    "BLK": (2012383, [1364742]),
    "APA": (1841666, [6769]),
    "VTRS": (1792044, [1623613, 69499]),
    "DIS": (1744489, [1001039]),
    "CI": (1739940, [701221]),
    "AVGO": (1730168, [1649338, 1441634]),
    "DD": (1666700, [30554]),
    "LIN": (1707925, [884905]),
    "LLL": (1039101, [1056239]),
    "FTI": (1681459, [1135152]),
    "BG": (1996862, [1144519]),
    "GOOG": (1652044, [1288776]),
    "SNDK": (2023554, [1000180]),
    "TFCF": (1308161, []),
}
cfg = load_config("dev", overrides={"data": {"lake_path": str(SCRATCH)}}, config_dir=ROOT / "config")
with build_edgar_client(cfg, Lake(SCRATCH)) as client:
    for symbol, (current, former) in PAIRS.items():
        print(f"== {symbol}")
        for cik in [current, *former]:
            subs = client.submissions(cik)
            names = [f["name"] for f in subs.get("formerNames", [])]
            periodic = filing_list(client, cik, forms=("10-K", "10-Q"), since=date(2010, 1, 1), refresh=False)
            first = periodic["filed"].min() if len(periodic) else None
            last = periodic["filed"].max() if len(periodic) else None
            print(f"  CIK {cik}: {subs.get('name')!r} tickers={subs.get('tickers')} "
                  f"former={names[:3]} 10-K/10-Q since 2010: {len(periodic)} ({first} .. {last})")
