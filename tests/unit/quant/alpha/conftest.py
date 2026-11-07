"""Alpha tests also use the Stage 16 factor world in `tests/unit/quant/factors/`."""

import sys
from pathlib import Path

_FACTOR_TESTS = Path(__file__).resolve().parents[1] / "factors"
if str(_FACTOR_TESTS) not in sys.path:
    sys.path.insert(0, str(_FACTOR_TESTS))
