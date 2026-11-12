#!/bin/zsh
# Phase 2 checkpoint, Tier 1: fast-forward `main` to the checkpoint branch and re-run the
# checks on the merged result (Project.md Rule 6A). Run from the main checkout:
#   zsh <this script> <output file>
# The sandbox cannot write the main checkout, so this runs in the owner's Terminal panel.
BRANCH=claude/phase-two-checkpoint-stages-a4dbeb
OUT=$1
{
  echo "=== before: $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
  git status --short
  echo "=== git merge --ff-only $BRANCH"
  git merge --ff-only $BRANCH
  echo "merge exit $?"
  git log --oneline -3
  echo "tree main: $(git rev-parse 'main^{tree}')  tree branch: $(git rev-parse "$BRANCH^{tree}")"
  echo "=== make check"
  make check 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https"
  echo "make check exit ${pipestatus[1]}"
  echo "=== uv run pytest -m integration -q -rs (main lake)"
  uv run pytest -m integration -q -rs 2>&1 | grep -v -E "nanobind|leaked|^ - |^See https"
  echo "integration exit ${pipestatus[1]}"
  echo "=== git branch --merged main"
  git branch --merged main | grep phase-two
  echo "=== after"
  git status --short
  git status -sb | head -1
} > $OUT 2>&1
echo "merged-main check written: $(grep -E 'exit [0-9]|passed|failed' $OUT | tr '\n' ' ')"
