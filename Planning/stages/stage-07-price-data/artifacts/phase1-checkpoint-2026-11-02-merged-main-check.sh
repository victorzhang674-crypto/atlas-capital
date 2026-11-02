#!/bin/zsh
# Phase 1 checkpoint (Tier 1 follow-ups to Stages 07, 11, 14): fast-forward main to the
# checkpoint branch, re-verify on the merged main checkout, then re-sync AVB's prices into
# the main lake with the new incomplete-bar rule (Alpaca keys from the main checkout's .env).
cd "/Users/zzjsmacbookair/Projects/atlas-capital" || exit 1
echo "# Phase 1 checkpoint Tier 1: fast-forward + verification on merged main · $(date '+%Y-%m-%d %H:%M %Z') · $(uptime)"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo '$ git merge --ff-only claude/phase-one-checkpoint-stages-80625b'
git merge --ff-only claude/phase-one-checkpoint-stages-80625b; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo; echo '$ uv sync --group dev'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
for cmd in "uv lock --check" "uv run pytest -q" "uv run pytest -m integration -q" "uv run ruff check ." "uv run ruff format --check ." "uv run mypy src/atlas" "uv run pytest tests/unit/data -q" "uv run pytest tests/unit/data/news -q" "make check" "git log --oneline -3" "git branch --merged main"; do
  echo; echo "\$ $cmd"; eval "$cmd" 2>&1 | grep -vE '^ - leaked|^nanobind|^See https|^\.+ +\[' ; echo "exit=${pipestatus[1]}"
done
echo; echo '$ COLUMNS=160 uv run atlas data sync --from 2015-01-01 --datasets prices --symbols AVB'
COLUMNS=160 uv run atlas data sync --from 2015-01-01 --datasets prices --symbols AVB 2>&1 | grep -vE '━|^\s*$' | tail -12; echo "exit=${pipestatus[1]}"
echo; echo '$ COLUMNS=160 uv run atlas data status'; COLUMNS=160 uv run atlas data status 2>&1; echo "exit=$?"
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
