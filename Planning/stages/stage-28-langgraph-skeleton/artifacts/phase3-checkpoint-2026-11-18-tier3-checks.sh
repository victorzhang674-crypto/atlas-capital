#!/bin/zsh
# Phase 3 checkpoint, Tier 3 (item 2, `atlas llm usage`): full checks. Run from the worktree root.
export UV_CACHE_DIR=${UV_CACHE_DIR:-$TMPDIR/uv-cache-p3} UV_NO_SYNC=1
A=Planning/stages/stage-28-langgraph-skeleton/artifacts; P=phase3-checkpoint-2026-11-18-tier3
run() { local out=$1; shift; "$@" > $A/$P-$out.txt 2>&1; echo "exit $?" >> $A/$P-$out.txt; }
run pytest uv run pytest -q -p no:cacheprovider
run make-check make check
run verify-llm uv run pytest tests/unit/llm -q -p no:cacheprovider
run verify-cli-llm uv run pytest tests/unit/cli/test_llm_cmd.py tests/unit/cli/test_llm_usage_cmd.py tests/unit/cli/test_cli_startup.py -q -p no:cacheprovider
run integration uv run pytest -m integration -q -rs -p no:cacheprovider
run coverage uv run pytest -q -p no:cacheprovider tests/unit/llm tests/unit/cli/test_llm_cmd.py tests/unit/cli/test_llm_usage_cmd.py --cov=src/atlas/llm --cov=src/atlas/cli --cov-report=term-missing
