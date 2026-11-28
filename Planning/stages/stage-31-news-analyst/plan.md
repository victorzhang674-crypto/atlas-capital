# Stage 31 — News & event analyst agent · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (see [verification.md](verification.md))
- **Kick-off (owner's words):** "please plan, execute and audit stage 31." Planned, executed and audited in one go (inline TDD, no subagents), the owner's standing preference.
- **Gate check:** Stage 30 is DONE ([verification.md](../stage-30-technical-analyst/verification.md) says ALL CRITERIA VERIFIED); `uv run pytest -q` → see [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt) (2026-11-28)

## Collisions with the overall plan
None. Roadmap row 31 (M, depends on 12, 28); spec §6 "News & Event Analyst | LLM (reads evidence) | quick | `AnalystReport`"; spec §7 `AnalystReport{…, signals[{name, direction, strength, evidence_ids}], …}`.

The brief adds `catalysts[{date?, description, evidence_id, expected_impact}]` to the output. As in Stage 30, the spec's contract is kept: the output is `NewsReport`, a subclass of `AnalystReport` with an extra `catalysts` field (D1). Every consumer of an `AnalystReport` takes it. Not a collision: nothing the spec states changes. (`atlas.core.types.research.Catalyst` already exists for the thesis, with a different shape: it stays as it is.)

## Decisions (recorded in PROGRESS.md › Decisions log)

- **D1 Output contract.** `atlas.core.types.research` gains `Impact` (StrEnum `POSITIVE` / `NEGATIVE` / `MIXED`: the direction of the expected effect on the stock's price), `NewsCatalyst` (`date: date | None`, `description`, `evidence_id`, `expected_impact: Impact`; extra keys refused) and `NewsReport(AnalystReport)` (`catalysts: tuple[NewsCatalyst, ...]`, default none). Rejected: reusing the thesis's `Catalyst` (no evidence id, no impact: the thesis's own shape is Stage 36's) and putting the fields on `Signal` (would change every analyst's schema).
- **D2 Citation of a catalyst.** The framework's citation check knows `evidence_ids` (claims) and `evidence` (lists). The brief names a single `evidence_id`. `check_citations` learns that a nested model with a string field `evidence_id` cites that one id: an id the run never showed the model is *unknown* (repair turn, then `low_quality`), exactly as for signals. A deliberate, tested extension of Stage 26's `atlas.agents.evidence` (recorded as a deviation from the brief's file list).
- **D3 Signals and sections.** Signals exactly `news_flow` (net direction and materiality of the recent news), `catalysts` (what the dated catalysts imply) and `event_risk` (how binary or adverse the coming events are). Summary sections in order: "Material vs noise:", "Catalysts:", "Risk events:", "Narrative shifts:", "What the market may be missing:", "Event calendar:" (the brief's prompt list, one to one, plus the calendar).
- **D4 Evidence and ids (stable, from the source).** `news:<article_id>` (an article, as `DataView.news` returns it: deduplicated, roundups dropped, point in time); `ncov:<SYM>:<day>:<company | sector>` (coverage of a news read: how many articles, over how many days, which peers were read: it makes "0 articles" a citable fact); `evt:<SYM>:<event_type>:<date>` (one upcoming event: earnings with BMO / AMC and whether the date is scheduled or only estimated, ex-dividend; `gap`-free "none" is `evt:<SYM>:<day>:none`); `rx:<SYM>:<event_date>` (one past earnings reaction: gap, open-to-close, abnormal and 3-session CAR against SPY, in percent) and `rxs:<SYM>:<day>:summary` (count, mean absolute CAR, share positive: computed by the tool, never by the model); `gap:<SYM>:<day>:<tool>` (what could not be gathered, and why). Coverage, summary, none and gap items are timed one microsecond before the decision cutoff (Stage 29's convention); articles, events and reactions carry their own `available_at`.
- **D5 Tools** (`atlas.agents.tools.news_tools`, settings `agents.news`): `get_company_news(symbol)` (articles over `lookback_days`, at most `max_articles`, newest first, plus the `ncov` item), `get_upcoming_events(symbol)` (the symbol's own events within `events_horizon_days`; macro rows are Stage 33's and are left out), `get_past_earnings_reactions(symbol)` (the last `reactions` = 8 with their summary), `get_sector_news(symbol)` (articles about the symbol's GICS-sector peers: the `sector_peers` members of the universe in its sector with the highest ADV, `sector_articles_per_peer` each, at most `sector_articles` in all, newest first, articles already in the company list left out, plus the `ncov:…:sector` item naming the peers read). Same `news:` ids as the company list, so one article is one evidence item. The three item kinds that can be empty say so (`ncov` count 0, `evt:…:none`, `rxs` count 0).
- **D6 Empty-news guard (brief).** With 0 company articles in the window (or the news table missing: a `gap`), (a) `check_output` asks, by repair turn, for confidence ≤ `empty_news_confidence_cap` (0.2), a summary that says there are no articles (`says_no_news`), and `news_flow` FLAT; (b) `finalize`, a new `BaseAgent` hook (identity by default, applied before the trace is written and after the low-quality cap), lowers the confidence to the cap whatever the model answered. So the cap cannot be missed even when the repair fails. Rejected: relying on the low-quality cap alone (0.3 > 0.2).
- **D7 Catalyst checks (`check_output`, `check_evidence`; repairable).** At most `max_catalysts` (8); a catalyst's `date`, when given, is on or after the as-of day; a catalyst citing an `evt:` item must carry that event's own date; every number in a description is stated by the evidence item the catalyst cites (`grounding_problems` on `"<description> [E:<id>]"`); plus Stage 29's rules (ticker, as_of, analyst, signals, sections, every number cited and grounded).
- **D8 Window defaults.** `lookback_days` 21 (the brief's 14–30), `max_articles` 25, `events_horizon_days` 30, `reactions` 8, `sector_peers` 8, `sector_articles_per_peer` 3, `sector_articles` 10, `max_catalysts` 8, `empty_news_confidence_cap` 0.2. Everything in `config/fund.yaml › agents.news`, nothing in code.
- **D9 Recorded fixture.** AAPL as of 2024-05-01 (Stages 29 and 30's day: the three reports can be read side by side; AAPL reported on 2024-05-02 after the close, so the next day's earnings is an upcoming event). Slice of the S&P 500 members on the day + AAPL + SPY (reaction benchmark), tables `news`, `events`, `macro_events` (the event view needs it), `earnings_reactions`, `prices_daily`, `corporate_actions`, `index_membership`, `instruments`, since a date that covers the news window, the 8 past reactions and the ADV window (checked by the main-lake vs slice comparison, item by item).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 1 — Output contract (D1)
- Files: modify `src/atlas/core/types/research.py`, `src/atlas/core/types/__init__.py` · test `tests/unit/core/types/test_types_news.py`
- [x] Failing tests: `Impact` values; `NewsCatalyst` needs a description, an evidence id and an impact, date optional, extra keys, bool, empty strings refused; `NewsReport` is an `AnalystReport`, JSON round trip, catalysts default empty; `cap_confidence` keeps the subclass; the core types package exports them
- [x] Run → FAIL (import error) · implement · run → PASS

### Task 2 — Citation of a single `evidence_id` (D2) and the `finalize` hook (D6)
- Files: modify `src/atlas/agents/evidence.py`, `src/atlas/agents/base.py` · tests `tests/unit/agents/test_evidence_single_id.py`, `tests/unit/agents/test_base_finalize.py`
- [x] Failing tests: a nested `evidence_id` is cited (known: ok; unknown: in `unknown`); an output with only such citations is not "cites nothing"; `finalize` runs once, after the low-quality cap, before the trace (the trace holds its result), default identity, Stage 26's tests unchanged
- [x] Run → FAIL · implement · run → PASS; every Stage 26 test still passes

### Task 3 — Config `agents.news` (D8)
- Files: modify `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/agents/research/test_news_config.py`
- [x] Failing tests: defaults; bounds (positive; `sector_articles_per_peer` ≤ `sector_articles`; the cap in [0, 1]); `fund.yaml` equals the defaults; every key commented; config docs tests green
- [x] Run → FAIL · implement · run → PASS

### Task 4 — Test world
- Files: create `tests/unit/agents/research/news_world.py`, extend `conftest.py`
- [x] A clean lake: sector peers (3 sectors), prices for ADV, articles (duplicates, a roundup, a correction, one published after the cutoff), scheduled and estimated earnings, an ex-dividend, a macro row, past earnings with reactions; a symbol with no news; a dirty copy adds rows known only after the as-of
- [x] A smoke test that the world builds and the `DataView` reads it

### Task 5 — `get_company_news` and `get_sector_news`
- Files: create `src/atlas/agents/tools/news_tools.py` · test `tests/unit/agents/research/test_news_tools.py`
- [x] Failing tests: articles newest first and capped; `ncov` counts and window; zero articles → `ncov` with count 0; the missing news table → `DataError`; sector peers chosen by ADV, the symbol excluded, articles of the company list left out, per-peer and total caps, the `ncov:…:sector` item naming peers; a symbol with no sector or no peers → an explicit item, not a guess; blocked symbol → `DataError`
- [x] Run → FAIL · implement · run → PASS

### Task 6 — `get_upcoming_events` and `get_past_earnings_reactions`
- Files: modify `news_tools.py` · test `test_news_tools.py`
- [x] Failing tests: earnings with BMO / AMC / unknown wording, scheduled vs estimated, ex-dividend, macro rows left out, none → `evt:…:none`; reactions: the last 8, percentages, summary mean absolute CAR and share positive hand-computed, none → `rxs` count 0
- [x] Run → FAIL · implement · run → PASS

### Task 7 — `prefetch` and the `Tool`s
- Files: modify `news_tools.py`, `src/atlas/agents/tools/__init__.py` docstring · tests `test_news_tools.py`, `test_news_tools_pit.py`
- [x] Failing tests: prefetch = company news + coverage + events + reactions + sector; a `DataError` of one tool becomes its `gap:` item, a blocked symbol propagates; the four `Tool`s (names, args, descriptions, version 1) return the same evidence for a peer; point in time: the dirty lake gives exactly the clean lake's evidence and every item's time is at or before the cutoff
- [x] Run → FAIL · implement · run → PASS

### Task 8 — The prompt and `NewsAnalyst`
- Files: create `src/atlas/agents/prompts/news_analyst.j2`, `src/atlas/agents/research/news.py`; modify `src/atlas/agents/research/__init__.py` · tests `tests/unit/agents/research/test_news.py` (the brief's Verify file), golden `golden/news_analyst_prompt.txt`
- [x] Failing FakeLLM tests: golden prompt; every prefetched item in the prompt; the four tools offered; a cited report validates; the empty-news path (0 articles: guard, repair, `finalize` cap, still-citable events); each rule of D3/D6/D7 gives a problem and the repair; every catalyst has an evidence id that was shown
- [x] Run → FAIL · implement · run → PASS

### Task 9 — Recorded live fixture (D9)
- Files: create `Planning/stages/stage-31-news-analyst/artifacts/record_fixture.py`, `tests/fixtures/recorded/stage-31-news-aapl-2024-05-01/` (`lake/`, `llm_cache.jsonl`, `expected_report.json`, `run.json`, `README.md`), `tests/unit/agents/research/recorded_news.py`, `tests/unit/agents/research/test_news_live.py` (marker `llm`)
- [x] Steps: `members` → `fixture` (main lake → slice) → `compare` (AAPL prefetch on the main lake vs the slice, item by item, equal) → `live` (DeepSeek quick tier on a copy of the slice, cache `read_write`) → `export`
- [x] Failing replay test in `test_news.py`: no keys, `OfflineProvider`, `replay_only`, the recorded report equals `expected_report.json`, citations ok, every catalyst's id shown, no uncited number, cost 0
- [x] Live test run once with the key (`-m llm -s`), output saved

### Task 10 — Docs and real-lake check
- Files: modify `docs/agents.md` ("The news analyst"), `docs/data-limitations.md` if a limit is found
- [x] Docs tests green
- [x] Prefetch on the main lake (read-only) for several symbols and days (AAPL; a symbol with no news; a day before news history starts; a crash day), output saved and read

### Task 11 — Verification, audit, records, git
- [x] `uv run pytest tests/unit/agents/research/test_news.py -q`, `uv run pytest -q`, `make check`, `make test-integration`, coverage of the new modules, mutation testing of the new modules (survivors classified)
- [x] Self-audit (`artifacts/audit-report.md`): every brief line against evidence, point in time, fail-safe paths, units, Rule 8 corners; fix findings test-first
- [x] `verification.md`, `worklog.md`, `PROGRESS.md` (status, handoff, Decisions log, Backlog)
- [x] Commit `stage-31: News & event analyst agent`, merge into `main`, re-run on the merged result, push, CI on the pushed commit
