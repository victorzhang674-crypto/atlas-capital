# Stage 01 — Project scaffolding & toolchain · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 01 — 2026-10-26
- Commands run (this chat):
  - `uv run pytest -q` → 25 passed, 0 failed, 0 deselected · raw: artifacts/pytest.txt
  - `uv run pytest -q --collect-only -m "live or llm or integration"` → no tests collected (25 deselected): the default run reaches no live services · raw: artifacts/pytest-external-markers.txt
  - `uv run ruff check .` → All checks passed! · raw: artifacts/ruff-check.txt
  - `uv run ruff format --check .` → 25 files already formatted · raw: artifacts/ruff-format.txt
  - `uv run mypy src/atlas` → Success: no issues found in 18 source files · raw: artifacts/mypy.txt
  - `make check` (working tree) → lint/format/mypy clean, 25 passed, exit 0 · raw: artifacts/make-check.txt
  - The four commands above, plus `make check`, are the re-run after the venv fix (see worklog, 2026-10-26). An earlier `make check` in this chat failed with `ModuleNotFoundError: No module named 'atlas'` because iCloud had hidden the venv's `.pth` files.
  - `uv sync --group dev && make check && git log --oneline` (brief's Verify, run in a fresh `git clone` in `$TMPDIR`, after the final code/`.gitignore` change) → see artifacts/clean-clone-verify.txt for the exact output and commit hashes
  - `git diff --stat 956d5f7 HEAD -- Project.md` → empty; `git diff 956d5f7 HEAD -- CLAUDE.md` → 0 removed lines; `head -5 CLAUDE.md` ends with `@Project.md` · raw: artifacts/docs-unchanged.txt
- Acceptance criteria (one line per criterion in brief.md):
  - [x] `make check` is green on a clean clone after `uv sync` — evidence: artifacts/clean-clone-verify.txt (exit=0, 25 passed, lint/format/mypy clean)
  - [x] `git log` shows the planning commit and the stage-01 commit — evidence: same file, `git log --oneline` lists `956d5f7 planning: …` and `stage-01: …` (afterwards the stage-01 commit was amended only to add that artifact file, so its final hash differs; the message is unchanged)
  - [x] `CLAUDE.md` still begins with the `@Project.md` import; `Project.md` is unchanged — evidence: `test_claude_md_imports_project_rules_first` passed; artifacts/docs-unchanged.txt (header lines 1–5 intact, 0 removed lines, empty `Project.md` diff)
- Brief's Tests section:
  - [x] `test_version` — tests/unit/test_version.py::test_version passed
  - [x] a test that imports every subpackage — tests/unit/test_package_layout.py::test_subpackage_imports[17 params], `test_no_unexpected_subpackages`, `test_every_source_dir_has_init` passed
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
