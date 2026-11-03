"""Add the 2015–2017 leavers to config/cik_overrides.yaml (Phase 1 checkpoint, Tier 3, 2026-11-02).

Input: `phase1-checkpoint-2026-11-02-build-cik-overrides-2015.txt` (the builder's output for
the 78 former members without an entry). Same rule as `write_cik_overrides.py` (Stage 09
addendum E3): exactly one verified candidate (name score >= 0.6 and >= 1 10-K/10-Q in the
membership window) is taken; every other symbol is decided by hand in REVIEWED. Existing
entries are kept as they are; the header is rewritten for the 2015 floor.
Run from the repo root: uv run python <this file>
"""

import re
from pathlib import Path

import yaml

ART = Path("Planning/stages/stage-09-fundamentals/artifacts")
OUT = Path("config/cik_overrides.yaml")

# symbol -> (cik or None, evidence); reviewed 2026-11-02 (see the Stage 09 worklog)
REVIEWED: dict[str, tuple[int | None, str]] = {
    "ACE": (20171, "CHUBB CORP: the change log removes 'ACE / Chubb' on 2016-01-19 when ACE Ltd acquired Chubb Corp and took its CB ticker; the removed company is Chubb Corp (ACE Ltd continues as today's CB, 896159)"),
    "ADT": (1546640, "ADT Corp (2012 Tyco spin-off, taken private 2016); not Tyco/Johnson Controls 833444 or today's ADT Inc. 1703056"),
    "CAM": (941548, "CAMERON INTERNATIONAL CORP; not Jewett-Cameron Trading 885307"),
    "CCE": (1491675, "Coca-Cola Enterprises, Inc. (the 2010 entity, now Coca-Cola European Partners US); not The Coca-Cola Co 21344"),
    "CMCSK": (1166691, "COMCAST CORP: CMCSK was Comcast's special class A share, the same issuer as CMCSA (in the index 2015-09-18..2015-12-15; the name search found no filer)"),
    "CPGX": (1629995, "Columbia Pipeline Group, Inc.; not Columbia Pipeline Partners LP 1420783"),
    "CVC": (1053112, "CABLEVISION SYSTEMS CORP /NY; not its subsidiary CSC Holdings 784681"),
    "DNB": (1115222, "DUN & BRADSTREET CORP/NW (the 2000 entity); not Moody's 1059556 (the pre-2000 D&B)"),
    "EMC": (790070, "EMC CORP; not EMC Insurance Group 356130 or Scandium International (EMC Metals) 1408146"),
    "HAR": (800459, "HARMAN INTERNATIONAL INDUSTRIES INC /DE/; not Handy & Harman 106618"),
    "KRFT": (1545158, "Kraft Foods Group, Inc. (the 2012 spin-off); not Mondelez 1103982 (the former Kraft Foods Inc)"),
    "LO": (1424847, "LORILLARD, INC. (now Lorillard, LLC); 6 10-K/10-Q in window (name score 0.50: 'Tobacco Company' only in the change-log name)"),
    "NE": (1458891, "Noble Corp plc (UK), the listed parent 2013-2020; not its subsidiary Noble Finance Co 1169055, today's Noble Corp plc 1895262, Noble Energy 72207 or Barnes & Noble 890491"),
    "OI": (812074, "OWENS ILLINOIS INC /DE/ (now O-I Glass); not its subsidiary Owens-Illinois Group 812233"),
    "RIG": (1451505, "Transocean Ltd.; not Transocean Partners LLC 1607250"),
    "SE": (1373835, "Spectra Energy Corp.; not Spectra Energy Partners LP 1394074 or Sea Ltd 1703399"),
    "TYC": (833444, "TYCO INTERNATIONAL plc (continues as Johnson Controls International plc after the 2016-09 merger); not TE Connectivity (formerly Tyco Electronics) 1385157"),
    "WIN": (1282266, "WINDSTREAM HOLDINGS, INC., the listed parent; not its subsidiary Windstream Services 1585644 (name score 0.50: 'Communications' only in the change-log name)"),
}  # fmt: skip

text = (ART / "phase1-checkpoint-2026-11-02-build-cik-overrides-2015.txt").read_text()
new: dict[str, tuple[int | None, str, str]] = {}
for block in re.split(r"\n(?=== )", text):
    if not block.startswith("== "):
        continue
    m = re.match(r"== (\S+) \| (.+) \| window (\S+)\.\.(\S+)", block.splitlines()[0])
    assert m, block.splitlines()[0]
    sym, name, start, end = m.group(1), m.group(2).strip("'\""), m.group(3), m.group(4)
    oks = [ln for ln in block.splitlines()[1:] if ln.strip().startswith("OK ")]
    ciks = {int(re.search(r"cik=(\d+)", ln).group(1)) for ln in oks}
    if sym in REVIEWED:
        cik, evidence = REVIEWED[sym]
    elif len(ciks) == 1:
        line = oks[0]
        cik = ciks.pop()
        sec = re.search(r"sec_name=(['\"])(.*?)\1 former", line).group(2)
        n = re.search(r"periodic_in_window=(\d+)", line).group(1)
        via = "current ticker" if "via=tickers" in line else "full-text search"
        evidence = f"{sec}; {n} 10-K/10-Q filed {start}..{end}; found via {via}"
    else:
        raise SystemExit(f"{sym}: {len(ciks)} verified candidates and no reviewed pick")
    new[sym] = (cik, name, evidence)

old_text = OUT.read_text()
old = {o["symbol"] for o in yaml.safe_load(old_text)["overrides"]}
assert not old & set(new), f"already present: {sorted(old & set(new))}"
entries = [ln for ln in old_text.splitlines() if ln.startswith("  - {symbol: ")]


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


for sym, (cik, name, evidence) in new.items():
    entries.append(
        f"  - {{symbol: {sym}, cik: {'null' if cik is None else cik}, name: {q(name)}, "
        f"evidence: {q(evidence)}}}"
    )
entries.sort(key=lambda ln: re.match(r"  - \{symbol: (\S+),", ln).group(1))
lines = [
    "# Verified SEC CIKs of former S&P 500 members (Stage 09 addendum, owner-approved), read by",
    "# atlas.data.fundamentals.load_cik_overrides; not part of load_config. company_tickers.json",
    "# lists current registrants only, so a former member either has no CIK there or resolves by",
    "# a ticker that may since belong to another company (APC is now ARKO Petroleum, STI is now",
    "# Solidion). Covers every symbol in the S&P 500 on or after 2015-01-01 (the backfill's default",
    "# start) that is not in today's constituents table. An entry replaces the company_tickers.json",
    "# mapping of its symbol; cik: null means no verified SEC CIK (the symbol gets no fundamentals).",
    "# Built 2026-10-28 (leavers since 2018) by Planning/stages/stage-09-fundamentals/artifacts/",
    "# build_cik_overrides.py and extended 2026-11-02 (leavers 2015-2017, Phase 1 checkpoint) by",
    "# phase1-checkpoint-2026-11-02-build-cik-overrides-2015.py (candidates: current ticker + EDGAR",
    "# full-text search of the change-log name; accepted when the SEC name or a former name matches",
    "# and the CIK filed a 10-K/10-Q during the membership window), then reviewed by hand (the",
    "# write_ scripts beside them list the hand-made picks and why).",
    "",
    f"# The list, by symbol ({len(entries)} entries).",
    "overrides:",
]
OUT.write_text("\n".join(lines + entries) + "\n")
print(f"wrote {OUT}: {len(entries)} entries ({len(new)} new, "
      f"{sum(s in REVIEWED for s in new)} of them reviewed by hand)")
