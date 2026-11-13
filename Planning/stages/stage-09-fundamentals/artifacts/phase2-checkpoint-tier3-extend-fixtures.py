"""Phase 2 checkpoint (Tier 2 item 7, Tier 3 item 2): add the facts of the tags the concept
map gained (depreciation & amortisation, weighted-average shares) to the recorded
companyfacts fixtures of AAPL, MSFT and JPM.

No network: the facts come from the archived companyfacts JSON of a lake
(`<lake>/raw/sec/companyfacts`), as EDGAR served them. Only facts of filings the fixture
already holds are added (accession number present in the fixture), so the trimmed filing
index beside it still covers every fact. Values are copied unchanged.

Usage: python <this file> <lake>/raw/sec/companyfacts tests/fixtures/fundamentals/companyfacts
"""

import json
import sys
from pathlib import Path

TAGS = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "Depreciation",
    "AmortizationOfIntangibleAssets",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
)
CIKS = (320193, 789019, 19617)

archive, fixtures = Path(sys.argv[1]), Path(sys.argv[2])
for cik in CIKS:
    name = f"CIK{cik:010d}.json"
    fixture = json.loads((fixtures / name).read_text())
    recorded = json.loads((archive / name).read_text())
    held = {
        fact["accn"]
        for taxonomy in fixture["facts"].values()
        for tag in taxonomy.values()
        for unit in tag["units"].values()
        for fact in unit
    }
    added = 0
    for tag in TAGS:
        body = recorded["facts"]["us-gaap"].get(tag)
        if body is None or tag in fixture["facts"]["us-gaap"]:
            continue
        units = {
            unit: [fact for fact in facts if fact["accn"] in held]
            for unit, facts in body["units"].items()
        }
        units = {unit: facts for unit, facts in units.items() if facts}
        if not units:
            continue
        fixture["facts"]["us-gaap"][tag] = {**body, "units": units}
        added += sum(len(facts) for facts in units.values())
        print(f"{name}: {tag}: {sum(len(f) for f in units.values())} facts")
    (fixtures / name).write_text(json.dumps(fixture, separators=(",", ":")) + "\n")  # as recorded
    print(f"{name}: {added} facts added, {len(fixture['facts']['us-gaap'])} us-gaap tags now")
