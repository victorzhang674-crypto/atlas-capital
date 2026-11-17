#!/bin/zsh
# Phase 3 checkpoint (Stages 24-28): the checks re-run on the unchanged tree (base 3900868).
# Usage: zsh phase3-checkpoint-2026-11-17-checks.sh   (from the repository root)
# Each command's output goes to its own file next to this script; the exit code is the last line.
A=Planning/stages/stage-28-langgraph-skeleton/artifacts
P=$A/phase3-checkpoint-2026-11-17
export UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4
run() {  # run <output file> <command...>
  out=$1; shift
  { echo "\$ $*"; date -u +"started %Y-%m-%dT%H:%M:%SZ"; } > $out
  "$@" >> $out 2>&1; rc=$?
  { date -u +"finished %Y-%m-%dT%H:%M:%SZ"; echo "exit $rc"; } >> $out
}
run $P-gate-pytest.txt uv run pytest -q
run $P-make-check.txt make check
run $P-verify-24-25-llm.txt uv run pytest tests/unit/llm -q
run $P-verify-26-agents.txt uv run pytest tests/unit/agents -q
run $P-verify-27-memory.txt uv run pytest tests/unit/agents/memory -q
run $P-verify-28-orchestration.txt uv run pytest tests/unit/orchestration -q
run $P-integration.txt uv run pytest -m integration -q -rs
echo done > $P-checks-done.txt
