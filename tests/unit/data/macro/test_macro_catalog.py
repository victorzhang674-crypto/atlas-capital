"""Stage 10 Task 1: the macro series catalog (`config/macro.yaml`)."""

from datetime import time
from pathlib import Path

import pytest

from atlas.core.errors import ConfigError
from atlas.data.macro import MacroCatalog, MacroSeries, load_macro_series

BRIEF_SERIES = {
    "DGS3MO", "DGS2", "DGS10", "T10Y2Y", "T10Y3M", "FEDFUNDS", "CPIAUCSL", "CPILFESL",
    "PCEPILFE", "UNRATE", "PAYEMS", "ICSA", "INDPRO", "RSAFS", "UMCSENT", "BAMLH0A0HYM2",
    "BAMLC0A0CM", "VIXCLS", "DTWEXBGS", "NFCI", "WALCL", "M2SL",
}  # fmt: skip
MARKET = {"DGS3MO", "DGS2", "DGS10", "T10Y2Y", "T10Y3M", "BAMLH0A0HYM2", "BAMLC0A0CM", "VIXCLS"}


def test_catalog_repo_file_has_the_brief_series(repo_root: Path) -> None:
    catalog = load_macro_series(repo_root / "config" / "macro.yaml")
    assert isinstance(catalog, MacroCatalog)
    assert set(catalog.ids) == BRIEF_SERIES
    assert len(catalog) == 22
    assert {i for i in catalog.ids if catalog.get(i).kind == "market"} == MARKET
    cpi = catalog.get("CPIAUCSL")
    assert cpi == MacroSeries(
        id="CPIAUCSL", kind="release", release_time=time(8, 30), description=cpi.description
    )
    assert catalog.get("DTWEXBGS").kind == "release"
    assert catalog.get("DGS10").release_time is None
    assert "DGS10" in catalog and "XYZ" not in catalog
    with pytest.raises(KeyError):
        catalog.get("XYZ")


@pytest.mark.parametrize(
    ("body", "pattern"),
    [
        ("series:\n  CPIAUCSL: {kind: release, description: CPI}\n", "release_time"),
        (
            'series:\n  DGS10: {kind: market, release_time: "08:30", description: x}\n',
            "release_time",
        ),
        ("series:\n  DGS10: {kind: daily, description: x}\n", "kind"),
        ("series:\n  dgs10: {kind: market, description: x}\n", "dgs10"),
        ("series: {}\n", "series"),
        ("series:\n  DGS10: {kind: market}\n", "description"),
    ],
)
def test_catalog_rejects_bad_files(tmp_path: Path, body: str, pattern: str) -> None:
    path = tmp_path / "macro.yaml"
    path.write_text(body)
    with pytest.raises(ConfigError, match=pattern) as info:
        load_macro_series(path)
    assert str(path) in str(info.value)


def test_catalog_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match=r"nope\.yaml"):
        load_macro_series(path)
