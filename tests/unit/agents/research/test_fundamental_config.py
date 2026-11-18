"""Stage 29 Task 1: the fundamental analyst's settings (`agents.fundamental`) and the
receivables concept its red flags need."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from atlas.core.config import (
    AgentsConfig,
    AtlasConfig,
    FundamentalAnalystConfig,
    RedFlagsConfig,
    load_config,
)
from atlas.data.fundamentals import FACT_COLUMNS, metric_names, resolve_concept
from atlas.data.fundamentals.mapping import load_concept_map

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_defaults() -> None:
    settings = AtlasConfig().agents.fundamental
    assert settings == FundamentalAnalystConfig()
    assert settings.summary_quarters == 8
    assert settings.valuation_years == 5
    assert settings.min_valuation_quarters == 4
    assert settings.min_sector_names == 5
    assert settings.price_tolerance_days == 10
    assert settings.share_count_tolerance_days == 100
    assert settings.red_flags == RedFlagsConfig()


def test_fund_yaml_matches_the_defaults() -> None:
    loaded = load_config("dev", config_dir=REPO_ROOT / "config").agents
    assert loaded == AgentsConfig()
    assert loaded.fundamental == FundamentalAnalystConfig()


@pytest.mark.parametrize(
    "bad",
    [
        {"summary_quarters": 0},
        {"summary_quarters": 41},
        {"valuation_years": 0},
        {"min_valuation_quarters": 0},
        {"min_sector_names": 0},
        {"price_tolerance_days": -1},
        {"share_count_tolerance_days": -1},
        {"unknown_key": 1},
        {"red_flags": {"dilution_max": 0}},
    ],
)
def test_bad_settings_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FundamentalAnalystConfig.model_validate(bad)


def test_settings_can_be_overridden() -> None:
    cfg = AtlasConfig.model_validate(
        {"agents": {"fundamental": {"summary_quarters": 4, "red_flags": {"dilution_max": 0.1}}}}
    )
    assert cfg.agents.fundamental.summary_quarters == 4
    assert cfg.agents.fundamental.red_flags.dilution_max == 0.1
    assert cfg.agents.fundamental.red_flags.accruals_max == 0.10


def _fact(tag: str, value: float, end: date) -> dict[str, object]:
    at = datetime(2024, 2, 1, 21, tzinfo=UTC)
    return {
        "symbol": "AAA", "cik": 1, "taxonomy": "us-gaap", "tag": tag, "unit": "USD",
        "value": value, "period_start": None, "period_end": end, "duration_days": 0,
        "fy": None, "fp": None, "form": "10-K", "filed": date(2024, 2, 1),
        "accn": f"0000000001-24-{abs(hash((tag, end))) % 10**6:06d}",
        "accepted_at": at, "available_at": at,
    }  # fmt: skip


def test_receivables_are_a_stock_with_a_fallback_tag() -> None:
    cmap = load_concept_map(REPO_ROOT / "config" / "xbrl_map.yaml")
    concept = cmap["accounts_receivable"]
    assert (concept.kind, concept.unit) == ("stock", "USD")
    assert "accounts_receivable" in metric_names(cmap)
    facts = pd.DataFrame(
        [
            _fact("AccountsReceivableNetCurrent", 29.5e9, date(2023, 9, 30)),
            _fact("ReceivablesNetCurrent", 60.9e9, date(2023, 9, 30)),  # lower priority
            _fact("ReceivablesNetCurrent", 28.2e9, date(2022, 9, 24)),  # the only tag that year
        ],
        columns=list(FACT_COLUMNS),
    )
    facts["fy"] = facts["fy"].astype("Int64")
    for col in ("accepted_at", "available_at"):
        facts[col] = pd.to_datetime(facts[col], utc=True).astype("datetime64[us, UTC]")
    out = resolve_concept(facts, concept).sort_values("period_end")
    assert list(out["value"]) == [28.2e9, 29.5e9]
    assert list(out["tag"]) == ["ReceivablesNetCurrent", "AccountsReceivableNetCurrent"]
