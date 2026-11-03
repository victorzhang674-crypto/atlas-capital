"""Find and verify a CIK for every former S&P 500 member that left in 2015–2017 (Phase 1
checkpoint, Tier 3, 2026-11-02): a copy of the Stage 09 builder (`build_cik_overrides.py`) with
the floor moved to 2015-01-01 and the symbols already in `config/cik_overrides.yaml` skipped.

Former member = a symbol with a membership interval reaching 2015-01-01 or later (Stage 08
reconstruction from the recorded Wikipedia fixtures) that is not in today's constituents.
Candidates per symbol: the CIK of its current ticker in the real company_tickers.json, and the
filers found by EDGAR full-text search for its change-log name in 10-K/10-Q filings during its
membership window. Each candidate is checked against its own submissions: best name match
(current or former SEC name) and the number of 10-K/10-Q filings during the window.
Prints one block per symbol for review; the reviewed result is config/cik_overrides.yaml.
Needs SEC_USER_AGENT in .env. Usage (repo root):
uv run python <this file> <scratch lake dir> [SYM,SYM,...]
"""

import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals import CikMap, build_edgar_client
from atlas.data.reference import build_instruments, load_ticker_map, parse_changes, parse_constituents
from atlas.data.universe import build_membership

FLOOR = date(2015, 1, 1)
TODAY = date(2026, 11, 2)
WINDOW_FROM = date(2014, 7, 1)  # half a year before the floor, as 2017-07-01 was for 2018
STOP = {"inc", "corp", "corporation", "co", "company", "the", "plc", "ltd", "limited", "holdings",
        "holding", "group", "de", "sa", "nv", "llc", "lp", "incorporated", "class", "a", "b", "c",
        "and", "of", "new", "trust", "international", "intl", "com"}  # fmt: skip
PERIODIC = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT"}


def tokens(name: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " and ")).split()
    return {w for w in words if w not in STOP}


def name_score(log_name: str, sec_names: list[str]) -> float:
    want = tokens(log_name)
    if not want:
        return 0.0
    return max((len(want & tokens(n)) / len(want) for n in sec_names), default=0.0)


cfg = load_config("dev")
ua = cfg.secrets.sec_user_agent.get_secret_value()  # never printed
fx = Path("tests/fixtures/universe")
cons = parse_constituents((fx / "wikipedia_sp500_constituents.html").read_text())
chg = parse_changes((fx / "wikipedia_sp500_changes.html").read_text())
tmap = load_ticker_map(Path("config/ticker_changes.yaml"))
build = build_membership(cons, chg, ticker_map=tmap)
iv = build.intervals
recent = iv[iv["end_date"].isna() | (iv["end_date"] > FLOOR)]
import yaml  # noqa: E402

done = {o["symbol"] for o in yaml.safe_load(Path("config/cik_overrides.yaml").read_text())["overrides"]}
former = sorted(set(recent["symbol"]) - set(cons["symbol"]) - done)
if len(sys.argv) > 2:  # optional comma-separated symbols: resume after a dropped connection
    former = [s for s in former if s in set(sys.argv[2].split(","))]
print(f"# {len(former)} former members since {FLOOR} without an override")
names = {i.symbol: i.name for i in build_instruments(cons, chg, iv, etf_symbols=(), ticker_map=tmap)}

lake = Lake(Path(sys.argv[1]))
client = build_edgar_client(cfg, lake)
cik_map = CikMap.from_company_tickers(client.company_tickers())
http = httpx.Client(headers={"User-Agent": ua}, timeout=60)


def efts(query: str, start: date, end: date) -> Counter[int]:
    for attempt in range(4):  # full-text search answers an occasional HTTP 500
        time.sleep(0.12 + 2.0 * attempt)
        resp = http.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": f'"{query}"', "forms": "10-K,10-Q", "dateRange": "custom",
                    "startdt": start.isoformat(), "enddt": end.isoformat()},
        )  # fmt: skip
        if resp.status_code < 500:
            break
    resp.raise_for_status()
    found: Counter[int] = Counter()
    body = resp.json()
    if "hits" not in body:  # an error answer for an unusual query: no candidates from search
        print(f"   (search for {query!r} gave no result list: {str(body)[:120]})")
        return found
    for hit in body["hits"]["hits"]:
        for cik in hit["_source"].get("ciks", []):
            found[int(cik)] += 1
    return found


def profile(cik: int, start: date, end: date) -> tuple[list[str], int, str]:
    subs = client.submissions(cik)
    sec_names = [subs.get("name", "")] + [f["name"] for f in subs.get("formerNames", [])]
    tables = [subs["filings"]["recent"]]
    for page in subs["filings"].get("files", []):
        lo, hi = date.fromisoformat(page["filingFrom"]), date.fromisoformat(page["filingTo"])
        if lo <= end and hi >= start:
            tables.append(client.submissions_page(page["name"]))
    n = 0
    for t in tables:
        for form, filed in zip(t["form"], t["filingDate"], strict=True):
            if form in PERIODIC and start <= date.fromisoformat(filed) <= end:
                n += 1
    return sec_names, n, ",".join(subs.get("tickers", [])) or "-"


for sym in former:
    rows = iv[iv["symbol"] == sym]
    start = max(min(rows["start_date"]), WINDOW_FROM)
    ends = [e for e in rows["end_date"] if e is not None and e == e]
    end = max(ends) if len(ends) == len(rows) else TODAY
    log_name = names.get(sym) or sym
    cands: list[tuple[int, str]] = []
    if (c := cik_map.cik(sym)) is not None:
        cands.append((c, "tickers"))
    query = " ".join(w for w in re.sub(r"[,.]", " ", log_name).split() if w.lower() not in STOP)
    for c, _n in efts(query or log_name, start, end).most_common(4):
        if c not in [x for x, _ in cands]:
            cands.append((c, "efts"))
    print(f"== {sym} | {log_name!r} | window {start}..{end}")
    for c, how in cands:
        sec_names, n, tick = profile(c, start, end)
        score = name_score(log_name, sec_names)
        ok = "OK " if score >= 0.6 and n >= 1 else "-- "
        print(f"   {ok}cik={c} via={how} score={score:.2f} periodic_in_window={n} "
              f"tickers_now={tick} sec_name={sec_names[0]!r} former={sec_names[1:4]}")
