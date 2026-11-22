#!/bin/zsh
# Stage 29 follow-up 3 (H4): replace the main lake's `filing_text` table with the one rebuilt
# in a scratch lake by `atlas data sync --datasets filings` (10-Ks and 10-Qs, the follow-up 3
# extraction). Run in the owner's Terminal panel (the sandbox cannot write the main checkout):
#   zsh <this script> <scratch lake (absolute)> <output file> [backup suffix, default 2026-11-22]
# (run twice: round 1 with the default suffix, the H8-H9 re-ingest with 2026-11-22b)
# The current table, _meta.json and the sync state are moved / copied to a dated backup first;
# no other table is touched.
set -u
SRC=$1
OUT=$2
LAKE=$HOME/Projects/atlas-capital/data
BACKUP=$LAKE/cache/backup-stage29-filing-text-${3:-2026-11-22}
{
  echo "=== check"
  test -d "$SRC/curated/filing_text" || { echo "no $SRC/curated/filing_text"; exit 1; }
  test -d "$LAKE/curated/filing_text" || { echo "main lake has no filing_text to replace"; exit 1; }
  test ! -e "$BACKUP" || { echo "backup $BACKUP already exists"; exit 1; }
  python3 - "$SRC" "$LAKE" <<'PY' || exit 1
import json, sys
src, lake = sys.argv[1], sys.argv[2]
a = json.load(open(f"{src}/curated/_meta.json"))["tables"]["filing_text"]
b = json.load(open(f"{lake}/curated/_meta.json"))["tables"]["filing_text"]
print("schema entry equal:", a == b)
if a != b:
    raise SystemExit(f"filing_text schema differs: {a} vs {b}")
PY
  echo "=== backup to $BACKUP"
  mkdir -p "$BACKUP" && cp -p "$LAKE/curated/_meta.json" "$LAKE/cache/sync_state.json" "$BACKUP/" || exit 1
  ( cd "$LAKE/curated/filing_text" && find . -type f -exec shasum -a 256 {} + | sort ) > "$BACKUP/filing_text.sha256"
  echo "=== copy the new table, then move the old one into the backup"
  cp -R "$SRC/curated/filing_text" "$LAKE/curated/filing_text.new" || exit 1
  mv "$LAKE/curated/filing_text" "$BACKUP/filing_text" && mv "$LAKE/curated/filing_text.new" "$LAKE/curated/filing_text" || exit 1
  ( cd "$BACKUP/filing_text" && find . -type f -exec shasum -a 256 {} + | sort ) | cmp -s - "$BACKUP/filing_text.sha256" && echo "backup of the old table EQUAL to it ($(wc -l < "$BACKUP/filing_text.sha256") files)" || { echo "backup DIFFERENT"; exit 1; }
  echo "=== the sync state's filing_text step from the scratch lake"
  python3 - "$SRC" "$LAKE" <<'PY' || exit 1
import json, os, sys, tempfile
src, lake = sys.argv[1], sys.argv[2]
entry = json.load(open(f"{src}/cache/sync_state.json"))["steps"]["filing_text"]
path = f"{lake}/cache/sync_state.json"
data = json.load(open(path))
data["steps"]["filing_text"] = entry
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(data, f, indent=2, sort_keys=True)
os.replace(tmp, path)
print(f"{path}: replaced steps.filing_text ({len(entry)} keys)")
PY
  echo "=== verify: the new table's files equal the scratch lake's"
  ( cd "$SRC/curated/filing_text" && find . -type f -exec shasum -a 256 {} + | sort ) > "$BACKUP/new.sha256"
  ( cd "$LAKE/curated/filing_text" && find . -type f -exec shasum -a 256 {} + | sort ) | cmp -s - "$BACKUP/new.sha256" && echo "files EQUAL ($(wc -l < "$BACKUP/new.sha256") files)" || { echo "files DIFFERENT"; exit 1; }
  echo "replace exit 0"
} > "$OUT" 2>&1
echo "replace finished: $(tail -1 "$OUT")"
