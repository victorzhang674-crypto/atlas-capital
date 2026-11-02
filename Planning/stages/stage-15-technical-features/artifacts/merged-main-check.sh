#!/bin/zsh
# Stage 15: fast-forward main to the stage branch, then re-verify on the merged main checkout.
# Run from the owner's Terminal panel (the sandbox cannot write the main checkout's working tree).
# Output goes to stdout; the caller tees it into artifacts/merged-main-check.txt.
cd "/Users/zzjsmacbookair/Projects/atlas-capital" || exit 1
echo "# Stage 15: fast-forward + verification on merged main · $(date '+%Y-%m-%d %H:%M %Z')"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo '$ git merge --ff-only claude/stage-15-plan-implement-faa041'
git merge --ff-only claude/stage-15-plan-implement-faa041; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
echo; echo '$ uv sync --group dev'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
for cmd in "uv run pytest -q" "uv run pytest tests/unit/quant/features -q" "make check" "git branch --merged main"; do
  echo; echo "\$ $cmd"; eval "$cmd"; echo "exit=$?"
done
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
