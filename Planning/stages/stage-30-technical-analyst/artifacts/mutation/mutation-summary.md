# Stage 30 — mutation testing (audit, 2026-11-25/26)

**Result: 997 of 1,048 mutants killed (95.1%).** The 51 left are all classified, each with its
reason, in [final-survivors-classified.tsv](final-survivors-classified.tsv): 47 equivalent,
3 unreachable, 1 on code the audit deleted. No untested edge is left. The run found one real
defect (below) and eleven untested edges, each closed by a test.

## Setup

- mutmut 3.8.1 as a throwaway tool on a scratch copy of the worktree at commit `7d9f6da`
  ([mutmut-setup.cfg](mutmut-setup.cfg)).
- Mutated: `src/atlas/agents/tools/technical_tools.py`, `src/atlas/agents/research/technical.py`,
  `src/atlas/agents/research/checks.py`.
- Tests: `tests/unit/agents/research/` and `tests/unit/core/types/test_types_technical.py`.

## Runs

1. `mutmut run` → 1,048 mutants: 929 killed, 1 survived, 118 "suspicious"
   ([mutmut-run.txt](mutmut-run.txt), [mutmut-results.txt](mutmut-results.txt)). "Suspicious" is
   the SIGTRAP exit of mutmut's forked workers on this Mac when the tests pass (Stage 29 saw the
   same).
2. Each suspicious mutant re-run in a plain process ([classify.sh](classify.sh)). The first
   attempt was discarded: `uv run --with mutmut` was refused pypi.org by the sandbox, so its exit
   codes were uv errors, not test results. Re-run offline (the worktree's Python, mutmut
   installed `--no-deps` into a scratch folder): 8 killed, 110 passing
   ([suspicious-rerun.txt](suspicious-rerun.txt)).
3. The 111 survivors (110 + the one from run 1) were read one by one
   ([survivor-diffs.txt](survivor-diffs.txt)). New tests
   (`tests/unit/agents/research/test_technical_edges.py`, one more in `test_technical_tools.py`)
   were added for every real gap, then the 111 were re-run against them: 57 killed
   ([survivors-rerun.txt](survivors-rerun.txt)). The mutants' source predates the audit's
   snapshot fix, so that fix's one test was deselected in this re-run (it fails on the old code,
   which would have counted every mutant as killed).
4. Three more gaps closed with three more tests; those three mutants killed
   ([last3-rerun.txt](last3-rerun.txt)).

Killed: 929 + 8 + 57 + 3 = 997.

## What the survivors showed

- **Defect:** `get_technical_snapshot` for a symbol with no bars in the window (a peer the model
  names wrongly) returned a snapshot of n/a values instead of an error. It now raises
  `DataError` ("no technical features for ZZZ …"), which the tool registry hands the model as an
  error text. Test `test_a_snapshot_of_a_symbol_without_bars_fails` fails on the previous commit
  ([edges-on-previous-commit.txt](edges-on-previous-commit.txt)).
- **Dead argument:** `_feature_values(…, on, …)` never used `on`; deleted.
- **Untested edges, now tested:** a NaN or infinite feature reads "n/a"; fewer than 20 bars (no
  swing levels, no ATR, no averages) and exactly 20 bars; one missing first bar; flat prices
  (range n/a, SMA50 equal to SMA200, every level at the close is support, resistance "none"); a
  stale symbol's averages and ATR read at its last bar; a stale symbol with a short history; a
  credit source with no z-score; the regime input's type named in the error; the exact
  check-output messages (signals, sections, uncited numbers from the summary and the risks); a
  given tool registry is used; the memory query names the symbol; the snapshot tool's regime gap
  names the requested symbol.
