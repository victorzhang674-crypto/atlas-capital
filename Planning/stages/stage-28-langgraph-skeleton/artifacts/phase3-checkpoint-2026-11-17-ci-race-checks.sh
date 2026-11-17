#!/bin/zsh
# Full checks after the kill-resume test fix (Phase 3 checkpoint, CI follow-up). Run from the worktree root.
export UV_CACHE_DIR=${UV_CACHE_DIR:-$TMPDIR/uv-cache-p3} UV_NO_SYNC=1
A=Planning/stages/stage-28-langgraph-skeleton/artifacts; P=phase3-checkpoint-2026-11-17-ci-race
for i in 1 2 3 4 5 6 7 8 9 10; do uv run pytest tests/unit/orchestration/test_orchestration_checkpoint.py -q -p no:cacheprovider -k killed 2>&1 | grep -E "passed|failed"; done > $A/$P-repeat.txt
uv run pytest -q -p no:cacheprovider > $A/$P-pytest.txt 2>&1; echo "exit $?" >> $A/$P-pytest.txt
make check > $A/$P-make-check.txt 2>&1; echo "exit $?" >> $A/$P-make-check.txt
