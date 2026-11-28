"""The recorded live run of the news analyst (Stage 31): AAPL as of 2024-05-01.

The fixture folder holds `lake/` (the slice the run read, built by the stage's
`artifacts/record_fixture.py`: AAPL's sector, news since 2024-04-01, events and reactions
since 2022), `llm_cache.jsonl` (`atlas llm cache export`), `expected_report.json` and
`run.json`. `replay_lake` (Stage 29's helper) copies the slice and loads the cache, so a test
needs no lake, no key and no provider.
"""

from datetime import date

from recorded import REPO_ROOT

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recorded" / "stage-31-news-aapl-2024-05-01"
SYMBOL = "AAPL"
AS_OF = date(2024, 5, 1)
