#!/bin/zsh
# Phase 1 checkpoint (Tier 3 follow-ups to Stages 06, 07, 09, 13): fast-forward main to the
# checkpoint branch, re-verify on the merged main checkout, then re-sync the fundamentals and
# events of the 78 names that got a CIK (their checkpoint entries are forgotten first; the
# re-sync only adds rows) and show the lake status.
cd "/Users/zzjsmacbookair/Projects/atlas-capital" || exit 1
A=Planning/stages/stage-09-fundamentals/artifacts
echo "# Phase 1 checkpoint Tier 3: fast-forward + verification on merged main + re-sync · $(date '+%Y-%m-%d %H:%M %Z') · $(uptime)"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo '$ git merge --ff-only claude/phase-one-checkpoint-stages-80625b'
git merge --ff-only claude/phase-one-checkpoint-stages-80625b; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo; echo '$ uv sync --group dev'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
for cmd in "uv lock --check" "uv run pytest -q" "uv run pytest -m integration -q" "uv run ruff check ." "uv run ruff format --check ." "uv run mypy src/atlas" "uv run pytest tests/unit/core/storage -q" "uv run pytest tests/unit/data/fundamentals -q" "uv run pytest tests/unit/data -q" "make check" "git log --oneline -3" "git branch --merged main"; do
  echo; echo "\$ $cmd"; eval "$cmd" 2>&1 | grep -vE '^ - leaked|^nanobind|^See https|^\.+ +\[' ; echo "exit=${pipestatus[1]}"
done
echo; echo "\$ uv run python $A/phase1-checkpoint-2026-11-02-tier3-forget-keys.py data/cache/sync_state.json"
out=$(uv run python $A/phase1-checkpoint-2026-11-02-tier3-forget-keys.py data/cache/sync_state.json 2>&1); rc=$?; echo "$out"; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
syms=$(echo "$out" | sed -n 's/^symbols: //p')
echo; echo "\$ COLUMNS=160 uv run atlas data sync --from 2015-01-01 --datasets fundamentals,events --symbols <the 78>"
COLUMNS=160 uv run atlas data sync --from 2015-01-01 --datasets fundamentals,events --symbols "$syms" 2>&1 | grep -vE '━|^\s*$' | tail -20; echo "exit=${pipestatus[1]}"
echo; echo '$ COLUMNS=160 uv run atlas data status'; COLUMNS=160 uv run atlas data status 2>&1; echo "exit=$?"
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
