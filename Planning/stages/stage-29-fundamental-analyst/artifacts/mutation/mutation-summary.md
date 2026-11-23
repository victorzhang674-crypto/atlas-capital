# Stage 29 — mutation testing (audit A9 follow-up, 2026-11-18)

**Result: 2,339 of 2,396 mutants killed (97.6%)**, up from 2,053 (85.7%) before the follow-up
tests. All 57 survivors are classified, each with its reason, in
[final-survivors-classified.tsv](final-survivors-classified.tsv): 48 equivalent, 9 unreachable,
no untested edge left. No production defect was found. Every mutant that was killed was killed by a
test that also passes on the real code.

The code added later in follow-up 2 (the MD&A text and the going-concern check) has its own
run: **778 of 811 killed (95.9%)**, and all 33 survivors are equivalent (see the last section).

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
| 6 | 16 of 73 (the untested edges) | 12 | own-history window start (`valuation_years` 1 and 2); bar gaps; a quarter without a share count; red-flag periods with under a year of history; a peer without an instrument row before others; the no-factor-values message; a flow without a TTM; a tag-like piece |

| 7 (follow-up 3) | 4 of 61 (the last untested edges) | 4 | the dilution split window's start; the split factor of a quarter's own bar day; the view's calendar in `_window_sessions` |

Total: 343 − 286 = **57 survivors**.

## The 57 survivors

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
- **Unreachable (9).**
  - The fallback stamp in `_derived_items` is never used, because revenue's own TTM row always has one.
  - The default column of `_History.stamp` is never used.
  - The missing-symbol branch of `factor_ranks_from` is never reached: `sector_panel` raises first.
- **Untested edges: none left.** The last four were killed in follow-up 3 (pass 7).

## Follow-up 2: the MD&A text and the going-concern check

- **Setup:** [../mutmut-setup-followup2.cfg](../mutmut-setup-followup2.cfg).
  - Mutated: `atlas.data.filings.mda`, `atlas.data.filings.text`, `DataView.mda`, `_going_concern`, `going_concern_rule`, `going_concern_sentences` and `_phrase` (by mutant name).
  - Tests: the filings tests, the going-concern, red-flag and contracts tests, the view PIT suite and the sync tests.
  - `test_methods_cover_the_public_api` is deselected, because mutmut's trampolines add methods to `DataView`.
  - No mutant was "suspicious" in this run.
- **First run, on the code before the third round of tests:** 613 of 706 killed. Tests were then added for:
  - history pages that reach `since`;
  - two tickers of one issuer;
  - a symbol without filings followed by another;
  - a given calendar;
  - the log events;
  - exact errors;
  - the decoding of bytes undefined in Windows-1252;
  - comments in HTML;
  - the length-not-letters choice of a section;
  - inclusive `min_chars` and heading width;
  - the view's per-symbol cache.

  One dead line was removed: lxml.html never yields `{ns}tag` names.
- **Second run:** 764 of 801 killed. Three more tests (a 200-character quote, the bare-string error, submissions without history pages) killed 4 of the survivors ([followup2-rerun.txt](followup2-rerun.txt)).
- **Final run, after E7:** E7 added the MD&A-heading fallback and the "Management Discussion" heading. The run gave **778 of 811 killed (95.9%)** ([followup2-mutmut-results.txt](followup2-mutmut-results.txt)). Its 33 survivors are exactly the second run's, less the 4 killed there.
- **The 33 survivors are all equivalent** ([followup2-survivors-classified.tsv](followup2-survivors-classified.tsv)):
  - codec and time-zone name casing;
  - an anchored regex that matches once;
  - dtype inference;
  - dedupe, sort and index variants on unique keys;
  - default arguments equal to the given ones;
  - cache-key names;
  - a config never read when there is no text.

## Follow-up 3: 10-Q MD&As, more headings, the view's choice of filing

- **Setup:** the same as follow-up 2 ([../mutmut-setup-followup2.cfg](../mutmut-setup-followup2.cfg)), run on the follow-up 3 code. The changes covered:
  - 10-Q Part I Item 2;
  - EX-99 exhibits and an MD&A later in the 10-K (`skip_items`);
  - the "Financial Review" and upper-case company-name headings;
  - the bullet and cross-reference rules;
  - `DataView.mda` choosing the latest filing whose MD&A was found;
  - the "period ended" wording.
- **Run:** 851 killed, 1 timeout (a `_sections` loop that never ends, so the mutant is detected), 37 survived ([followup3-mutmut-results.txt](followup3-mutmut-results.txt)).
  - One survivor was a real gap: an inline bullet ("• Contingencies") before an end heading. The test `test_an_inline_bullet_entry_does_not_end_the_mda_either` killed it ([followup3-rerun.txt](followup3-rerun.txt)).
- **Result: 853 of 889 detected (96.0%).** The 36 survivors are all equivalent ([followup3-survivors-classified.tsv](followup3-survivors-classified.tsv)): the follow-up 2 kinds, plus a never-zero end position and default-form spellings that still read as a 10-K.

## Follow-up 3, after the data review (H6–H7)

- **Scope:** the review of the full 10-K and 10-Q data changed these functions, so they were mutated again (same setup, selected by mutant name):
  - `going_concern_sentences` (sentences cut by a line break or "Inc.");
  - `extract_mda` (Part II Item 2), `extract_exhibit_mda` (Entergy's heading, condensed statements) and `points_elsewhere`;
  - `_unless_later` and `_mda_of` (a short MD&A gives way to a later section or an exhibit).
- **First run:** 231 of 240 killed. Six survivors were real gaps:
  - the `short_mda_chars` bound (twice);
  - the tie with a later section or an exhibit (twice);
  - the `source` of a not-found row (twice).

  `test_the_short_bound_and_ties_keep_what_was_found`, `test_a_pointer_at_the_short_bound_or_tied_with_the_exhibit_is_kept` and a `source` assertion killed all six ([followup3-h4-rerun.txt](followup3-h4-rerun.txt)).
- **Result: 237 of 240 killed (98.75%)** ([followup3-h4-mutmut-results.txt](followup3-h4-mutmut-results.txt)). The 3 survivors are equivalent ([followup3-h4-survivors-classified.tsv](followup3-h4-survivors-classified.tsv)): two default-form spellings that still read as a 10-K, and a fallback string that is never used.

## Follow-up 4: the changed functions, including old Stage 06, 09 and 12 code

- **Setup:** [../mutmut-setup-followup4.cfg](../mutmut-setup-followup4.cfg). mutmut 3.8.1 was installed into a scratch folder: `uv run --with` would not add it on this run. Selected by mutant name:
  - `mda`: `_not_an_end`, `_sections`, `extract_mda`, `extract_exhibit_mda`, `points_elsewhere`;
  - `text`: `_unless_later`, `_filings_of`, `_rows`, `ingest_filing_text`;
  - `CikSource.covers`, `CikMap.__init__` / `with_overrides` / `sources`;
  - `ingest_fundamentals`, `ingest_earnings`;
  - `Lake.read_table`, `_sorted`, `_large_type`;
  - `going_concern_sentences`.

  Two of these functions had never been mutation-tested: `ingest_fundamentals` (Stage 09) and `ingest_earnings` (Stage 12). `Lake.read_table` (Stage 06) had not been either. No mutant was "suspicious".
- **First run:** 745 killed, 138 survived. The survivors on lines this follow-up wrote showed 13 real gaps, each killed by a new test:
  - the short MD&A is searched after its first copy, including when it opens the text, and an "Item" line after it is no later heading (`_unless_later`, 6 mutants);
  - a later override drops an earlier `since`, and a blocked symbol does not stop the overrides after it (2);
  - a page number as the first piece, or right after a first broken sentence (2);
  - an empty current CIK does not stop the former one (1);
  - `refresh` reaches the companyfacts, filing-index and submissions calls of every CIK, in both directions (6);
  - an EDGAR failure does not stop the next symbol in the earnings ingest (1; an old line).

  The co-registrant tests also pin the dedupe of facts and MD&A filings.
- **Result: 768 of 883 killed (87.0%)** ([followup4-mutmut-results.txt](followup4-mutmut-results.txt)). The 115 survivors are classified in [followup4-survivors-classified.tsv](followup4-survivors-classified.tsv):
  - **32 on lines this follow-up wrote, all equivalent:**
    - the sort `kind` on several columns;
    - `ignore_index` / `reset_index` where the index is reset or never read;
    - `keep="first"` (the default);
    - `zip(strict=)` on one frame's columns;
    - an upsert's default keys;
    - the default calendar;
    - identical duplicate rows of co-registrants in the earnings frame.
  - **83 on old lines** of the Stage 06, 09 and 12 functions: log fields, report counters, defaults and messages. They were never mutation-tested and are out of this follow-up's scope (Backlog).
  - Inside the new code, the old lines `_not_an_end`'s line start (unreachable), `extract_mda`'s default-form spellings and `_rows`' time-zone casing stay equivalent, as in follow-up 3.

## Follow-up 5: the 83 old-line survivors, and the code follow-up 5 changed

### The 83 survivors on old Stage 06, 09 and 12 lines

- **Setup:** [../mutmut-setup-followup5-old-lines.cfg](../mutmut-setup-followup5-old-lines.cfg), mutmut 3.8.1 from follow-up 4's scratch install. Selected by name: `_not_an_end`, `extract_mda`, `_rows`, `ingest_filing_text`, `ingest_fundamentals`, `ingest_earnings`, `Lake.read_table`. 642 mutants were selected; none were "suspicious".
- **Before (follow-up 4's tests):** 533 killed, 109 survived ([followup5-old-lines-mutmut-results-before.txt](followup5-old-lines-mutmut-results-before.txt)).
  - 81 of the 83 old-line survivors still survived.
  - The two `refresh` default mutants were already killed by follow-up 4's refresh tests, written after that follow-up's first run.
  - The other 28 survivors are follow-up 4's equivalent new-line ones.
- **Tests added** (no code changed to kill a mutant):
  - `test_fundamentals_ingest.py`:
    - `test_failure_reasons_and_the_request_message_are_exact`;
    - `test_the_log_events` (`structlog.testing.capture_logs`, the whole event dicts);
    - `test_fallback_rows_add_up_over_symbols_and_use_the_default_calendar` (AAPL's and MSFT's submissions with no acceptance times);
    - `test_a_given_calendar_or_the_config_s_calendar_dates_the_fallback_rows`;
    - `test_the_concept_map_given_or_else_the_config_s_map_file` (a map keeping each concept's first tag).
  - `test_events_earnings.py`:
    - `test_rows_and_skipped_filings_add_up_over_symbols_and_are_logged` (two tickers of one CIK, two acceptance times blanked);
    - `test_failure_reasons_are_exact_and_logged`;
    - `test_a_given_calendar_or_the_config_s_calendar_dates_the_events`.
  - `test_filings_ingest.py`: `test_the_config_s_calendar_dates_filings_without_an_acceptance_time`.
- **After:** 602 killed, 40 survived ([followup5-old-lines-mutmut-results.txt](followup5-old-lines-mutmut-results.txt)). **70 of the 83 are killed.** The 13 left are equivalent, each shown in [followup5-old-lines-classified.tsv](followup5-old-lines-classified.tsv), which also names the test that kills each of the other 70:
  - `Lake.read_table`'s `format="parquet"` (2, the dataset default) and `schema=` (2). For the schema: every file the lake writes carries the declared schema (`validate_frame`; `_check_meta` refuses a changed schema). A probe over all 19 registered tables found the inferred schema equal to the declared one and the reads identical ([read-table-schema-probe-followup5.txt](read-table-schema-probe-followup5.txt), script `read_table_schema_probe_followup5.py`).
  - `write_table`'s `keys=None` / omitted (4: the default is the primary key).
  - `ingest_filing_text`'s `mode="upsert"` omitted (append). Only filings not yet stored are written, and the batch is deduplicated by accession, so no key repeats and append writes the same rows.
  - `_rows`' `"datetime64[us, utc]"`: pandas 3.1.0 reads `utc` as `datetime.timezone.utc`, so the dtype, Arrow type and values are identical (checked).
  - `_not_an_end`'s `max(at - 1, 1)`. An end is searched from after a start match, so `at >= 1`, and `at == 1` is a line start only after a newline at 0. Either way the previous line is empty.
  - `extract_mda`'s default form spelled `"XX10-KXX"` or `"10-k"`: any form not starting with 10-Q selects the 10-K item.

### The functions follow-up 5 changed

- **Setup:** [../mutmut-setup-followup5.cfg](../mutmut-setup-followup5.cfg), tests `tests/unit/data/filings/`. Selected by name:
  - `mda`: `item_sections`, `named_sections`, `_title_key`, `extract_titled_mda`, `extract_exhibit_mda`, `_not_an_end`, `_continues`, `_sections`;
  - `text`: `_named`, `_longer`, `_mda_of`.

  The changed regular expressions are module constants, which mutmut does not mutate; the tests in `test_filings_mda.py` cover each rule.
- **First run** (before the last rule changes): 319 killed, 35 survived ([followup5-mutmut-results-first.txt](followup5-mutmut-results-first.txt)). Real gaps, each killed by a new test:
  - the closing-quote check reading only the end line;
  - the 3- and 100-character title bounds;
  - a title ending in "X" (APPENDIX);
  - a titled MD&A skipping ends that are no heading, running to the end, and at exactly `min_chars`;
  - `_longer` on a tie;
  - `_named` reading the 10-Q item and searching after the first copy;
  - the titled fallback after a `by_reference` item.
- **Second run** (final code): 421 killed, 1 timeout, 18 survived. Three more gaps were killed by tests:
  - `_sections`' combined-heading page check must not look past its own line (2);
  - a 10-Q item naming sections must be read through the titles, not through the "Financial Review" heading rule (1).
- **Result: 425 of 440 detected (423 killed, 2 timeouts; 96.6%)** ([followup5-mutmut-results.txt](followup5-mutmut-results.txt)). The 15 survivors are equivalent, each explained in [followup5-survivors-classified.tsv](followup5-survivors-classified.tsv):
  - positions that cannot occur (an end starts at a line start; `html_to_text` leaves no blank lines; `find` from `at >= 1`): 10, seven of them on follow-up 4's `_sections` lines;
  - default-form spellings: 2;
  - `_title_key` transforms applied to both sides of its comparison: 2;
  - a text that is never `None` in that branch: 1.
