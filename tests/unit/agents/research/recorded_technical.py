"""The recorded live run of the technical analyst (Stage 30): AAPL as of 2024-05-01.

The fixture folder holds `lake/` (the slice the run read, `atlas data slice`), `regime.json`
(the day's market regime computed on the main lake, passed as `inputs["regime"]`),
`llm_cache.jsonl` (`atlas llm cache export`), `expected_report.json` and `run.json`.
`replay_lake` (shared with Stage 29's helper) copies the slice and loads the cache, so a test
needs no lake, no key and no provider.
"""

from datetime import date

from recorded import REPO_ROOT

from atlas.quant.regime import RegimeState

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recorded" / "stage-30-technical-aapl-2024-05-01"
SYMBOL = "AAPL"
AS_OF = date(2024, 5, 1)


def recorded_regime() -> RegimeState:
    """`regime.json`: the regime the run was given."""
    return RegimeState.model_validate_json((FIXTURE / "regime.json").read_text(encoding="utf-8"))
