"""CLI tests share the Stage 14 fake data world in `tests/unit/data/sync_helpers.py`."""

import sys
from pathlib import Path

_DATA_TESTS = Path(__file__).resolve().parents[1] / "data"
if str(_DATA_TESTS) not in sys.path:
    sys.path.insert(0, str(_DATA_TESTS))
