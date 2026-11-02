#!/bin/zsh
# Phase 0 checkpoint (Tier 2 follow-ups to Stages 02, 04, 05): fast-forward main to the
# checkpoint branch, then re-verify on the merged main checkout. Output goes to stdout.
cd "/Users/zzjsmacbookair/Projects/atlas-capital" || exit 1
echo "# Phase 0 checkpoint Tier 2: fast-forward + verification on merged main · $(date '+%Y-%m-%d %H:%M %Z') · $(uptime)"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo '$ git merge --ff-only claude/phase-one-checkpoint-28fcd7'
git merge --ff-only claude/phase-one-checkpoint-28fcd7; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo; echo '$ uv sync --group dev'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
for cmd in "uv run pytest -q" "uv run ruff check ." "uv run ruff format --check ." "uv run mypy src/atlas" "uv run pytest tests/unit/core/test_config.py -q" "uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core" "uv run pytest tests/unit/core -q && uv run ruff check ." "make check" "git branch --merged main"; do
  echo; echo "\$ $cmd"; eval "$cmd"; echo "exit=$?"
done
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
