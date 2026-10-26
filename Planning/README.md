# Atlas Capital — Planning

A multi-agent **hedge fund simulator**. LLM agents act as analysts, PMs, CIO and risk officers; a deterministic quant core handles sizing, risk, execution and accounting. It is inspired by [TradingAgents](https://github.com/TauricResearch/TradingAgents) but extends it from "one ticker → one rating" to a full multi-strategy fund with real portfolio construction and hard risk limits.

## Read in this order

| File | What it is |
|---|---|
| [../Project.md](../Project.md) | **Governing rules. Read before every task.** Verify before claiming done; the overall plan wins collisions; strict stage order. Auto-loaded through `CLAUDE.md` |
| [00-design-spec.md](00-design-spec.md) | The design: vision, how Atlas differs from TradingAgents, org chart, the Fund Day, agents, risk framework, tech stack, assumptions |
| [01-master-roadmap.md](01-master-roadmap.md) | **The overall plan:** 66 stages in 10 phases, with dependencies, sizes and milestones; it wins any collision |
| [02-stage-protocol.md](02-stage-protocol.md) | How each chat runs a stage: the kick-off prompt, Definition of Done, conventions |
| [PROGRESS.md](PROGRESS.md) | Live tracker: stage checkboxes, handoff notes, backlog |
| [stages/](stages/) | **One folder per stage** (`stage-NN-<slug>/`) holding `brief.md` (goal, scope, files, interfaces, tasks, tests, acceptance). When the stage runs it adds `plan.md`, `worklog.md`, `verification.md` and `artifacts/`. All stage work is recorded there; code goes in `src/atlas/` and `tests/` |
| [stages/_template/](stages/_template/) | The files each stage copies into its folder when it starts |

## How to proceed

1. Review `00-design-spec.md`, especially **§15 Assumptions**. Change anything you disagree with now.
2. Open a new chat in this project and paste the kick-off prompt from `02-stage-protocol.md §1` with `NN = 01`.
3. Repeat for each stage, strictly in order. A stage counts as `DONE` only after its verification has been run in its own chat and the evidence is recorded in `PROGRESS.md`. The next stage cannot start before then.

## Estimate

**66 stages → 66 chats** (a few large stages may need a second chat). Milestones: M1 data (after 14), M2 quant (23), M3 research theses (36), M4 decision stack (49), M5 full backtest (59), M6 paper trading (66).

> Research and paper-trading software. It is not investment advice, and nothing here guarantees returns.
