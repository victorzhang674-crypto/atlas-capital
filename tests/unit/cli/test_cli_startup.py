"""Phase 2 checkpoint, Tier 4 item 1: `atlas` starts without the heavy libraries. matplotlib
(tear sheet figures), statsmodels (Engle-Granger), hmmlearn and scikit-learn (the HMM regime)
are imported by the functions that use them, so `atlas --help` and the data commands do not
pay for them (about 11 s on a cold cache)."""

import subprocess
import sys

HEAVY = ("matplotlib", "statsmodels", "hmmlearn", "sklearn")


def _loaded_after(statement: str) -> set[str]:
    code = f"import sys\n{statement}\nprint(' '.join(sorted(m for m in sys.modules)))"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    return {name.split(".")[0] for name in out.stdout.split()}


def test_cli_imports_no_heavy_library() -> None:
    loaded = _loaded_after("import atlas.cli.app")
    assert not loaded & set(HEAVY), sorted(loaded & set(HEAVY))


def test_quant_packages_import_no_heavy_library() -> None:
    loaded = _loaded_after("import atlas.quant.research, atlas.quant.regime, atlas.quant.statarb")
    assert not loaded & set(HEAVY), sorted(loaded & set(HEAVY))
