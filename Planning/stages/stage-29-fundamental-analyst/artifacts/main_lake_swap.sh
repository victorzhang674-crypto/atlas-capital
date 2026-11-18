#!/bin/zsh
# Stage 29 follow-up (audit A5): put the tables rebuilt on a scratch copy of the main lake (the
# fundamentals re-parsed with the receivables concept, then the factor and risk stores rebuilt
# because their input fingerprints count fact rows) into the main lake. The old tables are kept
# in a dated backup under <lake>/cache/ (copied and checked before anything is replaced), then
# each table directory is replaced by the rebuilt one and compared file by file (SHA-256).
# Adapted from the Phase 2 one-pass swap (Stage 23 artifacts).
#   zsh <this script> <main lake> <scratch lake> <output file> "<table> <table> ..."
set -u
LAKE=$1; SCRATCH=$2; OUT=$3
TABLES=(${=4})
BACKUP=$LAKE/cache/backup-stage29-receivables-2026-11-18
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
  cmp -s $LAKE/curated/_meta.json $SCRATCH/curated/_meta.json && echo "_meta.json: identical to the scratch copy's" || echo "_meta.json: DIFFERS from the scratch copy's"
  for kind in factors risk_model; do
    cp -p $SCRATCH/runs/$kind/build.log $LAKE/runs/$kind/build-stage29-receivables-2026-11-18.log && echo "build log kept: runs/$kind/build-stage29-receivables-2026-11-18.log"
  done
  echo "swap exit 0"
} > $OUT 2>&1
