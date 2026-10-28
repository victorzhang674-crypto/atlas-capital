"""Stage 09 live smoke test against real SEC EDGAR (`-m live`; needs SEC_USER_AGENT in .env)."""

from datetime import date
from pathlib import Path

import pytest
from fundamentals_helpers import REPO_ROOT

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals import (
    Fundamentals,
    build_cik_map,
    build_edgar_client,
    ingest_fundamentals,
)


@pytest.mark.live
def test_live_edgar_aapl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    cfg = load_config("dev")
    if cfg.secrets.sec_user_agent is None:
        pytest.skip("SEC_USER_AGENT is not set (.env): EDGAR needs a contact User-Agent")
    lake = Lake(tmp_path / "lake")
    with build_edgar_client(cfg, lake) as client:
        cik_map = build_cik_map(client, cfg)
        assert cik_map.cik("AAPL") == 320193
        assert cik_map.cik("CELG") == 816284  # former member, from config/cik_overrides.yaml
        report = ingest_fundamentals(lake, client, ["AAPL", "CELG"], cik_map=cik_map, config=cfg)
    assert report.symbols_ok == ("AAPL", "CELG")
    reader = Fundamentals(lake, cfg)
    assert reader.pit("AAPL", date(2023, 11, 3))["revenue_ttm"] == 383_285e6
    assert reader.pit("CELG", date(2019, 3, 1))["revenue_ttm"] == 15_281e6  # FY2018 10-K
