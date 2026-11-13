"""Phase 2 checkpoint, Tier 3 item 3: do the reference tests catch deliberate mistakes in the
row-based fundamentals pipeline? Each mutant changes one line of `mapping.py` / `pit.py`, runs
`tests/unit/data/fundamentals/test_fundamentals_reference.py` and expects a failure. The file
is restored after every mutant (also on Ctrl-C / SIGTERM). Run from the repo root:

    uv run python Planning/stages/stage-09-fundamentals/artifacts/phase2-checkpoint-tier3-mutate.py
"""

import signal
import subprocess
import sys
from pathlib import Path

MAPPING = Path("src/atlas/data/fundamentals/mapping.py")
PIT = Path("src/atlas/data/fundamentals/pit.py")
TEST = "tests/unit/data/fundamentals/test_fundamentals_reference.py"

MUTANTS: list[tuple[str, Path, str, str]] = [
    (
        "earliest filing of a period wins",
        MAPPING,
        "            for i in reversed(order):  # the latest filing of a period is met first",
        "            for i in order:  # MUTANT",
    ),
    (
        "last alternative with a value wins",
        MAPPING,
        "            out.setdefault(key, row)",
        "            out[key] = row",
    ),
    (
        "combined row cites the earlier input on a tie",
        MAPPING,
        "            if (row[4], row[5]) >= (latest[4], latest[5]):",
        "            if (row[4], row[5]) > (latest[4], latest[5]):",
    ),
    (
        "strict sum keeps periods one input lacks",
        MAPPING,
        "        if label is not None and len(rows) != len(frames):",
        "        if label is not None and len(rows) > len(frames):",
    ),
    (
        "derived quarter cites the later row on a tie",
        PIT,
        "        if row[0] > best[0]:",
        "        if row[0] >= best[0]:",
    ),
    (
        "derived quarters beat reported ones",
        PIT,
        "    quarters = _first_per_end([*reported, *_chain_differences(flows, low, high)])",
        "    quarters = _first_per_end([*_chain_differences(flows, low, high), *reported])",
    ),
    (
        "year-to-date chains ignore the start date",
        PIT,
        "        if prev[0] != cur[0] or not low <= (cur[1] - prev[1]).days <= high:",
        "        if not low <= (cur[1] - prev[1]).days <= high:",
    ),
    (
        "Q4 derived although the quarters do not tile the year",
        PIT,
        "                    if not low <= (year[1] - q3[1]).days <= high:",
        "                    if not (year[1] - q3[1]).days <= high:",
    ),
    (
        "four quarters beat the fiscal year in a TTM",
        PIT,
        'key=lambda r: (r[1], r[6] != "annual")',
        'key=lambda r: (r[1], r[6] == "annual")',
    ),
    (
        "TTM sums three quarters",
        PIT,
        "                    value = ((q1[2] + q2[2]) + q3[2]) + q4[2]",
        "                    value = (q1[2] + q2[2]) + q3[2]",
    ),
    (
        "history takes the earlier of the two availability times",
        PIT,
        "        available = max(row[3] for row in (quarter, total) if row is not None)",
        "        available = min(row[3] for row in (quarter, total) if row is not None)",
    ),
    (
        "latest balance is the earliest instant",
        PIT,
        "    _, end, _, value, available, accn, tag = instants[-1]",
        "    _, end, _, value, available, accn, tag = instants[0]",
    ),
]

ORIGINAL = {path: path.read_text() for path in (MAPPING, PIT)}


def restore(*_: object) -> None:
    for path, text in ORIGINAL.items():
        path.write_text(text)


def stop(*_: object) -> None:
    restore()
    sys.exit(130)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

caught = 0
try:
    for name, path, old, new in MUTANTS:
        text = ORIGINAL[path]
        if text.count(old) != 1:
            print(f"SKIPPED (text not found once): {name}")
            continue
        path.write_text(text.replace(old, new))
        run = subprocess.run(
            ["uv", "run", "pytest", "-x", "-q", "-p", "no:cacheprovider", TEST],
            capture_output=True,
            text=True,
            check=False,
        )
        restore()
        killed = run.returncode != 0
        caught += killed
        print(f"{'caught  ' if killed else 'SURVIVED'}: {name}", flush=True)
finally:
    restore()
print(f"{caught}/{len(MUTANTS)} mutants caught")
