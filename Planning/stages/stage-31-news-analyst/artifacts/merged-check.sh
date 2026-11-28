#!/bin/zsh
# Stage 31: checks on the stage branch with origin/main merged in (run from the worktree root).
A=Planning/stages/stage-31-news-analyst/artifacts
export UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 UV_CACHE_DIR=$TMPDIR/uv-cache-s31
run() { local name=$1; shift; echo "\$ $*" > $A/$name.txt; "$@" >> $A/$name.txt 2>&1; local rc=$?; echo "" >> $A/$name.txt; echo "exit $rc" >> $A/$name.txt; echo "$name exit $rc"; }
echo "HEAD $(git rev-parse HEAD)"
run merged-pytest uv run pytest -q -p no:cacheprovider
run merged-make-check make check
run merged-integration make test-integration
