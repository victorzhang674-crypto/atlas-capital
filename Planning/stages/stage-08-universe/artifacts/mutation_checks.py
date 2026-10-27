"""Mutation checks for the Stage 08 tests that never had a red step (audit A5).

Each mutation injects one deliberate bug into the code a test guards, runs that test, and
expects it to FAIL; the file is then restored and its hash checked. A mutation named
"equivalent" removes a pure shortcut and is expected to survive (same result). Run from the
repo root:

    uv run python Planning/stages/stage-08-universe/artifacts/mutation_checks.py
"""

import hashlib
import subprocess
import sys
from pathlib import Path

UNI = Path("src/atlas/data/universe.py")
REF = Path("src/atlas/data/reference.py")
T = "tests/unit/data/test_universe.py::"

MUTATIONS = [
    (
        "members ignore start dates",
        UNI,
        'started = intervals["start_date"] <= day',
        'started = intervals["start_date"] <= date.max',
        [T + "test_acceptance_members_2019_06_03"],
    ),
    (
        "tradable uses every stored bar (no pit_filter, no guard)",
        UNI,
        "known = pit_filter(bars, point)\n        assert_no_lookahead(known, point)",
        "known = bars",
        [T + "test_acceptance_tradable_uses_only_data_before_as_of"],
    ),
    (
        "tradable returns members when no prices exist",
        UNI,
        "result = sorted(str(s) for s in stats.index[ok])",
        "result = sorted(names) if stats.empty else sorted(str(s) for s in stats.index[ok])",
        [T + "test_tradable_without_any_prices_is_empty"],
    ),
    (
        "duplicate constituent symbols accepted",
        REF,
        "    if dupes:\n",
        "    if False:\n",
        [T + "test_parse_constituents_guards"],
    ),
    (
        "missing constituent column accepted",
        REF,
        "    if missing:\n        raise DataError(f\"constituents table lacks",
        "    if False:\n        raise DataError(f\"constituents table lacks",
        [T + "test_parse_constituents_guards"],
    ),
    (
        "changes header not checked",
        REF,
        'if top[:6] != ["Effective Date", "Added", "Added", "Removed", "Removed", "Reason"]:',
        "if False:",
        [T + "test_parse_changes_unexpected_header_raises"],
    ),
    (
        "ETF that is an index member accepted",
        REF,
        "        if symbol in out:\n",
        "        if False:\n",
        [T + "test_build_instruments_rejects_etf_that_is_a_member"],
    ),
    (
        "bad log ticker not skipped",
        REF,
        "                except ValueError:\n                    continue",
        "                except KeyError:\n                    continue",
        [T + "test_build_instruments_skips_bad_log_ticker"],
    ),
    (
        "members_on empty shortcut removed (expected equivalent: same result)",
        UNI,
        "    if intervals.empty:\n        return []\n",
        "",
        [T + "test_empty_inputs_give_empty_lists"],
    ),
    (
        "tradable empty-members shortcut removed (needed: else the calendar is consulted)",
        UNI,
        "        if not names:\n            return []\n",
        "",
        [T + "test_empty_inputs_give_empty_lists"],
    ),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures = 0
    for name, path, old, new, tests in MUTATIONS:
        original = path.read_text()
        before = digest(path)
        if original.count(old) != 1:
            print(f"SETUP ERROR  {name}: pattern found {original.count(old)} times")
            failures += 1
            continue
        path.write_text(original.replace(old, new))
        try:
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            path.write_text(original)
        assert digest(path) == before, f"{path} not restored"
        summary = (run.stdout.strip().splitlines() or ["?"])[-1]
        verdict = "KILLED (test failed)" if run.returncode != 0 else "SURVIVED (test passed)"
        print(f"{verdict:24} {name}  [{summary}]")
        if run.returncode == 0 and "equivalent" not in name:
            failures += 1
    print(f"\n{len(MUTATIONS)} mutations; unexpected survivors or setup errors: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
