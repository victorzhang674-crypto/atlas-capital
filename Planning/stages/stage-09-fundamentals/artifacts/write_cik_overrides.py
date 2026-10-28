"""Write config/cik_overrides.yaml from build-cik-overrides-output.txt plus the reviewed picks.

Automatic rule (plan addendum E3): a symbol whose candidates have exactly one verified CIK
(name score >= 0.6 and >= 1 10-K/10-Q in the membership window) gets that CIK. Every other
symbol is decided by hand in REVIEWED (reasons kept in the YAML evidence). Run from the repo
root: uv run python Planning/stages/stage-09-fundamentals/artifacts/write_cik_overrides.py
"""

import re
from pathlib import Path

ART = Path("Planning/stages/stage-09-fundamentals/artifacts")
OUT = Path("config/cik_overrides.yaml")

# symbol -> (cik or None, evidence); reviewed 2026-10-28 (see the worklog)
REVIEWED: dict[str, tuple[int | None, str]] = {
    "AAL": (6201, "parent American Airlines Group Inc. (not subsidiary American Airlines, Inc. 4515)"),
    "AIV": (922864, "Apartment Investment & Management Co, the REIT that issued AIV (not its operating partnership 926660); change-log name 'Aimco'"),
    "ANDV": (50104, "Andeavor (formerly Tesoro Corp), not Andeavor Logistics LP 1507615"),
    "ARNC": (4281, "Arconic Inc. (formerly Alcoa Inc., now Howmet Aerospace) was the member until the 2020-04-01 split; not the spun-off Arconic Corp 1790982"),
    "BCR": (9892, "BARD C R INC /NJ/; 2 10-K/10-Q filed 2017-07-01..2018-01-03 (manual check, artifacts/verify-cik-manual.txt)"),
    "CA": (356028, "CA, INC. (formerly Computer Associates International); 5 10-K/10-Q in window (name score 0.50: 'Technologies' only in the change-log name)"),
    "DISCA": (1437107, "Discovery, Inc. (now Warner Bros. Discovery); not Discovery Oil & Gas 1435387"),
    "DISCK": (1437107, "Discovery, Inc. class C (now Warner Bros. Discovery); not Discovery Oil & Gas 1435387"),
    "DRE": (783280, "DUKE REALTY CORP, not its operating partnership 1003410"),
    "FMC": (37785, "FMC CORP, not the Livent spin-off 1742924"),
    "FRC": (None, "First Republic Bank filed its reports with the FDIC, not the SEC: no SEC fundamentals"),
    "GT": (42582, "GOODYEAR TIRE & RUBBER CO /OH/, not Cooper Tire 24491"),
    "HES": (4447, "HESS CORP, not Hess Midstream 1789832/1619739"),
    "HOG": (793952, "HARLEY-DAVIDSON, INC., not its motorcycle trusts"),
    "HP": (46765, "Helmerich & Payne, Inc., not Cimarex (formerly H&P Exploration) 1168054"),
    "KMX": (1170010, "CARMAX INC, not its auto owner trusts"),
    "KSS": (885639, "KOHLS Corp; 13 10-K/10-Q in window (name score 0: 'Kohl's' vs 'KOHLS' tokenised differently)"),
    "KSU": (54480, "KANSAS CITY SOUTHERN; 18 10-K/10-Q filed 2017-07-01..2021-12-14 (manual check; name search failed with an SEC-side connection error)"),
    "LNC": (59558, "LINCOLN NATIONAL CORP, not Lincoln National Life Insurance Co 726865"),
    "MBC": (1941365, "MasterBrand, Inc.; in the index 2022-12-15..2022-12-19 only, before its first 10-K/10-Q (none in window)"),
    "NAVI": (1593538, "NAVIENT CORP, not its student loan trusts"),
    "RTN": (1047122, "RAYTHEON CO/ until the 2020-04 merger, not RTX Corp (formerly United Technologies) 101829"),
    "SBNY": (None, "Signature Bank filed its reports with the FDIC, not the SEC: no SEC fundamentals"),
    "SLG": (1040971, "SL GREEN REALTY CORP, not its operating partnership 1492869"),
    "TRIP": (1526520, "TripAdvisor, Inc., not Liberty TripAdvisor Holdings 1606745"),
    "TSS": (721683, "TOTAL SYSTEM SERVICES INC (TSYS); 11 10-K/10-Q in window (name score 0: change-log name is the abbreviation 'TSYS')"),
    "UNM": (5513, "Unum Group, not Cogent Biosciences (formerly Unum Therapeutics) 1622229"),
    "VFC": (103379, "V F CORP; 26 10-K/10-Q in window (name score 0: 'VF' vs 'V F' tokenised differently)"),
    "VIAB": (1339947, "Viacom Inc. (2006-2019, 'New Viacom'), not Paramount Global (CBS/ViacomCBS) 813828"),
    "VNO": (899689, "VORNADO REALTY TRUST, not Vornado Realty LP 1040765"),
    "XL": (875159, "XL GROUP LTD, not Destination XL 813298 or EcoloCap 1290506"),
    "XRX": (108772, "XEROX CORP filed throughout the window; Xerox Holdings Corp 1770450 only from 2019"),
}  # fmt: skip

text = (ART / "build-cik-overrides-output.txt").read_text()
rows = []
for block in re.split(r"\n(?=== )", text):
    if not block.startswith("== "):
        continue
    head = block.splitlines()[0]
    m = re.match(r"== (\S+) \| (.+) \| window (\S+)\.\.(\S+)", head)
    assert m, head
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
    rows.append((sym, cik, name, evidence))


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


lines = [
    "# Verified SEC CIKs of former S&P 500 members (Stage 09 addendum, owner-approved), read by",
    "# atlas.data.fundamentals.load_cik_overrides; not part of load_config. company_tickers.json",
    "# lists current registrants only, so a former member either has no CIK there or resolves by",
    "# a ticker that may since belong to another company (APC is now ARKO Petroleum, STI is now",
    "# Solidion). Covers every symbol in the S&P 500 on or after 2018-01-01 (backtest window, spec",
    "# A7) that is not in today's constituents table. An entry replaces the company_tickers.json",
    "# mapping of its symbol; cik: null means no verified SEC CIK (the symbol gets no fundamentals).",
    "# Built 2026-10-28 by Planning/stages/stage-09-fundamentals/artifacts/build_cik_overrides.py",
    "# (candidates: current ticker + EDGAR full-text search of the change-log name; accepted when the",
    "# SEC name or a former name matches and the CIK filed a 10-K/10-Q during the membership window),",
    "# then reviewed by hand (write_cik_overrides.py lists the hand-made picks and why).",
    "",
    f"# The list, by symbol ({len(rows)} entries).",
    "overrides:",
]
for sym, cik, name, evidence in sorted(rows):
    lines.append(
        f"  - {{symbol: {sym}, cik: {'null' if cik is None else cik}, name: {q(name)}, "
        f"evidence: {q(evidence)}}}"
    )
OUT.write_text("\n".join(lines) + "\n")
print(f"wrote {OUT}: {len(rows)} entries, {sum(c is None for _, c, _, _ in rows)} null, "
      f"{sum(s in REVIEWED for s, *_ in rows)} reviewed by hand")
