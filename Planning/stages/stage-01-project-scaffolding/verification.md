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

#### Phase 0 checkpoint follow-up (Tier 4) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): pre-commit hooks run the locked tools offline (still optional, not installed); a first `integration` test and `make test-integration`; no CI while there is no remote. Re-run in this chat on the final working tree (base commit `81ada1e`):
- New toolchain test failed on `81ada1e`'s config and PASSES now; old hooks could not start offline; `-m integration` collected nothing on `81ada1e` (exit 5) · raw: [artifacts/checkpoint-2026-11-02-tier4-red-before-fix.txt](artifacts/checkpoint-2026-11-02-tier4-red-before-fix.txt)
- `uv run pre-commit run --all-files` → ruff, ruff format, end-of-file, trailing whitespace all Passed, exit 0, no file changed · raw: [artifacts/checkpoint-2026-11-02-tier4-pre-commit.txt](artifacts/checkpoint-2026-11-02-tier4-pre-commit.txt)
- `uv run pytest -m integration -q` → 1 passed, 2127 deselected · `uv lock --check`, `uv sync --group dev` → exit 0 · `uv run pytest -q` → 2116 passed, 0 failed, 12 deselected, exit 0; `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 206 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 88 source files; `make check` → exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier4-verify.txt](artifacts/checkpoint-2026-11-02-tier4-verify.txt)
- The stage's acceptance criteria still hold: `make check` green; `CLAUDE.md` and `Project.md` untouched by this change (`test_claude_md_imports_project_rules_first`, `test_precommit_excludes_governing_docs` pass).
- Brief's Verify `uv sync --group dev && make check && git log --oneline` on a fresh clone of `be247fb` → exit 0; 2116 passed; `git log` shows `be247fb`; `make test-integration` → 1 passed · raw: [artifacts/checkpoint-2026-11-02-tier4-clean-clone-verify.txt](artifacts/checkpoint-2026-11-02-tier4-clean-clone-verify.txt)
- On merged `main` (`be247fb`, fast-forward from `81ada1e`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv lock --check` exit 0; `uv run pytest -q` → 2116 passed; `uv run pytest -m integration -q` → 1 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 206 files already formatted; `mypy src/atlas` → Success (88 files); the briefs' Verify commands → 207 passed (02), 136 passed (06); `uv run pre-commit run --all-files` → all four hooks Passed; `make check` → exit 0 (2116 passed); `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all commands exit 0 · raw: [stage-01 artifacts/checkpoint-2026-11-02-tier4-merged-main-check.txt](artifacts/checkpoint-2026-11-02-tier4-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (CI) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): private GitHub remote `victorzhang674-crypto/atlas-capital`; GitHub Actions CI runs `make check` and `make test-integration`.
- History scanned before the first push: no secrets, no `.env`, no data (worklog)
- New CI test failed without the workflow, then failed on `@v7`/`@v10` tags, and passes now; local run of the CI steps green · raw: [artifacts/checkpoint-2026-11-02-ci-local-verify.txt](artifacts/checkpoint-2026-11-02-ci-local-verify.txt), [artifacts/checkpoint-2026-11-02-ci-fix-local-verify.txt](artifacts/checkpoint-2026-11-02-ci-fix-local-verify.txt)
- CI runs 1 and 2 failed (action tag; one help-text test) and were fixed · raw: [artifacts/checkpoint-2026-11-02-ci-run-1-failed.txt](artifacts/checkpoint-2026-11-02-ci-run-1-failed.txt), [artifacts/checkpoint-2026-11-02-ci-run-2-failed.txt](artifacts/checkpoint-2026-11-02-ci-run-2-failed.txt)
- CI run 3 `41505703051` on `f514eb2` (= `main`): success — ruff clean, 206 files formatted, mypy Success (88 files), 2118 passed, 12 deselected; integration 1 passed · raw: [artifacts/checkpoint-2026-11-02-ci-run-3-passed.txt](artifacts/checkpoint-2026-11-02-ci-run-3-passed.txt)
- Local: `make check` → exit 0 (2118 passed) under plain, `GITHUB_ACTIONS=true` and `FORCE_COLOR=1` environments · raw: [stage-14 artifacts/checkpoint-2026-11-02-ci-help-test.txt](../stage-14-data-cli/artifacts/checkpoint-2026-11-02-ci-help-test.txt)

**Result:** ALL CRITERIA VERIFIED

#### Phase 2 checkpoint follow-up (timing budgets run alone) — 2026-11-13
- Commands run (this chat): `make check` → exit 0 (ruff clean, 386 files formatted, mypy clean, 4166 + 3 passed); `uv run pytest -q` → 4169 passed, 71 deselected · raw: [artifacts/phase2-checkpoint-onepass-timing-checks.txt](artifacts/phase2-checkpoint-onepass-timing-checks.txt); red first: [artifacts/phase2-checkpoint-onepass-timing-red.txt](artifacts/phase2-checkpoint-onepass-timing-red.txt); the CI failure it fixes: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-ci-failed-log.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-ci-failed-log.txt)
- Acceptance criteria unchanged and still evidenced; the three budgets keep their limits.

**Result:** ALL CRITERIA VERIFIED
