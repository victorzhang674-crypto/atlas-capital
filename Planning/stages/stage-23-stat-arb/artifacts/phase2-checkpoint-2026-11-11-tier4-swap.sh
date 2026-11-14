#!/bin/zsh
# Phase 2 checkpoint, Tier 4: put the tables rebuilt on a scratch copy of the main lake (reused
# tickers purged, universe synced, factor cache migrated and rebuilt, risk model rebuilt) into
# the main lake, with the scratch copy's _meta.json. The old tables are kept in a dated backup
# under <lake>/cache/ (copied first; nothing is deleted before the copy is checked), then each
# table directory is replaced by the rebuilt one and compared file by file (SHA-256).
#   zsh <this script> <main lake> <scratch lake> <output file>
set -u
LAKE=$1; SCRATCH=$2; OUT=$3
TABLES=(prices_daily corporate_actions index_membership instruments factor_exposures factor_returns risk_model_snapshots)
BACKUP=$LAKE/cache/backup-phase2-tier4-2026-11-14
digest() { (cd $1 && find . -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-16) }
{
  echo "=== swap into $LAKE from $SCRATCH, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -e $BACKUP ]; then echo "STOP: $BACKUP exists already"; echo "swap exit 1"; exit 1; fi
  mkdir -p $BACKUP || { echo "swap exit 1"; exit 1; }
  for t in $TABLES; do
    cp -Rp $LAKE/curated/$t $BACKUP/$t || { echo "backup of $t failed"; echo "swap exit 1"; exit 1; }
    [ "$(digest $LAKE/curated/$t)" = "$(digest $BACKUP/$t)" ] || { echo "backup of $t differs"; echo "swap exit 1"; exit 1; }
    echo "backed up $t: $(du -sh $BACKUP/$t | cut -f1) digest $(digest $BACKUP/$t)"
  done
  cp -p $LAKE/curated/_meta.json $BACKUP/_meta.json
  for t in $TABLES; do
    rm -rf $LAKE/curated/$t.new && cp -Rp $SCRATCH/curated/$t $LAKE/curated/$t.new || { echo "copy of $t failed"; echo "swap exit 1"; exit 1; }
    [ "$(digest $LAKE/curated/$t.new)" = "$(digest $SCRATCH/curated/$t)" ] || { echo "copy of $t differs"; echo "swap exit 1"; exit 1; }
  done
  for t in $TABLES; do
    mv $LAKE/curated/$t $LAKE/curated/$t.old && mv $LAKE/curated/$t.new $LAKE/curated/$t && rm -rf $LAKE/curated/$t.old
    echo "replaced $t: digest now $(digest $LAKE/curated/$t) (scratch $(digest $SCRATCH/curated/$t))"
  done
  # factor_exposures is at schema version 2 in the scratch copy: its _meta.json record goes too
  cp -p $SCRATCH/curated/_meta.json $LAKE/curated/_meta.json.new && mv $LAKE/curated/_meta.json.new $LAKE/curated/_meta.json
  cmp -s $LAKE/curated/_meta.json $SCRATCH/curated/_meta.json && echo "_meta.json: replaced by the scratch copy's (old one in the backup)" || echo "_meta.json: DIFFERS from the scratch copy's"
  for kind in factors risk_model; do
    cp -p $SCRATCH/runs/$kind/build.log $LAKE/runs/$kind/build-tier4-2026-11-14.log && echo "build log kept: runs/$kind/build-tier4-2026-11-14.log"
  done
  echo "swap exit 0"
} > $OUT 2>&1
