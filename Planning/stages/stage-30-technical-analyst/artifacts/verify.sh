#!/bin/zsh
# Stage 30 verification: run from the worktree root; each output goes to artifacts/<name>.txt
# with its exit code on the last line.
A=Planning/stages/stage-30-technical-analyst/artifacts
export UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4
run() { local name=$1; shift; echo "\$ $*" > $A/$name.txt; "$@" >> $A/$name.txt 2>&1; local rc=$?; echo "" >> $A/$name.txt; echo "exit $rc" >> $A/$name.txt; echo "$name exit $rc"; }
echo "HEAD $(git rev-parse HEAD)"
run verify-brief uv run pytest tests/unit/agents/research/test_technical.py -q -p no:cacheprovider
run pytest-full uv run pytest -q -p no:cacheprovider
run make-check make check
run test-integration make test-integration
run coverage uv run pytest tests/unit/agents/research tests/unit/core/types/test_types_technical.py -q -p no:cacheprovider --cov=src/atlas/agents/tools/technical_tools.py --cov=src/atlas/agents/research/technical.py --cov=src/atlas/agents/research/checks.py --cov-report=term-missing
