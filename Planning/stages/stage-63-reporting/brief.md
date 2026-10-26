# Stage 63 — Reporting: daily risk report & letters
**Phase:** 9 · **Size:** M · **Depends on:** 53, 57

## Goal
The documents a real fund produces: a daily risk report, a weekly PM commentary and a monthly investor letter, all generated from facts.

## In scope
- Daily risk report (deterministic + CRO memo): NAV/P&L, exposures, VaR/CVaR, stress table, limit utilisation, circuit-breaker state, top risk contributors, trades done, TCA, upcoming events for holdings
- Weekly PM commentary (LLM report writer): per-pod performance, new and exited theses with one-line rationales, macro view changes
- Monthly investor letter (LLM): performance vs benchmarks, attribution, positioning, outlook, risk; a strict "facts only" pipeline: a fact sheet (JSON) is built deterministically, and the writer may only use numbers from the fact sheet (a validator checks that every number in the text exists in the facts)
- Output HTML (+ PDF via WeasyPrint, optional) in `data/reports/YYYY/MM/…`; an index page
- CLI: `atlas report daily|weekly|monthly --date D`

## Files
`src/atlas/reporting/{facts.py,daily_risk.py,weekly.py,monthly_letter.py,validator.py,templates/*.j2}`, prompts, tests

## Acceptance criteria
- The number validator catches a fabricated figure (test); all three reports render for a fixture run.

## Verify
`uv run pytest tests/unit/reporting -q && uv run atlas report daily --date <date>`
