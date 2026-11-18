# Stage 29 — mutation testing (audit A9 follow-up, 2026-11-18)

**Result: 2,323 of 2,396 mutants killed (97.0%)**, up from 2,053 (85.7%) before the follow-up
tests. All 73 survivors are classified, each with its reason, in
[final-survivors-classified.tsv](final-survivors-classified.tsv): 48 equivalent, 9 unreachable,
16 untested edges. No production defect was found. Every mutant that was killed was killed by a
test that also passes on the real code.

## Setup

- mutmut 3.8.1, run as a throwaway tool (`uv run --with mutmut==3.8.1`, not a project
  dependency) on a scratch copy of the worktree. Config: [../mutmut-setup.cfg](../mutmut-setup.cfg).
- Mutated: `src/atlas/agents/tools/fundamental_tools.py`, `src/atlas/agents/research/fundamental.py`,
  `src/atlas/data/slice.py`.
- Tests: `tests/unit/agents/research/`, `tests/unit/data/test_data_slice.py`,
  `tests/unit/cli/test_cli_data_slice.py` (not `llm`, not `live`).

## First run

`mutmut run` → 2,396 mutants: 2,038 killed, 66 survived, 292 "suspicious"
([mutmut-run-tail.txt](mutmut-run-tail.txt), [mutmut-results.txt](mutmut-results.txt)).

"Suspicious" is mutmut's fallback for an unknown exit code. Here it was -5 (SIGTRAP): on this Mac,
mutmut's forked test workers end with SIGTRAP whenever the tests pass. So each suspicious
mutant was re-run in a plain process ([classify.sh](classify.sh):
`MUTANT_UNDER_TEST=<mutant> pytest -x …`, four at a time). Result: 15 killed, 277 passing, so
they survived ([suspicious-rerun.txt](suspicious-rerun.txt)). The plain process also mutates
import-time code (the tool registrations, the section patterns), which the forked run cannot do.

Before the follow-up: 2,038 + 15 = **2,053 killed (85.7%)**, 66 + 277 = 343 survivors. Their
diffs are in [survived-diffs.txt](survived-diffs.txt) and
[suspicious-diffs.txt](suspicious-diffs.txt).

## What the survivors showed, and the tests added

The survivors were read one by one. The real gaps were contracts that the first test set
checked only loosely, or not at all:

- the evidence `source` of every item and the `gap:` items;
- the point-in-time stamp of the margins / growth items (moved by a late input);
- the staleness and peer / own-history minimum boundaries;
- dilution from the share count alone, and a split after both counts;
- one failing tool inside `prefetch` becoming its own gap (and the order of the gaps);
- the exact NOT EVALUATED reasons and rule details;
- `fmt_usd` / ratio / percentile / nearest-end / split-factor edges;
- the exact repair-problem texts;
- the memory query;
- a caller's tool registry;
- the tools passing the run's view and symbol;
- the slice's manifest text and log, its registry, and list-column tables;
- the repair's quoting of at most five uncited sentences.

Re-run passes over the survivors ([after-reruns.txt](after-reruns.txt)):

| Pass | Survivors in | Killed | New tests in this pass |
|---|---|---|---|
| 1 | 343 | 248 | `test_fundamental_contracts.py` (64 tests); 3 slice tests; the five-sentence repair test; the gap-source assert |
| 2 | 95 | 9 | no TTM revenue → four checks NOT EVALUATED with the exact reason; going-concern check name; empty sentence piece; blocked symbol in `sector_panel` |
| 3 | 86 | 4 | the latest period is the last one with a TTM revenue; leading line break in `sentences` |
| 4 | 82 | 7 | revenue CAGR when shrinking (negative), zero or negative (n/m); own history counts only finite multiples; a tag-only piece joins the sentence before |
| 5 | 2 of 75 | 2 | a non-finite current multiple (a loss-maker's P/E) keeps its own history and gets no percentile |

Total: 343 − 270 = **73 survivors**.

## The 73 survivors

- **Equivalent (48).** The mutant behaves the same for every input. Examples:
  - `strict=` on zips of equal-length sequences;
  - `"UTF-8"` vs `"utf-8"`, and the encoding of an ASCII-only manifest;
  - `"utc"` vs `"UTC"`;
  - pandas arguments equal to the default it would use;
  - `typing.cast`;
  - replacement strings with no digit;
  - guards that NaN propagation makes redundant;
  - a write mode on a folder that is always new and empty;
  - arguments that the branch taken never reads.
- **Unreachable (9).** The fallback stamp in `_derived_items` is never used, because revenue's own
  TTM row always has one. The default column of `_History.stamp` is never used. The
  missing-symbol branch of `factor_ranks_from` is never reached (`sector_panel` raises first).
- **Untested edges (16).** These are recorded, not fixed; by reading, the code is right in each:
  - date boundaries in the own-history price matching (a quarter end exactly on the window
    start or the tolerance; a bar gap; a quarter with no bar followed by quarters with bars);
  - the dilution share-count window start;
  - the calendar config passed through to `_window_sessions`;
  - the period text when filings are fresh but there is under a year of history;
  - `sector_panel` with a member that has no instrument row placed before other peers, and
    the exact text when the symbol gets no factor values;
  - the stamp of a flow that has a value but no TTM at the latest end;
  - a strip-set variant that differs only for a tag-only piece ending in a literal `X`.

  Each needs a hand-built boundary world; the cost is not worth it at this stage. They are in the
  Backlog.
