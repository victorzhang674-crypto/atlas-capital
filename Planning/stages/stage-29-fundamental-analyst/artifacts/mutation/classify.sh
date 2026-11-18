#!/bin/bash
# Stage 29 follow-up (audit A9): run each mutant's tests in a plain process (no fork), because
# mutmut 3.8.1's forked workers end with SIGTRAP (-5, "suspicious") on this Mac whenever the
# tests pass. MUTANT_UNDER_TEST is set before import, so import-time code is mutated too.
# usage: classify.sh LISTFILE OUTFILE  -> "<mutant> <pytest exit code>" per line
W=${W:?set W to the worktree root}  # the project the mutants are tested with
cd "$TMPDIR/s29/mutcopy/mutants"
export UV_CACHE_DIR=$TMPDIR/uv-cache-s29 UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=1 PYTHONPATH=$PWD/src W
: > "$2"
one() {
  MUTANT_UNDER_TEST=$1 uv run --project "$W" --with mutmut==3.8.1 python -m pytest -q -p no:cacheprovider -x \
    tests/unit/agents/research/ tests/unit/data/test_data_slice.py tests/unit/cli/test_cli_data_slice.py \
    -m "not llm and not live" > /dev/null 2>&1
  echo "$1 $?"
}
export -f one
xargs -P 4 -n 1 bash -c 'one "$0"' < "$1" >> "$2"
echo done
