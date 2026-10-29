"""Stage 10 live smoke test against the real FRED API (`-m live`; needs FRED_API_KEY in .env)."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from macro_helpers import REPO_ROOT

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.macro import Macro, build_fred_client, ingest_macro


@pytest.mark.live
def test_live_fred_cpi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    cfg = load_config("dev", overrides={"data": {"macro": {"observation_start": "2018-01-01"}}})
    if cfg.secrets.fred_api_key is None:
        pytest.skip("FRED_API_KEY is not set (.env): the FRED API needs a key")
    lake = Lake(tmp_path / "lake")
    with build_fred_client(cfg, lake) as client:
        report = ingest_macro(lake, client, ["CPIAUCSL", "DGS10"], config=cfg)
    assert report.series_ok == ("CPIAUCSL", "DGS10")
    macro = Macro(lake, cfg)
    frame = macro.pit(["CPIAUCSL"], date(2020, 4, 15), timedelta(days=60))
    assert frame.loc["2020-03-01", "CPIAUCSL"] == 257.953  # first release, 2020-04-10
    early = macro.pit(["CPIAUCSL"], date(2020, 4, 5), timedelta(days=60))
    assert "2020-03-01" not in early.index.strftime("%Y-%m-%d")
    assert (
        macro.pit(["DGS10"], date(2020, 3, 4), timedelta(days=5)).loc["2020-03-02", "DGS10"] == 1.10
    )
