#!/bin/zsh
# Stage 29 follow-up 7: put the tables of the full data sync on the scratch copy of the main lake
# (main_lake_followup7.py) into the main lake, with the EDGAR and FRED answers the sync fetched
# (every regular file under the copy's raw/) and the sync checkpoint file. Run in the owner's
# Terminal panel (the sandbox cannot write the main checkout). Adapted from
# main_lake_followup6_swap.sh:
#   zsh <this script> <main lake> <scratch lake> <output file> "<table> <table> ..."
# The old tables, _meta.json, sync_state.json and every replaced raw file are kept in a dated backup under
# <lake>/cache/ (copied and checked before anything is replaced); each table directory is then
# replaced by the rebuilt one and compared with it (SHA-256 of every file).
set -u
LAKE=$1; SCRATCH=$2; OUT=$3
TABLES=(${=4})
BACKUP=$LAKE/cache/backup-stage29-followup7-2026-11-25
digest() { (cd $1 && find . -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-16) }
{
  echo "=== swap into $LAKE from $SCRATCH, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -e $BACKUP ]; then echo "STOP: $BACKUP exists already"; echo "swap exit 1"; exit 1; fi
  mkdir -p $BACKUP/raw || { echo "swap exit 1"; exit 1; }
  for t in $TABLES; do
    cp -Rp $LAKE/curated/$t $BACKUP/$t || { echo "backup of $t failed"; echo "swap exit 1"; exit 1; }
    [ "$(digest $LAKE/curated/$t)" = "$(digest $BACKUP/$t)" ] || { echo "backup of $t differs"; echo "swap exit 1"; exit 1; }
    echo "backed up $t: $(du -sh $BACKUP/$t | cut -f1) digest $(digest $BACKUP/$t)"
  done
  cp -p $LAKE/curated/_meta.json $BACKUP/_meta.json
  cp -p $LAKE/cache/sync_state.json $BACKUP/sync_state.json
  for t in $TABLES; do
    rm -rf $LAKE/curated/$t.new && cp -Rp $SCRATCH/curated/$t $LAKE/curated/$t.new || { echo "copy of $t failed"; echo "swap exit 1"; exit 1; }
    [ "$(digest $LAKE/curated/$t.new)" = "$(digest $SCRATCH/curated/$t)" ] || { echo "copy of $t differs"; echo "swap exit 1"; exit 1; }
  done
  for t in $TABLES; do
    mv $LAKE/curated/$t $LAKE/curated/$t.old && mv $LAKE/curated/$t.new $LAKE/curated/$t && rm -rf $LAKE/curated/$t.old
    echo "replaced $t: digest now $(digest $LAKE/curated/$t) (scratch $(digest $SCRATCH/curated/$t))"
  done
  cp -p $SCRATCH/cache/sync_state.json $LAKE/cache/sync_state.json && cmp -s $SCRATCH/cache/sync_state.json $LAKE/cache/sync_state.json && echo "sync_state.json: replaced by the scratch copy's (old one in the backup)"
  cmp -s $LAKE/curated/_meta.json $SCRATCH/curated/_meta.json && echo "_meta.json: identical to the scratch copy's" || echo "_meta.json: DIFFERS from the scratch copy's"
  # the EDGAR answers the scratch copy fetched are regular files (the rest are links to the main archive)
  added=0; replaced=0
  for f in $(cd $SCRATCH/raw && find . -type f); do
    if [ -e $LAKE/raw/$f ]; then
      mkdir -p $BACKUP/raw/$(dirname $f) && cp -p $LAKE/raw/$f $BACKUP/raw/$f && replaced=$((replaced+1))
    else
      added=$((added+1))
    fi
    mkdir -p $LAKE/raw/$(dirname $f) && cp -p $SCRATCH/raw/$f $LAKE/raw/$f || { echo "raw copy of $f failed"; echo "swap exit 1"; exit 1; }
    cmp -s $SCRATCH/raw/$f $LAKE/raw/$f || { echo "raw $f differs"; echo "swap exit 1"; exit 1; }
  done
  echo "raw: $added files added, $replaced replaced (old ones in the backup)"
  for kind in factors risk_model; do
    cp -p $SCRATCH/runs/$kind/build.log $LAKE/runs/$kind/build-stage29-followup7-2026-11-25.log && echo "build log kept: runs/$kind/build-stage29-followup7-2026-11-25.log"
  done
  echo "swap exit 0"
} > $OUT 2>&1
echo "swap finished: $(tail -1 $OUT)"
