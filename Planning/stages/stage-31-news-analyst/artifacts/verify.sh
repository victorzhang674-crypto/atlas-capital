#!/bin/zsh
# Stage 31 verification: run from the worktree root; each output goes to artifacts/<name>.txt
# with its exit code on the last line.
A=Planning/stages/stage-31-news-analyst/artifacts
export UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 UV_CACHE_DIR=$TMPDIR/uv-cache-s31
run() { local name=$1; shift; echo "\$ $*" > $A/$name.txt; "$@" >> $A/$name.txt 2>&1; local rc=$?; echo "" >> $A/$name.txt; echo "exit $rc" >> $A/$name.txt; echo "$name exit $rc"; }
echo "HEAD $(git rev-parse HEAD)"
run verify-brief uv run pytest tests/unit/agents/research/test_news.py -q -p no:cacheprovider
run pytest-full uv run pytest -q -p no:cacheprovider
run make-check make check
run test-integration make test-integration
run coverage uv run pytest tests/unit/agents/research tests/unit/agents/test_agents_finalize.py tests/unit/agents/test_agents_single_evidence_id.py tests/unit/core/types/test_types_news.py -q -p no:cacheprovider --cov=src/atlas/agents/tools --cov=src/atlas/agents/research --cov=src/atlas/agents/evidence.py --cov-report=term-missing
