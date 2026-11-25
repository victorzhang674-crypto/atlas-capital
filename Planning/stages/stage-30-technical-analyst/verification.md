# Stage NN — <title> · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage NN — YYYY-MM-DD
- Commands run (this chat):
  - `uv run pytest -q` → <N passed, 0 failed, M deselected (live/llm)> · raw: artifacts/pytest.txt
  - `uv run ruff check .` → <All checks passed>
  - `uv run ruff format --check .` → <N files already formatted>
  - `uv run mypy src/atlas` → <Success: no issues found>
  - `<brief's Verify command>` → <key output line> · raw: artifacts/<file>
- Acceptance criteria (one line per criterion in brief.md):
  - [ ] <criterion 1> — evidence: <test name / output / file>
  - [ ] <criterion 2> — evidence: …
- Waivers: none | <criterion — owner waiver quote — date>

**Result:** NOT YET VERIFIED
<!-- Change to "ALL CRITERIA VERIFIED" only when every box above is ticked with evidence. -->
