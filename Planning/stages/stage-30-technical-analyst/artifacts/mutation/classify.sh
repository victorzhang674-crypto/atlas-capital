#!/bin/bash
# Stage 30: re-run each mutant's tests in a plain process (no fork): mutmut 3.8.1's forked
# workers end with SIGTRAP (-5, "suspicious") on this Mac whenever the tests pass.
# No network: the worktree's venv Python with mutmut installed (--no-deps) in $TMPDIR/s30/mutlib.
# The mutants' source is commit 7d9f6da (before the audit's snapshot fix), so the one test of
# that fix is deselected here.
# usage: W=<worktree> classify.sh LISTFILE OUTFILE
#   -> "<mutant> <pytest exit code> <last line of pytest output>" per line (1 = killed, 0 = survived)
W=${W:?set W to the worktree root}
cd "$TMPDIR/s30/mutcopy/mutants"
export LOKY_MAX_CPU_COUNT=1 PYTHONPATH=$PWD/src:$TMPDIR/s30/mutlib W
: > "$2"
one() {
  out=$(MUTANT_UNDER_TEST=$1 "$W/.venv/bin/python" -m pytest -q -p no:cacheprovider -x \
    tests/unit/agents/research/test_technical.py tests/unit/agents/research/test_technical_tools.py \
    tests/unit/agents/research/test_technical_tools_pit.py tests/unit/agents/research/test_research_checks.py \
    tests/unit/agents/research/test_fundamental.py tests/unit/agents/research/test_technical_edges.py tests/unit/core/types/test_types_technical.py \
    --deselect=tests/unit/agents/research/test_technical_edges.py::test_a_snapshot_of_a_symbol_without_bars_fails \
    $EXTRA 2>&1)
  rc=$?
  echo "$1 $rc $(echo "$out" | grep -E 'passed|failed|error' | tail -1)"
}
export -f one
xargs -P 4 -n 1 bash -c 'one "$0"' < "$1" >> "$2"
echo done
