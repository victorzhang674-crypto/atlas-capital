#!/bin/zsh
# Phase 3 checkpoint, Tier 4 (item 1, `docs/llm.md`): full checks. Run from the worktree root.
export UV_CACHE_DIR=${UV_CACHE_DIR:-$TMPDIR/uv-cache-p3} UV_NO_SYNC=1
A=Planning/stages/stage-28-langgraph-skeleton/artifacts; P=phase3-checkpoint-2026-11-18-tier4
run() { local out=$1; shift; "$@" > $A/$P-$out.txt 2>&1; echo "exit $?" >> $A/$P-$out.txt; }
run pytest uv run pytest -q -p no:cacheprovider
run make-check make check
run verify-docs uv run pytest tests/unit/test_repo_docs.py -q -p no:cacheprovider
run integration uv run pytest -m integration -q -rs -p no:cacheprovider
