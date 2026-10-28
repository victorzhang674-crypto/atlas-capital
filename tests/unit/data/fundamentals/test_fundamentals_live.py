"""Stage 09 live smoke test against real SEC EDGAR (`-m live`; needs SEC_USER_AGENT in .env)."""

from datetime import date
from pathlib import Path

import pytest
from fundamentals_helpers import REPO_ROOT

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals import CikMap, Fundamentals, build_edgar_client, ingest_fundamentals


@pytest.mark.live
def test_live_edgar_aapl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    cfg = load_config("dev")
    if cfg.secrets.sec_user_agent is None:
        pytest.skip("SEC_USER_AGENT is not set (.env): EDGAR needs a contact User-Agent")
    lake = Lake(tmp_path / "lake")
    with build_edgar_client(cfg, lake) as client:
        cik_map = CikMap.from_company_tickers(client.company_tickers())
        assert cik_map.cik("AAPL") == 320193
        report = ingest_fundamentals(lake, client, ["AAPL"], cik_map=cik_map, config=cfg)
    assert report.symbols_ok == ("AAPL",)
    s = Fundamentals(lake, cfg).pit("AAPL", date(2023, 11, 3))
    assert s["revenue_ttm"] == 383_285e6
