#!/bin/zsh
# Stage 18: fast-forward main to the stage branch, re-verify on the merged main checkout, then
# push main and the stage branch (Rule 6A.7; a normal push, never a force-push).
# Run from the owner's Terminal panel: the sandbox cannot write the main checkout's working
# tree or reach GitHub. Usage: zsh merged-main-check.sh [--records-only]
#   --records-only: the follow-up records commit (fast-forward, quick checks, push).
BRANCH=claude/stage-18-covariance-vol-0d3e46
cd "/Users/zzjsmacbookair/Projects/atlas-capital" || exit 1
export MPLCONFIGDIR="${TMPDIR:-/tmp}/mpl-atlas"
echo "# Stage 18: fast-forward + verification on merged main · $(date '+%Y-%m-%d %H:%M %Z') · mode ${1:-full}"
echo '$ git status --short   (before; empty = clean)'; git status --short; echo "exit=$?"
echo; echo "\$ git merge --ff-only $BRANCH"
git merge --ff-only $BRANCH; rc=$?; echo "exit=$rc"
[ $rc -eq 0 ] || exit $rc
echo "# checkout: $(pwd) · branch $(git branch --show-current) · HEAD $(git rev-parse --short HEAD)"
if [ "${1:-full}" = "--records-only" ]; then
  cmds=("git diff --stat HEAD~1..HEAD" "uv run pytest tests/unit/quant/risk_model -q" "git log --oneline -3")
else
  echo; echo '$ uv sync --group dev'; uv sync --group dev 2>&1 | tail -3; echo "exit=$?"
  cmds=("uv lock --check" "uv run pytest -q" "uv run pytest tests/unit/quant/risk_model -q" "make check" "git log --oneline -3" "git branch --merged main")
fi
for cmd in "${cmds[@]}"; do
  echo; echo "\$ $cmd"; eval "$cmd" 2>&1 | grep -vE '^ - leaked|^nanobind|^See https|^\.+ +\[' ; rc=${pipestatus[1]}; echo "exit=$rc"
  [ $rc -eq 0 ] || { echo "STOP: '$cmd' failed; main is fast-forwarded but NOT pushed"; exit $rc; }
done
echo; echo '$ git status --short   (after; empty = clean)'; git status --short; echo "exit=$?"
echo; echo "\$ git push origin main $BRANCH"; git push origin main $BRANCH 2>&1; echo "exit=$?"
echo; echo '$ git status -sb'; git status -sb | head -1; echo "exit=$?"
echo; echo '$ gh run list --limit 3'; gh run list --limit 3 2>&1; echo "exit=$?"
