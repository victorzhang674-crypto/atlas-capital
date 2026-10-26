# Stage 12 — Events calendar & sentiment sources
**Phase:** 1 · **Size:** M · **Depends on:** 11 · **Spec refs:** §8 (Event pod), §6

## Goal
Know upcoming and past corporate events (earnings especially) and give agents a cheap, numeric sentiment baseline.

## In scope
- Earnings calendar: historical announcement dates derived from 10-Q/10-K/8-K item 2.02 filing timestamps (EDGAR submissions, already accessible), plus upcoming dates from Yahoo/Finnhub-free (optional provider) → `events(symbol, event_type, event_time, time_of_day BMO/AMC/unknown, available_at)`
- Earnings surprise proxy without paid estimates: price reaction (open-to-close and 3-day abnormal return vs SPY) after each earnings date, stored in `earnings_reactions`
- Dividend ex-dates from corporate actions (Stage 07) exposed as events
- Macro event calendar: FOMC dates (static YAML per year), CPI/NFP release dates (from ALFRED release calendars)
- Numeric news sentiment: a finance-tuned lexicon (Loughran-McDonald word lists, open licence for research) scored over headline+summary → `news_sentiment(article_id, score, pos, neg, uncertainty)`; daily aggregate per symbol with decay
- Optional Reddit/StockTwits providers behind a flag (off by default) with the same `Evidence` output

## Out of scope
Paid consensus estimates; LLM-based sentiment (Stage 32 uses the LLM).

## Files
`src/atlas/data/events/{earnings.py,macro_calendar.py}`, `src/atlas/data/sentiment/{lexicon.py,aggregate.py,social.py}`, `config/macro_calendar.yaml`, `data/reference/lm_lexicon.csv` (or download script), tests

## Tasks
1. Earnings dates from filing timestamps; test against known AAPL dates.
2. Earnings reaction computation (PIT: the reaction is only available after its window closes).
3. FOMC/CPI calendar loader.
4. Lexicon scorer + daily decayed aggregate; known-sentence tests.
5. `upcoming_events(as_of, horizon_days)` and `past_events(symbol, as_of, n)`.

## Acceptance criteria
- Event queries are PIT-correct; the sentiment aggregate is deterministic and tested.

## Verify
`uv run pytest tests/unit/data/events tests/unit/data/sentiment -q`
