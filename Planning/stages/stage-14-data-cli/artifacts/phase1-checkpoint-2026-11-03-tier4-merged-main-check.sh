#!/bin/zsh
# Phase 1 checkpoint (Tier 4 follow-ups to Stages 07, 08, 14): fast-forward main to the
# checkpoint branch, then re-verify on the merged main checkout (status is read-only).
cd "/Users/zzjsmacbookair/Projects/atlas-capital" || exit 1
echo "# Phase 1 checkpoint Tier 4: fast-forward + verification on merged main · $(date '+%Y-%m-%d %H:%M %Z') · $(uptime)"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo '$ git merge --ff-only claude/phase-one-checkpoint-stages-80625b'
git merge --ff-only claude/phase-one-checkpoint-stages-80625b; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo; echo '$ uv sync --group dev'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
for cmd in "uv lock --check" "uv run pytest -q" "uv run pytest -m integration -q" "uv run ruff check ." "uv run ruff format --check ." "uv run mypy src/atlas" "uv run pytest tests/unit/data -q" "uv run pytest tests/unit/data/test_universe.py -q" "uv run pytest tests/unit/cli -q" "make check" "git log --oneline -3" "git branch --merged main"; do
  echo; echo "\$ $cmd"; eval "$cmd" 2>&1 | grep -vE '^ - leaked|^nanobind|^See https|^\.+ +\[' ; echo "exit=${pipestatus[1]}"
done
echo; echo '$ COLUMNS=160 uv run atlas data status'; COLUMNS=160 uv run atlas data status 2>&1; echo "exit=$?"
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
