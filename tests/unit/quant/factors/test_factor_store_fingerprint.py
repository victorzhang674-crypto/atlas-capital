"""Phase 2 checkpoint (backlog, Stage 16): the factor cache knows when its data changed.

Each stored panel records `data_fingerprint`, a fingerprint of the data it was computed from
as known at its cutoff (`input_fingerprint`: how many facts, price bars and corporate actions
were known, the instruments' sectors and the index membership up to its day). A reader
recomputes it and treats a panel whose data changed as not stored (logged
`factors.cache_stale`); `build_factor_cache` recomputes such a date even without `refresh`.
A later filing or price bar (known after the panel's cutoff) changes nothing."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from factor_world import STOCKS, build_factor_world
from structlog.testing import capture_logs

from atlas.core.config import AtlasConfig
from atlas.core.pit import AsOf
from atlas.core.storage import (
    DEFAULT_REGISTRY,
    Lake,
    SchemaRegistry,
    TableSchema,
)
from atlas.data.view import DataView
from atlas.quant.factors import (
    FACTOR_EXPOSURES,
    FactorStore,
    build_factor_cache,
    factor_panel,
    input_fingerprint,
)

CFG = AtlasConfig.model_validate({"quant": {"factors": {"min_names": 5}}})
DAY = date(2024, 1, 31)


def _world(root: Path) -> Lake:
    lake = Lake(root / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    return lake


def _stored(lake: Lake) -> FactorStore:
    store = FactorStore(lake, CFG)
    store.write(factor_panel(DataView(lake, DAY, CFG), config=CFG.quant.factors))
    return store


def _late(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    """`frame` re-dated to be known only after `day`'s cutoff."""
    stamp = pd.Timestamp(datetime(day.year, day.month, day.day, 23, tzinfo=UTC))
    return frame.assign(available_at=stamp)


def test_schema_has_the_fingerprint() -> None:
    assert "data_fingerprint" in FACTOR_EXPOSURES.column_names
    assert FACTOR_EXPOSURES.version == 2


def test_fingerprint_is_stored_and_a_fresh_panel_is_read(tmp_path: Path) -> None:
    lake = _world(tmp_path)
    store = _stored(lake)
    rows = lake.read_table("factor_exposures")
    point = DataView(lake, DAY, CFG).as_of
    assert set(rows["data_fingerprint"]) == {input_fingerprint(lake, point)}
    assert store.read_on(DAY) is not None and store.read(DAY) is not None


def test_a_fact_known_before_the_cutoff_makes_it_stale(tmp_path: Path) -> None:
    lake = _world(tmp_path)
    store = _stored(lake)
    facts = lake.read_table("fundamentals_facts")
    extra = facts[facts["symbol"] == "AAA"].head(1).assign(tag="Depreciation", value=1.0)
    lake.write_table("fundamentals_facts", extra, mode="upsert")  # same filing: known before
    with capture_logs() as logs:
        assert store.read_on(DAY) is None
        assert store.read(DAY) is None
    stale = [e for e in logs if e["event"] == "factors.cache_stale"]
    assert stale and stale[0]["day"] == DAY.isoformat()


def test_data_known_after_the_cutoff_changes_nothing(tmp_path: Path) -> None:
    lake = _world(tmp_path)
    store = _stored(lake)
    facts = lake.read_table("fundamentals_facts")
    later = _late(facts[facts["symbol"] == "AAA"].head(3).assign(accn="0000000016-99-000001"), DAY)
    lake.write_table("fundamentals_facts", later, mode="upsert")
    bars = lake.read_table("prices_daily", filters=[("date", "==", date(2024, 2, 29))])
    lake.write_table("prices_daily", bars.assign(close=bars["close"] * 1.5), mode="upsert")
    membership = pd.DataFrame(
        {"index_id": "SP500", "symbol": "ZZZ", "start_date": date(2024, 2, 15), "end_date": None},
        index=[0],
    )
    lake.write_table("index_membership", membership, mode="upsert")
    assert store.read_on(DAY) is not None


def test_a_sector_or_membership_change_up_to_the_day_makes_it_stale(tmp_path: Path) -> None:
    lake = _world(tmp_path)
    store = _stored(lake)
    instruments = lake.read_table("instruments")
    moved = instruments[instruments["symbol"] == "LLL"].assign(sector="Energy")
    lake.write_table("instruments", moved, mode="upsert")
    assert store.read_on(DAY) is None
    lake2 = _world(tmp_path / "second")
    store2 = _stored(lake2)
    membership = pd.DataFrame(
        {"index_id": "SP500", "symbol": "ZZZ", "start_date": date(2023, 6, 1), "end_date": None},
        index=[0],
    )
    lake2.write_table("index_membership", membership, mode="upsert")
    assert store2.read_on(DAY) is None


def test_build_recomputes_only_the_stale_dates(tmp_path: Path) -> None:
    lake = _world(tmp_path)
    days = [date(2023, 12, 29), DAY]
    assert build_factor_cache(lake, days, config=CFG) == tuple(days)
    assert build_factor_cache(lake, days, config=CFG) == ()  # all fresh: nothing to do
    facts = lake.read_table("fundamentals_facts")
    jan = facts[
        (facts["symbol"] == "BBB") & (facts["available_at"] > pd.Timestamp("2024-01-02", tz="UTC"))
    ]
    assert not jan.empty
    early = jan.head(1).assign(
        tag="Depreciation",
        value=2.0,
        available_at=pd.Timestamp(datetime(2024, 1, 3, 21, tzinfo=UTC)),
        accepted_at=pd.Timestamp(datetime(2024, 1, 3, 21, tzinfo=UTC)),
        filed=date(2024, 1, 3),
        accn="0000000016-24-777777",
    )
    lake.write_table("fundamentals_facts", early, mode="upsert")  # known before DAY only
    assert build_factor_cache(lake, days, config=CFG) == (DAY,)


def test_rows_without_a_fingerprint_are_stale(tmp_path: Path) -> None:
    """A table stored before the fingerprint existed (schema version 1) migrates with empty
    fingerprints, which are never read as current."""
    lake = _world(tmp_path)
    old_schema = TableSchema(
        name=FACTOR_EXPOSURES.name,
        columns=tuple(c for c in FACTOR_EXPOSURES.columns if c.name != "data_fingerprint"),
        primary_key=FACTOR_EXPOSURES.primary_key,
        partition_by=FACTOR_EXPOSURES.partition_by,
        year_column=FACTOR_EXPOSURES.year_column,
        description=FACTOR_EXPOSURES.description,
        version=1,
    )
    old = Lake(lake.root, registry=SchemaRegistry([old_schema]))
    panel = factor_panel(DataView(lake, DAY, CFG), config=CFG.quant.factors)
    fresh = FactorStore(Lake(tmp_path / "tmp", registry=DEFAULT_REGISTRY.copy()), CFG)
    fresh.write(panel)
    rows = fresh.lake.read_table("factor_exposures").drop(columns="data_fingerprint")
    old.write_table("factor_exposures", rows, mode="overwrite")
    current = Lake(lake.root, registry=DEFAULT_REGISTRY.copy())
    assert current.migrate("factor_exposures") == 2
    assert current.read_table("factor_exposures")["data_fingerprint"].isna().all()
    assert FactorStore(current, CFG).read_on(DAY) is None


def test_a_separate_store_fingerprints_the_data_lake(tmp_path: Path) -> None:
    lake = _world(tmp_path)
    other = Lake(tmp_path / "store", registry=DEFAULT_REGISTRY.copy())
    store = FactorStore(other, CFG, data_lake=lake)
    store.write(factor_panel(DataView(lake, DAY, CFG), config=CFG.quant.factors))
    assert store.read_on(DAY) is not None
    point = DataView(lake, DAY, CFG).as_of
    assert set(other.read_table("factor_exposures")["data_fingerprint"]) == {
        input_fingerprint(lake, point)
    }
    assert input_fingerprint(lake, point) != input_fingerprint(other, point)
    assert len(STOCKS) == 12  # the world the module relies on
    assert isinstance(point, AsOf)
