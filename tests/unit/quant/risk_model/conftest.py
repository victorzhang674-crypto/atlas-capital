"""Lets the Stage 19 loader tests reuse the Stage 16 synthetic-lake builder
(`tests/unit/quant/factors/factor_world.py`) when this directory is run on its own."""

import sys
from pathlib import Path

_FACTORS = str(Path(__file__).resolve().parents[1] / "factors")
if _FACTORS not in sys.path:
    sys.path.append(_FACTORS)
