"""Purge the rows another company left under six reused tickers (Phase 1 checkpoint, 2026-11-03).

Owner's words: "Yes, purge them". AA, ADT, NE, POM, SE and TE were synced on 2026-11-01 through
today's ticker map, i.e. through the company that took the ticker after the member left the
S&P 500. Their correct CIKs are in config/cik_overrides.yaml since 2026-11-02 and were synced
the same day. Removed here, per symbol (wrong CIK, from the 2026-11-01 sync):

- `fundamentals_facts` rows with that CIK;
- `events` rows `sec-<accn>` whose accession is in the wrong CIK's archived submissions
  (raw/sec/submissions/) and not in the right CIK's;
- `earnings_reactions` rows of those events;
- `events` rows from Yahoo's schedule (`source` yahoo): dates of today's ticker owner.

Prints what it would delete; deletes only with --apply, after copying the touched tables to
data/cache/backup-phase1-purge-2026-11-03/ (events, earnings_reactions, the six
fundamentals_facts partitions). Run from the main checkout: uv run python <this file> [--apply]
"""

import json
import shutil
import sys
from pathlib import Path

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.events import EARNINGS_REACTIONS, EVENTS
from atlas.data.fundamentals import FUNDAMENTALS_FACTS, load_cik_overrides

WRONG = {"AA": 1675149, "ADT": 1703056, "NE": 1895262, "POM": 1877971, "SE": 1703399, "TE": 1992243}
"""Symbol -> the CIK of the company that reused the ticker (what the 2026-11-01 sync stored)."""


def accessions(submissions: Path, cik: int) -> set[str]:
    found: set[str] = set()
    for path in sorted(submissions.glob(f"CIK{cik:010d}*.json")):
        body = json.loads(path.read_text())
        table = body["filings"]["recent"] if "filings" in body else body
        found |= set(table["accessionNumber"])
    return found


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    cfg = load_config()
    lake = Lake(cfg.data.lake_path)
    right = {o.symbol: o.cik for o in load_cik_overrides(cfg.data.fundamentals.cik_overrides_file)}
    submissions = lake.raw_dir / "sec" / "submissions"
    plan: list[tuple[str, str, list[tuple[str, str, object]], int]] = []
    for symbol, wrong in WRONG.items():
        assert right[symbol] not in (None, wrong), symbol
        bad = accessions(submissions, wrong) - accessions(submissions, right[symbol])
        events = lake.read_table(EVENTS.name, filters=[("symbol", "=", symbol)])
        sec = [e for e in events["event_id"] if e.startswith("sec-")]
        wrong_ids = sorted(e for e in sec if e[4:] in bad)
        facts = lake.read_table(
            FUNDAMENTALS_FACTS.name, filters=[("symbol", "=", symbol), ("cik", "=", wrong)]
        )
        yahoo = events[events["source"] == "yahoo"]
        reactions = lake.read_table(EARNINGS_REACTIONS.name, filters=[("symbol", "=", symbol)])
        n_reactions = int(reactions["event_id"].isin(wrong_ids).sum())
        plan += [
            (symbol, FUNDAMENTALS_FACTS.name, [("symbol", "=", symbol), ("cik", "=", wrong)], len(facts)),
            (symbol, EVENTS.name, [("symbol", "=", symbol), ("event_id", "in", wrong_ids)], len(wrong_ids)),
            (symbol, EARNINGS_REACTIONS.name, [("symbol", "=", symbol), ("event_id", "in", wrong_ids)], n_reactions),
            (symbol, EVENTS.name, [("symbol", "=", symbol), ("source", "=", "yahoo")], len(yahoo)),
        ]  # fmt: skip
        print(f"{symbol}: right CIK {right[symbol]}, wrong {wrong}: facts {len(facts)}, "
              f"sec events {len(wrong_ids)} of {len(sec)}, reactions {n_reactions}, "
              f"yahoo events {len(yahoo)}")
    print(f"total rows: {sum(n for *_, n in plan)}")
    if not apply:
        print("dry run: nothing deleted (pass --apply)")
        return 0
    backup = lake.cache_dir / "backup-phase1-purge-2026-11-03"
    if backup.exists():
        raise SystemExit(f"{backup} exists: refusing to overwrite an earlier backup")
    for name in (EVENTS.name, EARNINGS_REACTIONS.name):
        shutil.copytree(lake.table_path(name), backup / name)
    for symbol in WRONG:
        part = lake.table_path(FUNDAMENTALS_FACTS.name) / f"symbol={symbol}"
        shutil.copytree(part, backup / FUNDAMENTALS_FACTS.name / f"symbol={symbol}")
    print(f"backup: {backup}")
    for symbol, table, filters, expected in plan:
        if expected == 0:
            continue
        deleted = lake.delete_rows(table, filters)
        status = "ok" if deleted == expected else "MISMATCH"
        print(f"{status} {symbol} {table}: deleted {deleted} (expected {expected})")
        if deleted != expected:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
