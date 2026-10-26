# Stage 31 — News & event analyst agent
**Phase:** 4 · **Size:** M · **Depends on:** 12, 28

## Goal
An analyst that turns the last 14–30 days of company news and upcoming events into catalysts, risks and an event calendar, with every item cited.

## In scope
- Tools: `get_company_news` (deduped evidence list), `get_upcoming_events` (earnings date/time, ex-div), `get_past_earnings_reactions` (last 8), `get_sector_news`
- Prompt: material vs noise classification, catalysts with dates, risk events, narrative shifts, what the market may be missing
- Output `AnalystReport` + `catalysts[{date?, description, evidence_id, expected_impact}]`
- Guard: when there are 0 articles, it says so (no fabrication), with confidence ≤ 0.2

## Files
`src/atlas/agents/research/news.py`, `src/atlas/agents/tools/news_tools.py`, prompt, tests

## Acceptance criteria
- Every catalyst has an evidence ID; the empty-news path is tested; offline replay works.

## Verify
`uv run pytest tests/unit/agents/research/test_news.py -q`
