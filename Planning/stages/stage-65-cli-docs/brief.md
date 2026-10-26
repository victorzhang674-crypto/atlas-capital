# Stage 65 — CLI polish & documentation
**Phase:** 9 · **Size:** S · **Depends on:** 64

## Goal
Make Atlas usable by someone who did not build it: a coherent CLI and complete docs.

## In scope
- CLI review: consistent verbs and flags, `--help` text for every command, `atlas doctor` (checks env keys, data freshness, config validity, and the paper-only guard), shell completion
- Docs (`docs/`): `index.md`, `quickstart.md` (zero → first research → first backtest → paper), `architecture.md` (with the graph diagrams exported in Stage 28), `agents.md` (each agent: role, inputs, outputs, prompt version), `risk.md` (every limit and breaker), `data.md`, `backtesting.md` (methodology, pitfalls, how to read the tear sheet), `operations.md` (runbook: daily checks, incidents, kill switch, recon breaks), `configuration.md` (every key, generated from the models)
- Generated config reference via a script reading the Pydantic models
- Update the project README with a feature overview and an honest limitations section

## Files
`src/atlas/cli/*`, `docs/**`, `scripts/gen_config_docs.py`, tests (CLI help snapshot tests)

## Acceptance criteria
- Every command has help text; `atlas doctor` passes on a configured machine; the docs build (mkdocs, optional) or render cleanly as Markdown.

## Verify
`uv run atlas --help && uv run atlas doctor`
