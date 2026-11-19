#!/bin/zsh
# Stage 29 follow-up 2 (E6): add the `filing_text` table, backfilled in a scratch lake by
# `atlas data sync --datasets filings`, to the main lake. Run in the owner's Terminal panel
# (the sandbox cannot write the main checkout):
#   zsh <this script> <scratch lake (absolute)> <output file>
# Adds one new table; changes no existing one. Backs up _meta.json and the sync state first.
set -u
SRC=$1
OUT=$2
LAKE=$HOME/Projects/atlas-capital/data
BACKUP=$LAKE/cache/backup-stage29-filing-text-2026-11-18
{
  echo "=== check"
  test -d "$SRC/curated/filing_text" || { echo "no $SRC/curated/filing_text"; exit 1; }
  test ! -e "$LAKE/curated/filing_text" || { echo "main lake already has filing_text"; exit 1; }
  echo "=== backup to $BACKUP"
  mkdir -p "$BACKUP" && cp -p "$LAKE/curated/_meta.json" "$LAKE/cache/sync_state.json" "$BACKUP/" || exit 1
  shasum -a 256 "$LAKE/curated/_meta.json" "$BACKUP/_meta.json" "$LAKE/cache/sync_state.json" "$BACKUP/sync_state.json"
  echo "=== copy the table"
  cp -R "$SRC/curated/filing_text" "$LAKE/curated/filing_text.new" && mv "$LAKE/curated/filing_text.new" "$LAKE/curated/filing_text" || exit 1
  echo "=== merge _meta.json and the sync state (filing_text entries only)"
  python3 - "$SRC" "$LAKE" <<'PY' || exit 1
import json, os, sys, tempfile
src, lake = sys.argv[1], sys.argv[2]
def merge(path_src, path_dst, outer, key):
    with open(path_src) as f:
        entry = json.load(f)[outer][key]
    with open(path_dst) as f:
        data = json.load(f)
    if key in data[outer]:
        raise SystemExit(f"{path_dst} already has {outer}.{key}")
    data[outer][key] = entry
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path_dst))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path_dst)
    print(f"{path_dst}: added {outer}.{key}")
merge(f"{src}/curated/_meta.json", f"{lake}/curated/_meta.json", "tables", "filing_text")
merge(f"{src}/cache/sync_state.json", f"{lake}/cache/sync_state.json", "steps", "filing_text")
PY
  echo "=== verify: the copied files equal the scratch lake's"
  ( cd "$SRC/curated/filing_text" && find . -type f -exec shasum -a 256 {} + | sort ) > /tmp/filing_text_src.sha
  ( cd "$LAKE/curated/filing_text" && find . -type f -exec shasum -a 256 {} + | sort ) > /tmp/filing_text_dst.sha
  if cmp -s /tmp/filing_text_src.sha /tmp/filing_text_dst.sha; then echo "files EQUAL ($(wc -l < /tmp/filing_text_dst.sha) files)"; else echo "files DIFFERENT"; exit 1; fi
  echo "swap exit 0"
} > "$OUT" 2>&1
echo "swap finished: $(tail -1 "$OUT")"
