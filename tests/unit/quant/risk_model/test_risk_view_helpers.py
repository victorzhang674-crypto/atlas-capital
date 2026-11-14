"""Phase 2 checkpoint, Tier 4 item 3 (Stage 18 code): `covariance_from_view` (the returns a
`DataView` knows, over `quant.risk_model.lookback` sessions, into the configured estimator)
and a hashable `VolForecast`."""

import math
from datetime import date

import pandas as pd
import pytest
from factor_world import AS_OF, STOCKS, build_factor_world
from pydantic import ValidationError

from atlas.core.config import AtlasConfig, RiskModelConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.risk_model import VolForecast, covariance_estimator, covariance_from_view

CFG = AtlasConfig()


@pytest.fixture(scope="module")
def world_lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    lake = Lake(tmp_path_factory.mktemp("lake") / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    return lake


# --- covariance_from_view ----------------------------------------------------------------------


def test_lookback_setting() -> None:
    assert RiskModelConfig().lookback == 252
    with pytest.raises(ValidationError, match="lookback"):
        RiskModelConfig(lookback=40)  # below min_history (63)


def test_equals_the_estimator_on_the_views_returns(world_lake: Lake) -> None:
    view = DataView(world_lake, AS_OF, CFG)
    names = list(STOCKS[:6])
    got = covariance_from_view(view, names)
    want = covariance_estimator(config=CFG).estimate(view.returns(names, 252))
    assert got.method == want.method == "ledoit_wolf"
    pd.testing.assert_frame_equal(got.cov, want.cov)
    assert got.n_obs == want.n_obs == 252
    assert got.end == want.end


def test_method_lookback_and_options_pass_through(world_lake: Lake) -> None:
    view = DataView(world_lake, AS_OF, CFG)
    names = list(STOCKS[:4])
    got = covariance_from_view(view, names, method="ewma", lookback=100, half_life=30)
    want = covariance_estimator("ewma", config=CFG, half_life=30).estimate(view.returns(names, 100))
    assert got.n_obs == 100
    pd.testing.assert_frame_equal(got.cov, want.cov)


def test_uses_the_views_config_and_only_known_bars(world_lake: Lake) -> None:
    """The view's config decides the default method and lookback; nothing after its as-of is
    read (the covariance ends at the last known session)."""
    cfg = AtlasConfig.model_validate(
        {"quant": {"risk_model": {"default_method": "sample", "lookback": 80}}}
    )
    view = DataView(world_lake, date(2024, 1, 2), cfg)
    got = covariance_from_view(view, list(STOCKS[:3]))
    assert (got.method, got.n_obs) == ("sample", 80)
    assert got.end < date(2024, 1, 2)


# --- VolForecast -------------------------------------------------------------------------------


def _forecast(**params: float) -> VolForecast:
    return VolForecast(variance=1e-4, method="blend", n_obs=500, params=params)


def test_vol_forecast_is_hashable_and_hashes_by_value() -> None:
    a = _forecast(ewma=0.4, garch=0.4, realised=0.2)
    b = _forecast(realised=0.2, garch=0.4, ewma=0.4)  # same parameters, other order
    c = _forecast(ewma=0.5, garch=0.3, realised=0.2)
    assert a == b and hash(a) == hash(b)
    assert a != c
    assert len({a, b, c}) == 2
    assert {a: "x"}[b] == "x"
    assert math.isclose(a.vol, 0.01)
