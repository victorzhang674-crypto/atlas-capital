# Hand-off 1 — Stage 29 follow-up 7 (full data sync of the main lake) — 2026-11-25 14:30

- **Original request (owner's words):** "run the full data sync for the rest". This follows follow-up 6, in which the owner chose "Sync prices to today". "The rest" means every dataset of `atlas data sync` other than prices, which is already current.
- **Stage / status:** 29 — Fundamental analyst agent — DONE (2026-11-18). Follow-ups 1–6 are merged. This is follow-up 7, not started; nothing is done for it yet. Stage 30 is NOT STARTED and must not be started (Rule 4).
- **Branch / worktree / last commit:**
  - branch `claude/stage-29-followup-5-235c7b`;
  - worktree `~/Projects/atlas-capital/.claude/worktrees/stage-29-followup-5-235c7b`;
  - last commit `dade0f0` "stage-29: follow-up 6 merge records" (plus this hand-off's commit);
  - `main` = `origin/main` = the branch;
  - CI on `dade0f0`: success on both runs (45633081131 `main`, 45633080791 branch).
- **Why a hand-off:** the session that did follow-ups 5 and 6 was close to a full context (Project.md Rule 7).

## Done so far (with evidence)

- Follow-up 5 (merged, CI green): MD&A rules; every stored MD&A re-extracted; 83 old-line mutants dealt with; OKTA and VYLR synced. See `verification.md › Follow-up 5`.
- Follow-up 6 (merged, CI green): main-lake prices from 2026-10-30 to **2026-11-24** for all 503 current members.
  - +10,675 bars, +155 corporate actions, `last_trade_date` refreshed.
  - Backup in `<lake>/cache/backup-stage29-followup6-2026-11-25`.
  - See `worklog.md › Follow-up 6` and `verification.md › Follow-up 6`.

## Current position

Nothing started for follow-up 7: no plan section, no scratch copy.

## Next steps, in order

1. **Rule 0.** Read `Project.md` (Rules 7 and 8 are new since 2026-11-25), `CLAUDE.md`, `Planning/PROGRESS.md` and this file. Check the state: `git status`, `git log -1`, and that `main` and `origin/main` equal the branch tip.
2. **Plan.** Write the section "Follow-up 7 — the full data sync" in this stage's `plan.md`, and a Decisions log row in `PROGRESS.md`. Owner's words: "run the full data sync for the rest".
   - Scope: `atlas data sync` over every dataset (`universe, prices, fundamentals, filings, macro, news, events, sentiment, quality`), window to today.
   - `universe` applies the index changes since 2026-11-01. That is wanted in a full sync: membership must stay point-in-time correct (Rule 8). New members then need their price history (the prices step does this) and their fundamentals, filings and events (the later steps).
3. **Scratch copy.** Copy `artifacts/main_lake_followup6.py` to `main_lake_followup7.py` and set `WORK = $TMPDIR/s29f7`.
   - It reuses `main_lake_followup5.copy` (all curated tables except news/news_sentiment, the DuckDB file, `sync_state.json`, per-file raw links).
   - **The news tables must be in the copy this time:** the news and sentiment datasets write them. Change `SKIP` to an empty set for follow-up 7.
   - Disk: about 27 GB free; the news table is ~170 MB.
4. **Sync on the copy.** Load the secrets with `set -a; . ~/Projects/atlas-capital/.env; set +a` (never print them) and run:
   `ATLAS__DATA__LAKE_PATH=$TMPDIR/s29f7/main-copy uv run atlas data sync`

   Run it in the background, with output to `$TMPDIR/s29f7/sync.txt`. Expect an hour or more; the Mac must stay awake (`mcp__ccd_host__request_keep_awake`).

   `allowed_domains`:
   - `data.sec.gov`, `www.sec.gov`;
   - `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `fc.yahoo.com`, `finance.yahoo.com`, `guce.yahoo.com`, `consent.yahoo.com`;
   - `api.stlouisfed.org`;
   - `data.alpaca.markets`;
   - `en.wikipedia.org`.

   Read the sync summary table: ok / empty / failed per step.
5. **Watch for the known checkpoint defect** (Backlog, follow-up 6 → 14). A step can mark a symbol as synced although the vendor returned no data in that run (seen for DHI, DHR, DIS on prices).
   - After the run, list every current member (`index_membership` with `end_date` null) that has no new rows where it should (prices to the last session, a recent 10-Q in fundamentals and filings, and so on).
   - For any such symbol, put its checkpoint in `cache/sync_state.json` back to the main lake's value and re-run that dataset with `--symbols`. Record each case in the worklog.
6. **Check against the main lake, table by table and row by row.** Extend `check` in `main_lake_followup7.py`:
   - rows removed, added and changed per table, and per date for dated tables;
   - prices: new bars only after 2026-11-24, apart from the sync's 5-session overlap (`adj_close` may change there for ex-dividends);
   - fundamentals: new facts filed since 2026-11-01; restated older facts listed;
   - `filing_text`: only new filings; old rows unchanged. The filings step never re-reads stored filings; it uses the follow-up 5 MD&A code;
   - events and reactions: added rows only;
   - `index_membership` and `instruments`: list every membership change.

   Review each kind of change before accepting it (Rule 8).
7. **Going-concern review.** Run `artifacts/filing_text_review_followup5.py $TMPDIR/s29f7/main-copy` (adapt the name to follow-up 7). It must still show the 76 FLAGs of follow-up 5; read any new FLAG.
8. **Stores (decide, and record the choice).**
   - The factor store is built at month-ends: the next is 2026-11-30, after today, so `atlas factors build` adds nothing yet.
   - `atlas risk-model build --from 2017 --to 2026 --cached-factors` would extend daily factor returns to 2026-11-24 using the October exposures.
   - If the universe changed, say whether the 2026-10-30 month-end exposures need a rebuild (new members' fundamentals) and do it if so: `atlas factors build --from 2015 --to 2026`, ~25 min, then the risk model, ~40 min.
   - Compare the stores with `main_lake_stores_compare_followup5.py`, adapted.
9. **Swap from the owner's Terminal panel.** Copy `main_lake_followup6_swap.sh` to `_followup7`, with backup `backup-stage29-followup7-<date>`; add the build-log lines back if the stores were rebuilt. Pass every changed table. Check that the backup and each replaced table's digests are equal to the scratch copy's. Then read back with an adapted `main_lake_readback_followup6.py`: row counts, `_meta.json`, and a few `DataView` cases (today's prices, a new 10-Q's fundamentals, `DataView.mda`, a news item).
10. **Records.**
    - `worklog.md`, `verification.md` (block "Follow-up 7 — Stage 29 — <date>"), `audit-report.md` if defects were found;
    - `PROGRESS.md`: last updated, Backlog (close the follow-up 6 → 14/16/19 line "the main lake still lacks fundamentals, events, filings and news since 2026-11-01"), Decisions log;
    - `docs/` only if a documented number changes.
11. **Git (Rule 6A; the owner never runs git).**
    - Review the staged diff and scan it for the `.env` values; no home or temp paths in artifacts (write `~` and `$TMPDIR`). Commit `stage-29: follow-up 7 (...)`.
    - Fast-forward `main` in the owner's Terminal panel with `artifacts/merged-main-check-followup6.sh` (adapted). It runs `uv run pytest -q` and `make check` on the merged result. If `main` has moved, merge `main` into the branch first.
    - Push `main` and the branch (Terminal panel); confirm with `git status -sb`.
    - Check CI by full SHA (`curl https://api.github.com/repos/victorzhang674-crypto/atlas-capital/actions/runs?head_sha=<sha>`, `allowed_domains: api.github.com`). Record the pushed hash and the CI result in the worklog, then push the records commit and report its CI in the reply.

## Open problems / suspicions

- The prices step's checkpoint defect (see step 5) may hit other steps too. Check every dataset's current members.
- Yahoo's cookie fetch fails from the sandbox and every chunk waits for a time-out. Slow but working. The upcoming-events step (Yahoo) will be slow too.
- `upcoming` is skipped when the run ends in the past; a run to today takes today's snapshot.
- `macro_calendar` re-reads FRED's release calendar for the whole market. That is expected in a full sync.

## Decisions taken and still owed to the owner

- Taken by the owner:
  - prices synced to today (follow-up 6);
  - full sync of the rest (this);
  - graph wiring stays with Stage 35.
- Owed: none known. If the universe step brings a membership change that looks wrong (a reused ticker, a rename), ask the owner before swapping.

## Environment notes

- uv:
  - `UV_CACHE_DIR=$TMPDIR/uv-cache-s29f5 UV_NO_SYNC=1 uv run ...`; the worktree venv is synced;
  - commits need `PRE_COMMIT_HOME=$TMPDIR/pre-commit` as well;
  - `LOKY_MAX_CPU_COUNT=4` for heavy runs.
- **Main lake** `~/Projects/atlas-capital/data`:
  - not writable from the sandbox; readable;
  - swaps, `git merge` on `main` and `git push` go through the Terminal panel (`mcp__terminal__run_in_terminal`, at most 6 tabs);
  - pass the sandbox's `$TMPDIR` as the absolute `/tmp/claude-501/...` in Terminal-panel commands;
  - write outputs into the worktree, not the owner's `$TMPDIR`.
- Never `echo` JSON in zsh. Read CI from the public API with `curl -o file`.
- The `.env` of the main checkout holds `FRED_API_KEY`, `SEC_USER_AGENT`, the Alpaca keys and `DEEPSEEK_API_KEY`; never print them.
- Scratch folders of follow-ups 5 and 6 in `$TMPDIR` (`s29f5`, about 4 GB with the document text cache; `s29f6`) may be deleted once follow-up 7 has its copy, to free disk.

## Files to read first

`Project.md`, `CLAUDE.md`, `Planning/PROGRESS.md`, this file, then in this stage's folder:
- `plan.md` (follow-ups 5 and 6);
- `worklog.md` (follow-ups 5 and 6);
- `artifacts/main_lake_followup5.py`, `artifacts/main_lake_followup6.py`;
- `artifacts/main_lake_followup6_swap.sh`, `artifacts/main_lake_readback_followup6.py`;
- `artifacts/merged-main-check-followup6.sh`.

## Not yet verified (must be re-run in the new chat before any DONE claim)

Everything for follow-up 7. Re-check `uv run pytest -q` at the start, as the gate (it was 5,563 passed on `dade0f0`).
