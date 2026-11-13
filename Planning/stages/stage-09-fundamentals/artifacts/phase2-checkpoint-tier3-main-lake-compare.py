"""Phase 2 checkpoint, Tier 3 item 3: compare the per-company fundamentals pipeline with the
pandas implementation it replaced, one for one, on every company of a real lake (read only).

    ATLAS__DATA__LAKE_PATH=<lake> uv run python <this file> 2017-06-30 2021-12-31 2026-10-30

For each company with stored facts and each as-of day: every concept's resolved periods,
quarters and TTM frames, `concept_values` and `concept_history` must be equal (values, dates,
filings, tags, dtypes). Prints the counts and exits 1 on the first difference.
"""

import sys
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests" / "unit" / "data" / "fundamentals"))

import pandas as pd  # noqa: E402
import pandas_reference as ref  # noqa: E402

from atlas.core.config import load_config  # noqa: E402
from atlas.core.pit import AsOf  # noqa: E402
from atlas.core.storage import Lake  # noqa: E402
from atlas.data.fundamentals import (  # noqa: E402
    FUNDAMENTALS_FACTS,
    concept_history,
    concept_values,
    known_facts,
    load_concept_map,
    quarterly_values,
    resolve_concept,
    ttm_values,
)


def same(got: pd.DataFrame, want: pd.DataFrame, what: str) -> None:
    assert list(got.dtypes) == list(want.dtypes), f"{what}: dtypes {got.dtypes} vs {want.dtypes}"
    pd.testing.assert_frame_equal(got, want, check_exact=True, obj=what)


def main() -> int:
    days = [date.fromisoformat(a) for a in sys.argv[1:]]
    cfg = load_config("dev")
    lake = Lake(cfg.data.lake_path)
    cmap = load_concept_map(cfg.data.fundamentals.xbrl_map_file)
    settings = cfg.data.fundamentals
    facts = lake.read_table(FUNDAMENTALS_FACTS.name)
    companies = dict(tuple(facts.groupby("symbol", sort=True)))
    print(f"{len(facts)} facts, {len(companies)} companies, as-of days {[str(d) for d in days]}")
    counts = {"companies": 0, "frames": 0, "values": 0, "history_rows": 0}
    seconds = {"reference": 0.0, "new": 0.0}
    for day in days:
        point = AsOf.for_session(day, config=cfg.calendar)
        for symbol, own in companies.items():
            known = known_facts(own, point)
            if known.empty:
                continue
            counts["companies"] += 1
            t0 = time.perf_counter()
            want_values = ref.concept_values(known, cmap, settings)
            want_history = ref.concept_history(known, cmap, list(cmap), settings)
            t1 = time.perf_counter()
            got_values = concept_values(known, cmap, settings)
            got_history = concept_history(known, cmap, list(cmap), settings)
            t2 = time.perf_counter()
            seconds["reference"] += t1 - t0
            seconds["new"] += t2 - t1
            where = f"{symbol} @ {day}"
            assert got_values == want_values, f"{where}: concept_values differ"
            counts["values"] += sum(v is not None for v in want_values.values())
            same(got_history, want_history, f"{where} history")
            counts["history_rows"] += len(want_history)
            for concept in cmap.values():
                want = ref.resolve_concept(known, concept)
                got = resolve_concept(known, concept)
                same(got, want, f"{where} resolve {concept.name}")
                counts["frames"] += 1
                if concept.kind == "flow":
                    same(
                        quarterly_values(got, settings),
                        ref.quarterly_values(want, settings),
                        f"{where} quarters {concept.name}",
                    )
                    same(
                        ttm_values(got, settings),
                        ref.ttm_values(want, settings),
                        f"{where} ttm {concept.name}",
                    )
                    counts["frames"] += 2
        print(f"{day}: ok so far: {counts}", flush=True)
    speed = seconds["reference"] / seconds["new"] if seconds["new"] else float("nan")
    print(
        f"concept_values + concept_history: reference {seconds['reference']:.1f} s, "
        f"new {seconds['new']:.1f} s ({speed:.0f}x)"
    )
    print(f"ALL EQUAL: {counts}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"DIFFERENCE: {exc}")
        raise SystemExit(1) from None
