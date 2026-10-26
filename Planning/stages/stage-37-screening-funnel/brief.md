# Stage 37 — Screening & research prioritisation funnel
**Phase:** 5 Portfolio · **Size:** M · **Depends on:** 22, 36 · **Spec refs:** §3.4, §5 step 5

## Goal
Decide **which names get expensive LLM research today**, within budget: the quant funnel that keeps costs bounded while covering what matters.

## In scope
- Candidate sources, each with a priority score: (a) multi-factor alpha extremes (top/bottom decile), (b) holdings whose thesis triggers fired (Stage 36), (c) holdings with `review_due` ≤ today, (d) upcoming earnings for holdings/candidates (Event pod), (e) unusual moves (|z| > 3) or news-volume spikes
- Priority = weighted sum (config) with mandatory items (triggered holdings always included)
- Budget-aware selection: estimated cost per deep-research name (from the Stage 24 ledger averages) × N ≤ the day's research budget; `max_names_per_day` (default 25)
- Rotation so every holding gets reviewed at least every `review_cycle_days` (default 20)
- Output `ResearchQueue{as_of, items[{symbol, reasons[], priority, mandatory}]}` + an audit event

## Files
`src/atlas/portfolio/screening.py`, tests

## Acceptance criteria
- Mandatory items are always included; the budget is never exceeded; tests cover the rotation fairness.

## Verify
`uv run pytest tests/unit/portfolio/test_screening.py -q`
