# Stage NN — <title> · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** IN PROGRESS
- **Gate check:** Stage NN−1 is DONE (see its verification.md); `uv run pytest -q` → <result> (YYYY-MM-DD)

## Collisions with the overall plan
None found. Otherwise: <conflicting texts → resolution, also logged in PROGRESS.md › Decisions log>

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 1 — <name>
- Files: create `…` · modify `…` · test `tests/unit/…`
- [ ] Write the failing test: `<test name>`
- [ ] Run `uv run pytest <path>::<test> -q` → expect FAIL (<reason>)
- [ ] Implement the minimal code
- [ ] Run the same command → expect PASS
- [ ] Refactor; run the module's tests → PASS

### Task 2 — …
