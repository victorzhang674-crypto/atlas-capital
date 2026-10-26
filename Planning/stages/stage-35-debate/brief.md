# Stage 35 — Bull / Bear / Red-team debate
**Phase:** 4 · **Size:** M · **Depends on:** 29–34 · **Spec refs:** §2 (debate row)

## Goal
An adversarial, evidence-bound debate that stress-tests the analysts' combined picture before a thesis is formed.

## In scope
- Agents: Bull, Bear (quick tier), Red Team (quick; attacks whichever side is winning: hidden assumptions, base rates, what is priced in, crowding, tail risks)
- Protocol: round 1 openings (Bull, Bear) → round 2 rebuttals → Red Team critique → round 3 final statements; `max_rounds` configurable (default 2 + red team); early stop if both sides concede on key points
- Each `DebateTurn{speaker, claims[{text, evidence_ids, strength}], concessions[], questions_for_other_side[]}`; uncited claims are discounted
- Memory: each debater recalls lessons for similar situations (Stage 27)
- A debate transcript is stored and rendered for reports
- Debate scoring helper: count of uncontested cited claims per side → `debate_balance∈[-1,1]` (input to the Director)

## Files
`src/atlas/agents/debate/{bull.py,bear.py,red_team.py,protocol.py,scoring.py}`, prompts, tests

## Tasks
1. Protocol state machine with FakeLLM (rounds, early stop).
2. Turn validation + citation discount.
3. Balance scoring tests.
4. Integrate into the research subgraph from Stage 28 (replace the stubs).

## Acceptance criteria
- The full debate runs offline from fixtures; transcript and balance are produced; uncited claims are measurably discounted.

## Verify
`uv run pytest tests/unit/agents/debate -q`
