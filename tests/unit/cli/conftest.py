"""CLI tests share the Stage 14 fake data world in `tests/unit/data/sync_helpers.py` and the
Stage 17 research world in `tests/unit/quant/research/research_world.py`."""

import sys
from pathlib import Path

_DATA_TESTS = Path(__file__).resolve().parents[1] / "data"
if str(_DATA_TESTS) not in sys.path:
    sys.path.insert(0, str(_DATA_TESTS))

_RESEARCH_TESTS = Path(__file__).resolve().parents[1] / "quant" / "research"
if str(_RESEARCH_TESTS) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_TESTS))

_REGIME_TESTS = Path(__file__).resolve().parents[1] / "quant" / "regime"
if str(_REGIME_TESTS) not in sys.path:
    sys.path.insert(0, str(_REGIME_TESTS))
