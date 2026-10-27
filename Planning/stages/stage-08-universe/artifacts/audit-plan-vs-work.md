# Stage 08 — Post-DONE audit: plan vs work (owner request, 2026-10-27)

Scope: [plan.md](../plan.md) (tasks, interfaces, D1–D9, Review Focus, global constraints) and [brief.md](../brief.md) against commit `5e78327` and this chat's [worklog](../worklog.md). Evidence: the test files, `git show --stat 5e78327`, and [audit-coverage.txt](audit-coverage.txt) (`pytest --cov` on the three Stage 08 modules: 97% lines; 88 passed).

## Matches (checked one by one)

| Plan item | Result |
|---|---|
| Task 1 parsers, 5 planned tests | Built as planned; all 5 tests present and green |
| Task 2 ticker map, planned tests | Built; one planned test replaced before code existed (`…cycle_raises` → `…rename_back_terminates`, plan D4 annotated); `test_ticker_change_validation`, `test_load_ticker_map_missing_file_is_empty` added |
| `config/ticker_changes.yaml` entry list | The 21 planned entries, same dates, each with a source (5 `log`, 3 `rev 1364118843`, 13 `manual`) |
| Task 3 reconstruction, D1 | Built as planned (rename rows skipped, removed tickers resolved the day before, three anomaly kinds); all planned tests present |
| Task 4 instruments, D3 | Built; column list matches the brief; `Instrument` gained the planned fields |
| Task 5 `Universe`, D5, D6, D7 | Signatures as planned; filters, defaults (5.0 / 20e6 / 20 / 252 / 5) and cache rule as planned; all planned tests present (+4 extra) |
| Task 6 live refresh | Built with one planned change (no `client`; annotated); live test passed against real Wikipedia |
| Task 7 docs + acceptance | Both acceptance tests and the doc test present and green; `docs/data-limitations.md` covers the planned topics |
| Review Focus 1–5 | Each has its named tests (RF4 also `test_tradable_without_any_prices_is_empty`) |
| Global constraints | Canonical symbols ✓, interval semantics ✓, PIT rule ✓ (`pit_filter` before every statistic), static tables + overwrite ✓, config-driven thresholds ✓, no network in unit tests ✓ (respx), test files as planned ✓, one stage commit ✓ (`5e78327`) |
| Brief In-scope (6 items) and the one acceptance line | All evidenced in [verification.md](../verification.md) |

## Findings

| # | Finding | Kind | Evidence | Proposed action |
|---|---|---|---|---|
| A1 | Plan D2 still says a current member with no "added" row gets the table's "Date added". After the Task 3 bug fix the code uses it only for members **no change-log row names** (DuPont case). Logged in the worklog, but the plan line was not annotated (unlike the D4 and Task 6 changes). | Record drift | plan.md D2; `build_membership` comment; `test_date_added_ignored_for_names_in_the_log` | Annotate D2 (records only) |
| A2 | 9 guard branches have no test: `parse_constituents` missing column / row without symbol / duplicate symbol; `parse_changes` unexpected header; `_read_table` "more than one table"; `build_instruments` ETF that is also an index member; empty-input shortcuts in `members_on` and `tradable`; `_log_names` bad-ticker skip. | Test gap | audit-coverage.txt: reference.py lines 79, 106, 110, 134, 148, 151, 167, 316–317, 368; universe.py 194, 350 | Add small tests for the 6 `DataError` guards and the 2 empty shortcuts |
| A3 | Stage protocol §5 asks every data path for a test that requests data after `as_of` and expects `LookAheadError`. `tradable()` **filters** future bars instead (four tests prove they are ignored), and its `assert_no_lookahead` guard cannot fire, because it checks the frame `pit_filter` just produced. No `LookAheadError` test exists for Stage 08. | Convention gap (interpretation) | `Universe.tradable`; Stage 07 also filters | Record as an interpretation in the Decisions log (filter semantics, as Stage 07); keep the guard as defence in depth |
| A4 | Public functions built but not named in the plan's file table: `read_instruments`, `read_config_file` (config), `TickerMap.changes`. | Scope addition | `git show --stat 5e78327` | Already listed as additions in the handoff note; no action |
| A5 | Tests with no red step: both acceptance tests (they check behaviour built in Tasks 3–5) and the self-review test `test_tradable_without_any_prices_is_empty`. | TDD deviation | worklog Task 7 entry | Already recorded; no action |
| A6 | Rule 6A merge into `main` not done: `git -C <main checkout> merge --ff-only` failed with "Operation not permitted" (the sandbox cannot write the main checkout). `main` is untouched at `c1afe56`. | Process gap (Rule 6 exception) | worklog; `git -C … rev-parse main` | Owner runs the fast-forward; then the AI re-runs the checks against `main` read-only and records the hashes (as in Stage 07) |
| A7 | `instruments.first_trade_date/last_trade_date` come from the prices in the lake **at refresh time**: null if prices are ingested after the refresh, and stale until the next refresh. In the docstring, not in `docs/data-limitations.md`. | Doc gap | `refresh_universe_reference` docstring | Add one line to the doc and the handoff note |
| A8 | `refresh_universe_reference` overwrites the whole `index_membership` table. Fine with one index (SP500); a second index would be wiped by the next S&P refresh. | Latent limit | D2 / Global constraints | Backlog item: make the overwrite per `index_id` when a second index is added |
| A9 | 13 of 21 rename dates are `manual` (not re-verified); COG→CTRA (2021-10-04) is the least certain. Membership is unaffected unless a change-log row falls between the true and the configured date. | Data accuracy | `config/ticker_changes.yaml`; Backlog "Stage 08" | Already in the Backlog; no action now |
| A10 | Privacy slip during the source probe: the first request to en.wikipedia.org sent a User-Agent containing the owner's e-mail address. No later request, file or commit contains it. | Conduct | worklog "Source probe" entry; staged-diff scan before commit was clean | Reported to the owner; nothing to undo in the repo |

No finding affects the acceptance criteria or the verification result: A2 and A3 are test or convention gaps around code whose main paths are tested; A1, A7 and A8 are records or latent limits.

## Follow-up (owner approved in chat: "fix audit items A1, A2, A3, A7 and A8 test-first, and commit the audit records with them")

| # | Outcome |
|---|---|
| A1 | Fixed: plan D2 annotated with the Task 3 change |
| A2 | Fixed: `test_parse_constituents_guards`, `test_parse_changes_unexpected_header_raises`, `test_build_instruments_rejects_etf_that_is_a_member`, `test_build_instruments_skips_bad_log_ticker`, `test_empty_inputs_give_empty_lists`. They pin existing code, so they had no red step; coverage of `atlas.data.reference` and `atlas.data.universe` is now 100% ([followup-coverage.txt](followup-coverage.txt)) |
| A3 | Fixed (records): Decisions-log row recording the filter interpretation |
| A7 | Fixed test-first: `test_data_limitations_doc` now requires `first_trade_date` (red: AssertionError), then the doc paragraph was added (green) |
| A8 | Fixed test-first, beyond the proposed backlog note: `test_refresh_keeps_other_indices` (red: the NDX rows were wiped, shape (0, 4) vs (2, 4)); `refresh_universe_reference` now replaces only the `SP500` rows (`_replace_index`) and upserts `instruments`; green, still idempotent |
| A4 | Closed in the gap-closing follow-up: plan file table lists the unplanned public functions |
| A5 | Closed in the gap-closing follow-up: [mutation-checks.txt](mutation-checks.txt), one injected bug per never-red test: 9 killed, 1 survivor that is a true equivalent (an empty-input shortcut in `members_on`); every source file restored byte-for-byte |
| A9 | Closed in the gap-closing follow-up: all 13 `manual` dates checked against SEC filings ([verify-renames-sec.txt](verify-renames-sec.txt), [verify-renames-sec-followup.txt](verify-renames-sec-followup.txt)); 9 confirmed, 4 consistent, JEC→J and Under Armour corrected |
| A6 | Resolved: the owner ran the fast-forward; merged-main checks green ([merged-main-check.txt](merged-main-check.txt)) |
| A10 | Reported to the owner |
