# Hand-off 2 — Stage 29 follow-up 7 (full data sync) — 2026-11-25

- **Original request (owner's words):** "run the full data sync for the rest".
- **Stage / status:** 29 — DONE; follow-up 7 IN PROGRESS (part 2).
- **Branch / worktree / last commit:**
  - branch `claude/exciting-faraday-bba38a`;
  - worktree `~/Projects/atlas-capital/.claude/worktrees/exciting-faraday-bba38a`;
  - last commit: this hand-off's (`stage-29 (part 7b)`).
  - **Not merged into `main` and not pushed.**
- **Done (evidence in `worklog.md › Follow-up 7`):**
  - gate 5563 passed; CI on `f800722` green;
  - plan M1–M8 + M2b and the Decisions log rows;
  - sync run 1 and its findings: macro raw/fred fix in the copy script; lexicon override; SNDK split gap (checkpoint kept at 2026-11-19); 24 members with stale SEC companyfacts (upstream);
  - two Stage 09 client fixes, test-first: stale history pages refetched against their index entry; the other pages read when a needed filing is still missing.
- **Current position:** sync run 2 (started before the second fix) may still be running or finished on `$TMPDIR/s29f7/main-copy`. **Discard it.**
- **Next steps, in order:**
  1. Rule 0. Check `git status` and `git log`. Run `uv run pytest -q`.
  2. Fresh copy: `uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_followup7.py copy`, after `rm -rf $TMPDIR/s29f7/main-copy`.
  3. `atlas data sync` on it, with `ATLAS__DATA__LAKE_PATH` pointing at the copy and `ATLAS__DATA__SENTIMENT__LEXICON_FILE=~/Projects/atlas-capital/data/reference/lm_lexicon.csv`. Allowed domains as in hand-off 1, plus `drive.usercontent.google.com`.
  4. Set SNDK's prices checkpoint back to `2026-11-19` in the copy's `cache/sync_state.json`.
  5. Run `members` and `check` (the `main_lake_followup7.py` steps). Expect no `accepted_at` value→NaT; review every kind of change, including the added events and news before 2026-11-01.
  6. Going-concern review (76 FLAGs).
  7. Decide on the stores: no membership change, and October fundamentals barely changed, so the expected answer is no rebuild before the 2026-11-30 month-end. Record why.
  8. Swap with `artifacts/main_lake_followup7_swap.sh` from the Terminal panel (all changed tables), then read back.
  9. Records:
     - `verification.md` "Follow-up 7";
     - `PROGRESS.md`: last updated, and close the follow-up 6 → 14/16/19 Backlog line;
     - add Backlog items: companyfacts gap for 24 members (read filings' XBRL); lexicon download quota (HTML page); SNDK post-split bars; checkpoint defect (exists).
  10. Git:
      - fast-forward `main`, using an adapted `merged-main-check-followup6.sh` with this branch;
      - push both branches;
      - CI by full SHA.
- **Not yet verified:** the full suite and `make check` on the final code; everything from step 2 on.
