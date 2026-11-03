#!/bin/zsh
# Phase 1 checkpoint (2026-11-03): purge the reused tickers' rows from the main lake (owner:
# "Yes, purge them"): dry run, apply (backup first), dry run again (must show 0 rows).
W="${0:A:h}"
export ATLAS__DATA__LAKE_PATH="/Users/zzjsmacbookair/Projects/atlas-capital/data"
cd "${W}/../../../.." || exit 1
P="$W/phase1-checkpoint-2026-11-03-purge-reused-tickers.py"
echo "# purge of reused tickers · $(date '+%Y-%m-%d %H:%M %Z') · lake $ATLAS__DATA__LAKE_PATH"
for args in "" "--apply" ""; do
  echo; echo "\$ uv run python $P $args"
  uv run python "$P" ${=args} 2>&1 | grep -v membership_anomaly; rc=${pipestatus[1]}; echo "exit=$rc"
  [ $rc -eq 0 ] || exit $rc
done
