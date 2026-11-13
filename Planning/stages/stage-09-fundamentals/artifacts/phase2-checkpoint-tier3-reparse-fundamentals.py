"""Phase 2 checkpoint (Tier 2 item 7, Tier 3 item 2): store the facts of the tags the
concept map gained, from the companyfacts JSON already archived in the lake.

No download: the EDGAR client is given an HTTP transport that answers 404, so only archived
files (`<lake>/raw/sec/`) are read; a company whose archive is missing is reported, not
fetched. The companies are the symbols `fundamentals_facts` already holds. The ingest is the
normal `ingest_fundamentals` (an upsert by primary key): rows of the tags stored before are
written again unchanged, rows of the new tags are added.

Run from the repo root (config paths are relative to it); the lake is `data.lake_path`
(`ATLAS__DATA__LAKE_PATH` overrides it):

    uv run python <this file>

It prints the row counts before and after, and checks that every row stored before is still
there with the same values.
"""

import sys

import httpx
import pandas as pd

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals import ingest_fundamentals, load_concept_map
from atlas.data.fundamentals.edgar_client import EdgarClient, build_cik_map
from atlas.data.fundamentals.parser import FUNDAMENTALS_FACTS


def offline(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text="offline re-parse: not in the archive")


config = load_config("dev")
lake = Lake(config.data.lake_path)
print(f"lake: {lake.root}")
before = lake.read_table(FUNDAMENTALS_FACTS.name)
symbols = sorted(set(before["symbol"]))
print(f"before: {len(before)} rows, {len(symbols)} symbols, {before['tag'].nunique()} tags")

concept_map = load_concept_map(config.data.fundamentals.xbrl_map_file)
client = EdgarClient(
    config.data.fundamentals,
    user_agent="offline re-parse (no request is sent)",
    lake=lake,
    http=httpx.Client(transport=httpx.MockTransport(offline)),
    sleep=lambda seconds: None,
)
cik_map = build_cik_map(client, config, refresh=False)
stored_cik = before.groupby("symbol")["cik"].first().to_dict()
moved = [s for s in symbols if cik_map.cik(s) != stored_cik[s]]
if moved:
    print(f"STOP: the CIK of {moved[:10]} differs from the one stored; nothing written")
    sys.exit(1)

report = ingest_fundamentals(
    lake, client, symbols, cik_map=cik_map, config=config, concept_map=concept_map, refresh=False
)
print(
    f"ingest: {report.rows} rows upserted, {len(report.symbols_ok)} symbols ok, "
    f"{len(report.symbols_failed)} failed, {report.fallback_rows} rows without an acceptance time"
)
for symbol, reason in report.failure_reasons.items():
    print(f"  failed {symbol}: {reason}")

after = lake.read_table(FUNDAMENTALS_FACTS.name)
print(f"after: {len(after)} rows, {after['symbol'].nunique()} symbols, {after['tag'].nunique()} tags")
key = list(FUNDAMENTALS_FACTS.primary_key)
old_tags = set(zip(before["taxonomy"], before["tag"], strict=True))
is_old = [pair in old_tags for pair in zip(after["taxonomy"], after["tag"], strict=True)]
kept = after[is_old].sort_values(key, kind="stable").reset_index(drop=True)
want = before.sort_values(key, kind="stable").reset_index(drop=True)
same = kept[want.columns].equals(want)
print(f"rows of the tags stored before: {len(kept)} (before: {len(want)}); identical: {same}")
new = after[[not flag for flag in is_old]]
print(f"rows of new tags: {len(new)}, for {new['symbol'].nunique()} symbols")
print(new.groupby("tag").agg(rows=("value", "size"), symbols=("symbol", "nunique")).to_string())
if not same:
    merged = want.merge(kept, on=key, how="outer", suffixes=("_before", "_after"), indicator=True)
    print(merged["_merge"].value_counts().to_string())
    sys.exit(1)
