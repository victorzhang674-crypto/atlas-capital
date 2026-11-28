#!/bin/zsh
# Stage 19 follow-up (ETF ridge 1e-2, 2026-11-28): put the two risk-model tables rebuilt on a scratch
# copy into the main lake. The old tables are kept in a dated backup under <lake>/cache/ (copied and
# checked first; nothing is deleted before the copy is verified), then each table directory is
# replaced by the rebuilt one and compared file by file (SHA-256).
#   zsh <this script> <main lake> <scratch lake> <output file>
set -u
LAKE=$1; SCRATCH=$2; OUT=$3
TABLES=(factor_returns risk_model_snapshots)
BACKUP=$LAKE/cache/backup-etf-ridge-2026-11-28
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
  python3 - $LAKE/curated/_meta.json $SCRATCH/curated/_meta.json <<'PY' || { echo "meta entries differ"; echo "swap exit 1"; exit 1; }
import json, sys
a = json.load(open(sys.argv[1]))["tables"]; b = json.load(open(sys.argv[2]))["tables"]
for t in ("factor_returns", "risk_model_snapshots"):
    assert a[t] == b[t], (t, a[t], b[t])
print("_meta.json entries of both tables are identical in the lake and the scratch copy")
PY
  for t in $TABLES; do
    rm -rf $LAKE/curated/$t.new && cp -Rp $SCRATCH/curated/$t $LAKE/curated/$t.new || { echo "copy of $t failed"; echo "swap exit 1"; exit 1; }
    [ "$(digest $LAKE/curated/$t.new)" = "$(digest $SCRATCH/curated/$t)" ] || { echo "copy of $t differs"; echo "swap exit 1"; exit 1; }
  done
  for t in $TABLES; do
    mv $LAKE/curated/$t $LAKE/curated/$t.old && mv $LAKE/curated/$t.new $LAKE/curated/$t && rm -rf $LAKE/curated/$t.old
    echo "replaced $t: digest now $(digest $LAKE/curated/$t) (scratch $(digest $SCRATCH/curated/$t))"
  done
  [ -f $SCRATCH/runs/risk_model/build.log ] && cp -p $SCRATCH/runs/risk_model/build.log $LAKE/runs/risk_model/build-etf-ridge-2026-11-28.log && echo "build log kept: runs/risk_model/build-etf-ridge-2026-11-28.log"
  echo "swap exit 0"
} > $OUT 2>&1
