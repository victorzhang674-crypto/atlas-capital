#!/bin/zsh
# Fast-forward main to the Stage 05 commit, then re-verify Stage 05 on the merged main checkout.
# Output goes to stdout (redirected by the caller into merged-main-check.txt).
cd "/Users/zzjsmacbookair/Desktop/Trading Agents" || exit 1
echo "# Stage 05 follow-up: fast-forward + verification on merged main · $(date '+%Y-%m-%d %H:%M %Z')"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo '$ git merge --ff-only claude/stage-5-detailed-plan-5c356f'
git merge --ff-only claude/stage-5-detailed-plan-5c356f; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo; echo '$ uv sync --group dev   (new runtime deps: exchange-calendars, pandas)'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
for cmd in "uv run pytest -q" "uv run ruff check ." "uv run ruff format --check ." "uv run mypy src/atlas" "uv run pytest tests/unit/core -q && uv run ruff check ." "make check"; do
  echo; echo "\$ $cmd"; eval "$cmd"; echo "exit=$?"
done
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
