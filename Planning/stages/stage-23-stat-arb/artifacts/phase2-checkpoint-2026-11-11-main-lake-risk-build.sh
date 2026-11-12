#!/bin/zsh
# Phase 2 checkpoint, Tier 1 item 4, second run: only the risk-model build on the main lake
# (the factor cache finished in the first run; the first risk-model run was killed, exit 137,
# before writing anything; the memory fix is in `load_cross_sections`). Run from the main
# checkout:   caffeinate -is zsh <this script> <output file>
# Progress: tail data/runs/risk_model/build.log.
OUT=$1
export COLUMNS=160 NO_COLOR=1 LOKY_MAX_CPU_COUNT=4
{
  echo "=== start $(date -u +%FT%TZ) on $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
  echo "=== uv run atlas risk-model build --from 2017 --to 2026"
  s=$(date +%s)
  uv run atlas risk-model build --from 2017 --to 2026 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https"
  echo "risk-model exit ${pipestatus[1]}; seconds $(( $(date +%s) - s ))"
  echo "=== end $(date -u +%FT%TZ)"
  git status --short
} > $OUT 2>&1
echo "main-lake risk-model build finished: $(grep -E 'exit [0-9]' $OUT | tr '\n' ' ')"
