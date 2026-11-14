"""Lets the feature tests reuse the Stage 16 synthetic-lake builder
(`tests/unit/quant/factors/factor_world.py`), which has a split known at its as-of."""

import sys
from pathlib import Path

_FACTORS = str(Path(__file__).resolve().parents[1] / "factors")
if _FACTORS not in sys.path:
    sys.path.append(_FACTORS)
