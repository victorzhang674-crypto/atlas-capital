# Stage 11 — News data pipeline
**Phase:** 1 · **Size:** M · **Depends on:** 06 · **Spec refs:** §11, §16 (hallucination)

## Goal
Timestamped, de-duplicated, ticker-tagged news with stable evidence IDs that agents can cite.

## In scope
- `NewsProvider` protocol; `AlpacaNewsProvider` (Benzinga feed, history from 2015, paginated), `YahooNewsProvider` (recent only, fallback)
- Curated `news(article_id, published_at=available_at, source, headline, summary, url, symbols[], content_hash)`
- Dedup by content hash + fuzzy headline similarity (rapidfuzz ≥ 92) within 48h
- Light relevance scoring: ticker mentioned in the headline > in symbols list only; drop articles tagged with more than 10 tickers (roundups) unless the headline names the ticker
- `news_pit(symbol, as_of, lookback_days=14, limit=50) -> list[Evidence]` with `Evidence.id = "news:<article_id>"`
- Market-wide news query for the macro agent (`symbols` empty or SPY/QQQ)
- Storage of article text is limited to headline + summary (copyright and size)

## Out of scope
Sentiment scoring (Stage 12/32); full-text scraping.

## Files
`src/atlas/data/news/{base.py,alpaca_news.py,yahoo_news.py,dedup.py,pit.py}`, fixtures, tests

## Tasks
1. Protocol + fake; Alpaca adapter with pagination (respx).
2. Dedup tests (same story from two sources → one).
3. Relevance filter tests.
4. PIT query + look-ahead test (an article published at 08:30 is invisible for an 08:00 cutoff).

## Acceptance criteria
- Evidence IDs are stable across re-ingests; PIT and dedup tests pass.

## Verify
`uv run pytest tests/unit/data/news -q`
