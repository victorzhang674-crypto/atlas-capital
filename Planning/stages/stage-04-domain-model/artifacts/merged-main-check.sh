#!/bin/zsh
# Re-verify Stage 04 on the merged main checkout; output goes to stdout (redirected by caller).
cd "/Users/zzjsmacbookair/Desktop/Trading Agents" || exit 1
echo "# Stage 04 follow-up: verification on merged main · $(date '+%Y-%m-%d %H:%M %Z')"
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo '$ git status --short   (empty = clean)'; git status --short; echo "exit=$?"
for cmd in "uv run pytest -q" "uv run ruff check ." "uv run ruff format --check ." "uv run mypy src/atlas" "uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core" "make check"; do
  echo; echo "\$ $cmd"; eval "$cmd"; echo "exit=$?"
done
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
