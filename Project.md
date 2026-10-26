# Project.md — Governing Rules for Atlas Capital

> **Read this whole file before every task, in every chat, with no exceptions.**
> These rules override every other instruction in this repository. If something conflicts with them, they win.

---

## Rule 0 — Read first

Before any task (planning, coding, testing, docs, or a one-line fix), the AI must:

1. Read this file, `Project.md`, in full.
2. Read `Planning/PROGRESS.md` to learn the current stage and its status.
3. Start the reply by stating: **"Project.md read. Current stage: NN — <title> (<status>)."**

If the AI cannot read these files, it stops and tells the owner.

---

## Rule 1 — Never claim "done" without verification

"Done" is a factual claim. It may be made **only after verification has actually been run in the current chat and has passed**.

### 1.1 What counts as verification
- The commands are **actually executed** in this chat: the stage brief's `## Verify` commands, `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy src/atlas`.
- Their **real output has been read** and shows success (exit code 0, tests passed, no errors).
- Each **acceptance criterion** in the stage brief has been checked one by one against evidence: a test name, a command output, or a file that exists.

### 1.2 What does NOT count
- "It should work", "this looks correct", or "I wrote the tests" without running them.
- Output from an earlier chat, an earlier commit, or memory.
- Skipped, x-failed or deselected tests that cover the stage's acceptance criteria.
- Running a narrower command than the brief requires (for example one test file when the brief says the whole suite).

### 1.3 Allowed statuses (use these exact words)

| Status | Meaning |
|---|---|
| `NOT STARTED` | No work yet |
| `IN PROGRESS` | Work started; verification not yet passed |
| `BLOCKED` | Cannot proceed or cannot be verified; the reason and what is needed are written down |
| `DONE` | Every acceptance criterion is verified with evidence recorded in `PROGRESS.md` |

There is no "mostly done", "done except…", or "done (unverified)". Anything short of full verification is `IN PROGRESS` or `BLOCKED`.

### 1.4 When something cannot be verified
If a criterion needs something unavailable (for example API keys for a `live` test), the stage is `BLOCKED`, not `DONE`, **unless the owner explicitly waives that specific criterion in chat**. The waiver is recorded in `PROGRESS.md` (date, criterion, owner's words).

### 1.5 Evidence must be recorded
Before marking a stage `DONE`, write the full **Verification evidence** block in the stage's own folder at `Planning/stages/stage-NN-<slug>/verification.md` (Rule 2A), with raw command output saved under that folder's `artifacts/`. Then copy the block (the summary lines, not the raw logs) into the stage's handoff note in `PROGRESS.md`, with a link to `verification.md`:

```
#### Verification evidence — Stage NN — YYYY-MM-DD
- Commands run (this chat):
  - `uv run pytest -q` → 412 passed, 0 failed, 6 deselected (live/llm)
  - `uv run ruff check .` → All checks passed
  - `uv run ruff format --check .` → N files already formatted
  - `uv run mypy src/atlas` → Success: no issues found
  - <stage Verify command> → <key output line>
- Acceptance criteria:
  - [x] <criterion 1> — evidence: <test name / output / file>
  - [x] <criterion 2> — evidence: …
- Waivers: none | <criterion — owner waiver quote — date>
```

Only after this block is written in **both** places may the checkbox be ticked and the status set to `DONE`.

### 1.6 Honest reporting
- If tests fail, say so and quote the failure. Never hide, skip or weaken a test to get green.
- Never modify a test's assertion just to make it pass, unless the test itself is wrong. Then explain why and record it in the handoff note.
- Report what was **not** done as clearly as what was done.

---

## Rule 2 — Follow the plan, task by task

1. Work from the current stage's brief: `Planning/stages/stage-NN-<slug>/brief.md`.
2. First write the step-by-step plan in that stage's folder (`plan.md`, Rule 2A), then execute its tasks **in order**.
3. Mark each task complete only after its own test(s) have been run and passed (Rule 1 applied at task level).
4. Do only what the stage's **In scope** list says. Anything else goes to `PROGRESS.md › Backlog`.

---

## Rule 2A — All stage work is recorded in its stage folder

Planning is finished. Every piece of work from Stage 01 onward belongs to exactly one stage, and that stage's records live in **its own folder**:

```
Planning/stages/stage-NN-<slug>/
├── brief.md          the stage brief (already written; edit only with the owner's approval)
├── plan.md           step-by-step TDD plan, written before any code
├── worklog.md        dated, in-order record of what was done, what failed, how it was fixed
├── verification.md   real command output + evidence for every acceptance criterion (Rule 1.5)
└── artifacts/        raw logs, test/coverage output, reports, screenshots, other non-code outputs
```

1. **Create the folder's files at the start of the stage** by copying `Planning/stages/_template/` (only after the Rule 4 gate check passes). `brief.md` is already there.
2. **Record all stage work there.** The plan, every work-log entry, every verification output and every non-code artifact of the stage go in that folder. Nothing is recorded in a loose or shared location (no `Planning/stage-plans/`, no files in `Planning/` root, no scratch notes elsewhere).
3. **Code and tests are the exception.** They live in the shared `src/atlas/` and `tests/` trees (design spec §12), because later stages import earlier code. The stage's `worklog.md` lists every code file it created or changed.
4. **No work outside a stage.** A fix or change that belongs to no stage is either added to the current stage's folder (if it is in scope) or written in `PROGRESS.md › Backlog`. It is not done ad hoc.
5. **Never create a folder's working files for a stage that has not started** (Rule 4). Folders for later stages contain only `brief.md` until their turn.
6. `PROGRESS.md` remains the index: status, checkbox, handoff summary, and links to the stage folder.

---

## Rule 3 — When instructions collide, the overall plan wins

**The overall plan** is `Planning/01-master-roadmap.md`, together with its design annex `Planning/00-design-spec.md`.

Precedence, highest first:

1. `Project.md` (this file)
2. `Planning/01-master-roadmap.md` (stage order, scope boundaries, dependencies)
3. `Planning/00-design-spec.md` (architecture, contracts, limits, principles)
4. `Planning/02-stage-protocol.md` (process and conventions)
5. `Planning/stages/stage-NN-<slug>/brief.md` (the stage brief)
6. `Planning/stages/stage-NN-<slug>/plan.md` (the step-by-step plan written in the stage's chat)
7. Code comments, earlier handoff notes, ad-hoc ideas

When a collision is found:
- Follow the higher-ranked document.
- Do **not** silently edit the overall plan to make the collision disappear. Changing `01-master-roadmap.md` or `00-design-spec.md` requires the owner's explicit approval in chat.
- Record the collision and how it was resolved in `PROGRESS.md › Decisions log` (date, stage, the conflicting texts, the resolution).
- If following the higher-ranked document would make the stage impossible, set the stage to `BLOCKED` and ask the owner.

---

## Rule 4 — Strict stage order: no stage starts before the previous one is DONE

1. Stages run in numeric order: 01 → 02 → … → 66.
2. **Stage N may not start until Stage N−1 is `DONE`** in `PROGRESS.md`, with its Verification evidence block present both in `PROGRESS.md` and in Stage N−1's `verification.md`.
3. "Start" includes any work for a later stage: writing its plan, creating its files, adding its code "early", or partially implementing its features inside the current stage.
4. **Gate check (required at the start of every stage chat):**
   - Confirm in `PROGRESS.md` that Stage N−1 is `DONE`, and that its folder's `verification.md` says **ALL CRITERIA VERIFIED** with evidence for every criterion.
   - Re-run `uv run pytest -q` (from Stage 01 onward) and confirm it is green.
   - If either check fails, **do not start Stage N.** Report the failure and work only on getting Stage N−1 to `DONE`.
5. A stage split across several chats ("part 1", "part 2") remains `IN PROGRESS` until its final part passes verification. The next stage still waits.
6. If the owner explicitly asks to start a later stage early, remind them of this rule, and proceed only if they confirm in chat. Record that override in the Decisions log.

---

## Rule 5 — Safety rails specific to this project

- Use paper trading only. Never add code paths to live-money endpoints (design spec §15 A4).
- Use no real network calls or real LLM calls in unit tests.
- Strategy and agent code accesses data only through `DataView` (point-in-time).
- Never commit secrets. Never print API keys.

---

## Quick checklist (copy into each stage chat)

- [ ] Read Project.md in full and stated the current stage and status
- [ ] Gate check: previous stage `DONE` with evidence; full test suite green
- [ ] Copied `Planning/stages/_template/` into the stage folder; read `brief.md`; wrote `plan.md`
- [ ] Executed tasks in order, verifying each one, and logged each in `worklog.md`
- [ ] Checked collisions against the overall plan; logged any in the Decisions log
- [ ] Ran the full verification in this chat; output read and saved to `artifacts/`; all criteria evidenced
- [ ] Wrote `verification.md` and the summary in `PROGRESS.md`, then ticked the box and set `DONE`
- [ ] Committed `stage-NN: <title>`
