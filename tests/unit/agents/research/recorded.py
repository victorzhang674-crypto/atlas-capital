"""The recorded live run of the fundamental analyst (Stage 29): AAPL as of 2024-05-01.

The fixture folder holds `lake/` (the slice the run read, `atlas data slice`), `llm_cache.jsonl`
(`atlas llm cache export`), `expected_report.json` and `run.json` (what the run recorded:
status, calls, tokens, cost). `replay_lake` copies the slice into a scratch folder and loads
the cache, so a test needs no lake, no key and no provider.
"""

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake, get_database
from atlas.llm import LLMCache
from atlas.llm.cache import read_export

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recorded" / "stage-29-fundamental-aapl-2024-05-01"
SYMBOL = "AAPL"
AS_OF = date(2024, 5, 1)


def replay_config(lake: Path, mode: str = "replay_only") -> AtlasConfig:
    """The default settings with `lake` as the data lake and the LLM cache in `mode` (no
    secrets: `AtlasConfig` reads none by itself)."""
    return AtlasConfig.model_validate(
        {"data": {"lake_path": str(lake)}, "llm": {"cache": {"mode": mode}}}
    )


def replay_lake(root: Path) -> Lake:
    """A writable copy of the fixture's lake under `root`, with the recorded cache loaded."""
    target = root / "lake"
    shutil.copytree(FIXTURE / "lake", target)
    lake = Lake(target)
    LLMCache(get_database(lake)).load(read_export(FIXTURE / "llm_cache.jsonl"))
    return lake


def recorded_run() -> dict[str, Any]:
    """`run.json`: what the live run recorded."""
    return dict(json.loads((FIXTURE / "run.json").read_text(encoding="utf-8")))
