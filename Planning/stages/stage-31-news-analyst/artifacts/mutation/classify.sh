#!/bin/bash
# Stage 31: re-run each mutant's tests in a plain process (no fork): mutmut 3.8.1's forked
# workers end with SIGTRAP ("suspicious") on this Mac whenever the tests pass.
# usage: W=<worktree> classify.sh LISTFILE OUTFILE  -> "<mutant> <pytest exit code> <summary>"
# (1 = killed, 0 = survived)
W=${W:?set W to the worktree root}
cd "$TMPDIR/s31/mutcopy/mutants"
export LOKY_MAX_CPU_COUNT=1 PYTHONPATH=$PWD/src:$TMPDIR/s31/mutlib W
: > "$2"
one() {
  out=$(MUTANT_UNDER_TEST=$1 "$W/.venv/bin/python" -m pytest -q -p no:cacheprovider -x \
    tests/unit/agents/research/test_news.py tests/unit/agents/research/test_news_tools.py \
    tests/unit/agents/research/test_news_tools_pit.py tests/unit/agents/research/test_news_config.py \
    $EXTRA 2>&1)
  rc=$?
  echo "$1 $rc $(echo "$out" | grep -E 'passed|failed|error' | tail -1)"
}
export -f one
xargs -P 4 -n 1 bash -c 'one "$0"' < "$1" >> "$2"
echo done
