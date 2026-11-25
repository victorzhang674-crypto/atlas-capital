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
- The commands are **actually executed by the AI** in this chat (Rule 6): the stage brief's `## Verify` commands, `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy src/atlas`. Output the owner ran and pasted counts only under a Rule 6 exception, labelled as such.
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
├── artifacts/        raw logs, test/coverage output, reports, screenshots, other non-code outputs
└── handoffs/         hand-off files written when a chat's context runs low (Rule 7); created only when needed
```

1. **Create the folder's files at the start of the stage** by copying `Planning/stages/_template/` (only after the Rule 4 gate check passes). `brief.md` is already there.
2. **Record all stage work there.** The plan, every work-log entry, every verification output and every non-code artifact of the stage go in that folder. Nothing is recorded in a loose or shared location (no `Planning/stage-plans/`, no files in `Planning/` root, no scratch notes elsewhere). The one exception is `Planning/handoffs/`, for hand-off files of work that belongs to no stage (Rule 7.3).
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

## Rule 6 — Run terminal commands yourself; never hand them to the owner

Whenever a task needs something run in a terminal, **the AI runs it itself**, automatically, with its own tools, and reads the output. It does not ask the owner to run commands, paste output, or "check manually".

1. **This covers everything runnable:** installing dependencies, tests, lint/format/type checks, builds, data syncs and backfills, migrations, CLI commands, all git operations (see Rule 6A), starting a server or the dashboard and checking it responds, and `live` / `llm` tests when the keys are already in the environment.
2. **Long jobs** (backfills, backtests) are run by the AI in the background, and their completion and output are checked by the AI. They are not handed off with "run this and tell me".
3. **Visual checks** (dashboard pages, reports, charts) are done by the AI with its browser and screenshot tools, not by asking the owner to look.
4. **Try first, never predict.** The AI attempts the command and reads the actual error before concluding it cannot run it.
5. **Only these exceptions allow involving the owner:**
   - **Technically impossible for the AI:** blocked by the sandbox or a permission it does not have (for example opening apps with `open`, binding a local port when that is disabled), an interactive sign-in, 2FA, a password or `sudo` prompt, or a GUI action its tools cannot perform.
   - **Missing owner secrets:** an API key or credential that is not in the environment. The AI never asks the owner to type secrets into chat; it asks them to add the key to `.env`, then runs the command itself.
   - **Needs approval first:** destructive or irreversible actions (deleting data, rewriting git history, force-pushing, publishing). The AI asks, and once approved, **it runs the command itself**.
   - **Forbidden outright:** anything Rule 5 prohibits (real-money or live endpoints). These are never run by anyone on the AI's instruction.
6. **When an exception applies,** the AI must:
   - say exactly what blocked it (quote the error);
   - give the owner the single exact command in its own fenced `bash` block, plus what output to expect;
   - keep the affected task or criterion `BLOCKED` until real output is available;
   - if the owner pastes the full output in chat, record it as evidence labelled **"run by owner — AI could not run: <reason>"**. This is acceptable only for commands the AI genuinely could not run.
7. Sentences like "please run…", "you can verify by…" or "check that it works on your machine" are not allowed in place of the AI doing it, unless one of the exceptions above applies and is stated.

---

## Rule 6A — Git operations are the AI's job, and the AI checks them

The owner never runs git commands or inspects git state by hand. The AI does every git operation itself, and verifies each one itself (Rule 6).

1. **The AI performs every git operation a stage needs:**
   - starting from an up-to-date `main`, and creating or using the stage's branch or worktree;
   - staging only the intended files, and committing as `stage-NN: <title>` (plus the attribution trailer);
   - merging `main` into the stage branch when `main` has moved on;
   - merging the finished stage into `main` once the stage is `DONE`;
   - resolving merge conflicts, and keeping the records on `main` (`PROGRESS.md`, the stage folder) in sync.
2. **Check before every commit:**
   - review the staged diff (`git diff --cached --stat` and the diff itself) and confirm it contains only the stage's intended files;
   - never commit secrets, `.env`, the data lake (`data/`), caches or large generated files; confirm `.gitignore` covers them.
3. **Verify after every git operation, and record the evidence:**
   - `git status --short` shows a clean tree (or only expected files);
   - `git log --oneline` shows the new commit or merge commit, with the right message;
   - `git diff --stat <base>..HEAD` touches only the intended files;
   - **after any merge,** run `uv run pytest -q` and `make check` on the merged result and confirm they are green, and confirm `git branch --merged main` lists the stage branch;
   - write the commit hashes, the merge commit and the post-merge check results into the stage's `worklog.md`, and into `verification.md` / the `PROGRESS.md` handoff note when they are part of the stage's evidence.
4. **`main` must never be left red.** A stage branch is merged into `main` only after the stage is `DONE`, and only if the merged result passes the full verification. If the merged result fails, the AI fixes it on the stage branch first; the stage is not `DONE` until the merged result is green.
5. **Conflicts:** the AI resolves them itself, following Rule 3 precedence. If a resolution would change the meaning of a higher-ranked document, or the right answer is genuinely unclear, it asks the owner.
6. **Failures** (a lock file, a failing hook, a merge conflict, a detached HEAD) are diagnosed and fixed by the AI. The owner is involved only if a Rule 6 exception truly applies.
7. **Remote:** there is none at the moment. If one is configured later, the AI pushes `main` and the stage branch after each verified commit or merge (a normal push, never a force-push), and confirms the push with `git status -sb`.
8. **Still needs the owner's approval first** (a Rule 6 exception): rewriting history (rebasing or amending commits already on `main`, `reset --hard` that discards work), force-pushing, deleting branches or worktrees that hold unmerged work, and deleting tags. Once approved, the AI runs the operation itself and verifies it. Cleaning up branches that are already merged may be proposed, and is done only with approval.

---

## Rule 7 — Hand off to a new chat instead of compacting

When a chat's context is close to full, the AI does **not** let the conversation be compacted (summarised) and carry on in the same chat. Instead it writes a **hand-off file** and starts a **new chat** that continues the work from that file. The new chat is a continuation of the original one.

1. **When to hand off.** As soon as the context is close to full, at the next safe boundary (see 2). Signs include a warning that compaction is near, a very long session with many large outputs, or a large remaining workload that clearly will not fit. Hand off early rather than late: a hand-off written before compaction is accurate; one written after it is not.
2. **Reach a safe boundary first.**
   - Finish or cleanly back out of the current step, so the code is in a consistent state. Never hand off in the middle of an edit or of a running check whose result is unknown.
   - Run the tests covering what changed, and note the result.
   - Commit the work in progress on the stage branch as `stage-NN (part k): <what>`, following Rule 6A (staged-diff review, `git status` / `git log` checks). Nothing is left uncommitted unless the hand-off file says exactly what and why.
   - Update the stage's `worklog.md` with exactly where the work stopped.
3. **Write the hand-off file.**
   - **For stage work,** it goes in `Planning/stages/stage-NN-<slug>/handoffs/handoff-<k>.md` (k = 1, 2, …).
   - **For owner-requested work that belongs to no stage** (rule changes, checkpoints), it goes in `Planning/handoffs/YYYY-MM-DD-<slug>.md`. This is the one allowed exception to Rule 2A.2.
   - Commit the hand-off file too. It must let a chat with no memory of this one continue without asking anything, so it contains:

   ```
   # Hand-off <k> — <stage or task> — YYYY-MM-DD HH:MM
   - Original request (owner's words): …
   - Stage / status: NN — <title> — IN PROGRESS (part k)
   - Branch / worktree / last commit: <branch> · <path> · <hash> "<message>"
   - Done so far (with evidence): <tasks, test results, commit hashes>
   - Current position: plan.md task <n>, step <m>: <what exactly is half-way>
   - Next steps, in order: 1. … 2. … 3. …
   - Open problems / failing tests / suspicions: …
   - Decisions taken and decisions still owed to the owner: …
   - Environment notes: <commands, env vars, sandbox workarounds, long jobs running and where their output goes>
   - Files to read first: Project.md, PROGRESS.md, brief.md, plan.md, worklog.md, <others>
   - Not yet verified (must be re-run in the new chat before any DONE claim): …
   ```
4. **Start the new chat yourself** (Rule 6). Use the session tools: hand the task off to a new session, or start a session linked to this one. Give it a prompt telling it to read `Project.md`, then `Planning/PROGRESS.md`, then the hand-off file, and to continue from "Next steps". Only if no session tool is available does the AI give the owner the exact one-line prompt to paste into a new chat.
5. **The original chat stops working on the task** once the new chat has started. It tells the owner where the hand-off file is and links the new chat. Only one chat works on a task at a time.
6. **The new chat checks before it continues.** It follows Rule 0, reads the hand-off file, then confirms the stated state is true: the branch, `git status` and `git log` match, and the tests named as green are still green. Any mismatch is investigated and recorded first. It never relies on the old chat's results for a `DONE` claim: verification is re-run in the chat that claims `DONE` (Rule 1.2).
7. **If compaction happens anyway,** the AI treats the summary as unreliable. It rebuilds the true state from the files and git (`plan.md`, `worklog.md`, `git log`, `git status`), writes the hand-off file from those, and continues in a new chat as above.

---

## Rule 8 — Quality over convenience, in every open task

The goal of this project is a trading system that **performs well and can be trusted**: correct, point-in-time-honest, robust out of sample, with good risk-adjusted returns after realistic costs. For **every open task** (stage tasks, follow-ups, backlog items being worked on, fixes), the AI chooses the approach that best serves that goal, **not the one that is quickest, easiest or cheapest for the AI**.

1. **When quality and convenience conflict, quality wins.** Extra time, compute, tests, data work and LLM spend (within the configured budgets) are acceptable costs. Taking longer is never a reason to cut a corner.
2. **Corners that may not be cut:**
   - simplifying a model, formula or data path below what the brief and design spec call for, because the proper version is harder;
   - mocking or faking real behaviour just to make something pass, or testing only the happy path;
   - hard-coding values, skipping edge cases (missing data, delistings, splits, holidays, zero volume, NaN), or ignoring warnings;
   - weakening an acceptance criterion, a limit, a test or a check, or quietly lowering scope to finish sooner;
   - leaving a known bug, data error or look-ahead risk unfixed and unrecorded;
   - picking a library, data source or algorithm because it is easier, when a better-fitting one is available and allowed.
3. **What "quality" means here** (in priority order):
   - correctness and point-in-time integrity (no look-ahead, no survivorship bias that could be avoided);
   - risk control (limits, drawdowns, fail-safe behaviour);
   - realistic, honest performance measurement (costs, slippage, out-of-sample testing);
   - robustness (stability across regimes and parameters);
   - then speed and efficiency.
   **Chasing in-sample backtest numbers is not quality:** overfitting, peeking at the hold-out, or tuning to one period is a defect, however good it looks.
4. **Quality within scope, not scope creep.** Rule 2.4 still holds. The AI does the in-scope work properly. A quality improvement outside the stage's scope goes into `PROGRESS.md › Backlog` with its expected impact, or is raised with the owner if it matters now. It is never silently skipped.
5. **Record the trade-off.** When a meaningful choice between a convenient and a better approach comes up, the AI writes it into the stage's `worklog.md` (or the Decisions log, if it affects the plan): the options, what was chosen, and why.
6. **If the best approach is truly impossible** (data unavailable, a tool limitation), the AI says so plainly, uses the best feasible alternative, documents the gap and its likely effect on performance, and puts the proper fix in the Backlog. It does not present the alternative as equivalent.

---

## Quick checklist (copy into each stage chat)

- [ ] Read Project.md in full and stated the current stage and status
- [ ] Gate check: previous stage `DONE` with evidence; full test suite green
- [ ] Copied `Planning/stages/_template/` into the stage folder; read `brief.md`; wrote `plan.md`
- [ ] Executed tasks in order, verifying each one, and logged each in `worklog.md`
- [ ] Chose quality over convenience in every task; recorded meaningful trade-offs and out-of-scope improvements (Rule 8)
- [ ] If context ran low: committed at a safe boundary, wrote the hand-off file, started the new chat myself, and stopped here (Rule 7)
- [ ] Checked collisions against the overall plan; logged any in the Decisions log
- [ ] Ran every command myself (Rule 6); any owner-run command has a stated exception and is labelled
- [ ] Ran the full verification in this chat; output read and saved to `artifacts/`; all criteria evidenced
- [ ] Wrote `verification.md` and the summary in `PROGRESS.md`, then ticked the box and set `DONE`
- [ ] Committed `stage-NN: <title>` after reviewing the staged diff; verified with `git status` / `git log` (Rule 6A)
- [ ] Merged into `main` after `DONE`; re-ran `uv run pytest -q` and `make check` on the merged result (green); recorded hashes in `worklog.md`
