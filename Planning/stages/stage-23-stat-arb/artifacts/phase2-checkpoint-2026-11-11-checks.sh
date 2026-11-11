#!/bin/zsh
# Phase 2 checkpoint: the full gate, each stage's Verify command, and the real-lake checks.
# Run from the repo root. Writes phase2-checkpoint-2026-11-11-*.txt next to this script.
A=Planning/stages/stage-23-stat-arb/artifacts
P=$A/phase2-checkpoint-2026-11-11
export UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 MPLCONFIGDIR=${TMPDIR:-/tmp}/mpl
make check > $P-make-check.txt 2>&1
echo "\nexit $?" >> $P-make-check.txt
: > $P-verify.txt
for pkg in features factors research risk_model metrics regime alpha statarb; do
  echo "=== uv run pytest tests/unit/quant/$pkg -q --durations=5" >> $P-verify.txt
  uv run pytest tests/unit/quant/$pkg -q --durations=5 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https" >> $P-verify.txt
  echo "exit ${pipestatus[1]}" >> $P-verify.txt
done
export ATLAS__DATA__LAKE_PATH=$HOME/Projects/atlas-capital/data
echo "=== uv run pytest -m integration -v -rs (real lake, read only)" > $P-integration.txt
uv run pytest -m integration -v -rs 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https" >> $P-integration.txt
echo "exit ${pipestatus[1]}" >> $P-integration.txt
uv run python -u $A/phase2-checkpoint-2026-11-11-profile-quality.py > $P-profile-quality.txt 2>&1
echo "exit $?" >> $P-profile-quality.txt
