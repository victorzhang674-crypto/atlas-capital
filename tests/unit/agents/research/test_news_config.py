"""Stage 31 Task 3: the news analyst's settings (`agents.news`)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import AtlasConfig, NewsAnalystConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_defaults() -> None:
    settings = AtlasConfig().agents.news
    assert settings == NewsAnalystConfig()
    assert settings.lookback_days == 21
    assert settings.max_articles == 25
    assert settings.events_horizon_days == 30
    assert settings.reactions == 8
    assert settings.sector_peers == 8
    assert settings.sector_articles_per_peer == 3
    assert settings.sector_articles == 10
    assert settings.max_catalysts == 8
    assert settings.empty_news_confidence_cap == 0.2


def test_fund_yaml_matches_the_defaults() -> None:
    loaded = load_config("dev", config_dir=REPO_ROOT / "config").agents
    assert loaded.news == NewsAnalystConfig()


def test_every_key_of_the_news_block_is_commented_in_fund_yaml() -> None:
    lines = (REPO_ROOT / "config" / "fund.yaml").read_text().splitlines()
    start = lines.index("  news:", lines.index("agents:"))  # `data.news` comes earlier
    block = []
    for line in lines[start + 1 :]:
        if line and not line.startswith("    "):
            break
        block.append(line)
    keys = [ln for ln in block if ln.strip() and not ln.strip().startswith("#")]
    assert len(keys) == len(NewsAnalystConfig.model_fields)
    assert all("#" in ln for ln in keys)


@pytest.mark.parametrize(
    "bad",
    [
        {"lookback_days": 0},
        {"lookback_days": 91},
        {"max_articles": 0},
        {"max_articles": 101},
        {"events_horizon_days": 0},
        {"reactions": 0},
        {"reactions": 41},
        {"sector_peers": 0},
        {"sector_articles_per_peer": 0},
        {"sector_articles": 0},
        {"sector_articles_per_peer": 5, "sector_articles": 4},
        {"max_catalysts": 0},
        {"empty_news_confidence_cap": -0.1},
        {"empty_news_confidence_cap": 1.1},
        {"lookback_days": True},
        {"lookback_days": 14.5},
        {"unknown_key": 1},
    ],
)
def test_bad_settings_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NewsAnalystConfig.model_validate(bad)


def test_the_cap_may_be_zero_or_one() -> None:
    assert NewsAnalystConfig(empty_news_confidence_cap=0.0).empty_news_confidence_cap == 0.0
    assert NewsAnalystConfig(empty_news_confidence_cap=1.0).empty_news_confidence_cap == 1.0
