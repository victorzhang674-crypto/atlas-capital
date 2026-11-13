"""Phase 2 checkpoint (Tier 2 item 7, Tier 3 item 2): which XBRL tags the archived
companyfacts files use for depreciation & amortisation and for weighted-average share counts.

Read only. Usage: python <this file> <lake>/raw/sec/companyfacts
Per tag: companies with a fact whose period ends in 2015 or later, and companies with one
ending in 2023 or later (USD durations for D&A, share durations for the averages).
"""

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

folder = Path(sys.argv[1])
da_words = ("Depreciation", "Amortization")
since_2015: Counter[str] = Counter()
since_2023: Counter[str] = Counter()
share_2015: Counter[str] = Counter()
share_2023: Counter[str] = Counter()
per_company: dict[str, dict[str, set[int]]] = {}
files = sorted(folder.glob("CIK*.json"))
for path in files:
    data = json.loads(path.read_text())
    gaap = data.get("facts", {}).get("us-gaap", {})
    years: dict[str, set[int]] = {}
    for tag, body in gaap.items():
        is_da = any(w in tag for w in da_words)
        is_avg = tag.startswith("WeightedAverageNumberOf")
        if not (is_da or is_avg):
            continue
        unit = "USD" if is_da else "shares"
        ends = set()
        for fact in body.get("units", {}).get(unit, []):
            if "start" not in fact:
                continue
            end = date.fromisoformat(fact["end"])
            days = (end - date.fromisoformat(fact["start"])).days
            if is_da and not 350 <= days <= 380:
                continue  # fiscal years only: one count per company and year
            ends.add(end.year)
        if not ends:
            continue
        years[tag] = ends
        target15, target23 = (since_2015, since_2023) if is_da else (share_2015, share_2023)
        if max(ends) >= 2015:
            target15[tag] += 1
        if max(ends) >= 2023:
            target23[tag] += 1
    per_company[path.stem] = years

print(f"{len(files)} companyfacts files")
print("\n== D&A-like us-gaap tags (USD, fiscal-year facts): companies with a year >= 2015 | >= 2023")
for tag, n in since_2015.most_common(45):
    print(f"  {n:4d} | {since_2023[tag]:4d}  {tag}")
print("\n== weighted-average share tags (shares, any duration): companies >= 2015 | >= 2023")
for tag, n in share_2015.most_common(12):
    print(f"  {n:4d} | {share_2023[tag]:4d}  {tag}")

chains = {
    "DDA only": [["DepreciationDepletionAndAmortization"]],
    "DDA, DA": [["DepreciationDepletionAndAmortization"], ["DepreciationAndAmortization"]],
    "DDA, DA, DAAccretion": [
        ["DepreciationDepletionAndAmortization"],
        ["DepreciationAndAmortization"],
        ["DepreciationAmortizationAndAccretionNet"],
    ],
    "DDA, DA, DAAccretion, Depreciation + AmortizationOfIntangibleAssets": [
        ["DepreciationDepletionAndAmortization"],
        ["DepreciationAndAmortization"],
        ["DepreciationAmortizationAndAccretionNet"],
        ["Depreciation", "AmortizationOfIntangibleAssets"],
    ],
}
print("\n== company-years 2015-2025 with a fiscal-year D&A value, by fallback chain")
total = sum(
    1
    for years in per_company.values()
    for y in range(2015, 2026)
    if y in years.get("Assets", set()) or True
)
for name, chain in chains.items():
    covered = companies = 0
    for years in per_company.values():
        got = set()
        for alt in chain:
            sets = [years.get(tag, set()) for tag in alt]
            got |= set.intersection(*sets)
        hit = len([y for y in got if 2015 <= y <= 2025])
        covered += hit
        companies += bool(hit)
    print(f"  {covered:5d} company-years, {companies:4d} companies  {name}")
