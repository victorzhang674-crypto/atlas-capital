#!/bin/zsh
# Phase 2 checkpoint, Tier 1 item 4: build the factor cache and the risk model's stored history
# on the main lake. Run from the main checkout (the sandbox cannot write the main lake):
#   caffeinate -is zsh <this script> <output file>
# Both commands resume or replace cleanly: `factors build` skips month-ends already stored,
# `risk-model build` replaces the rows of the days it writes. Progress: tail
# data/runs/factors/build.log and data/runs/risk_model/build.log.
OUT=$1
export COLUMNS=160 NO_COLOR=1 LOKY_MAX_CPU_COUNT=4
{
  echo "=== start $(date -u +%FT%TZ) on $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
  echo "=== uv run atlas factors build --from 2015 --to 2026"
  s=$(date +%s)
  uv run atlas factors build --from 2015 --to 2026 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https"
  echo "factors exit ${pipestatus[1]}; seconds $(( $(date +%s) - s ))"
  echo "=== uv run atlas risk-model build --from 2017 --to 2026"
  s=$(date +%s)
  uv run atlas risk-model build --from 2017 --to 2026 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https"
  echo "risk-model exit ${pipestatus[1]}; seconds $(( $(date +%s) - s ))"
  echo "=== end $(date -u +%FT%TZ)"
  git status --short
} > $OUT 2>&1
echo "main-lake builds finished: $(grep -E 'exit [0-9]' $OUT | tr '\n' ' ')"
