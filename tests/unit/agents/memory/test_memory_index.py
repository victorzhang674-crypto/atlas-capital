"""Stage 27 Task 4: the ChromaDB vector index (always filtered by creation time)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from atlas.agents.memory.index import Hit, IndexEntry, VectorIndex
from atlas.core.errors import ConfigError

T0 = datetime(2024, 3, 1, 13, tzinfo=UTC)


def unit(*values: float) -> np.ndarray:
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def entry(
    id_: str, vector: np.ndarray, *, agent: str = "a", symbol: str | None = None, days: int = 0
) -> IndexEntry:
    return IndexEntry(
        id=id_, vector=vector, agent=agent, symbol=symbol, created_at=T0 + timedelta(days=days)
    )


@pytest.fixture
def index() -> Iterator[VectorIndex]:
    idx = VectorIndex("test-embedder", dim=3)
    yield idx
    idx.close()


LATE = T0 + timedelta(days=100)


def test_add_and_query_returns_ids_and_cosine_similarity(index: VectorIndex) -> None:
    index.add("episodes", [entry("e1", unit(1, 0, 0)), entry("e2", unit(1, 1, 0))])
    hits = index.query("episodes", unit(1, 0, 0), n=5, agents=["a"], created_before=LATE)
    assert [h.id for h in hits] == ["e1", "e2"]
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-5)
    assert hits[1].similarity == pytest.approx(float(unit(1, 1, 0)[0]), abs=1e-5)
    assert isinstance(hits[0], Hit) and hits[0].vector is None
    assert index.count("episodes") == 2 and index.count("lessons") == 0


def test_query_filters_on_creation_time_strictly(index: VectorIndex) -> None:
    index.add(
        "episodes", [entry("old", unit(1, 0, 0), days=0), entry("new", unit(1, 0, 0), days=5)]
    )
    cutoff = T0 + timedelta(days=5)
    got = index.query("episodes", unit(1, 0, 0), n=5, agents=["a"], created_before=cutoff)
    assert [h.id for h in got] == ["old"]  # created exactly at the cutoff is not before it
    later = cutoff + timedelta(microseconds=1)
    got = index.query("episodes", unit(1, 0, 0), n=5, agents=["a"], created_before=later)
    assert {h.id for h in got} == {"old", "new"}


def test_query_filters_on_agent_and_symbol(index: VectorIndex) -> None:
    index.add(
        "episodes",
        [
            entry("a-x", unit(1, 0, 0), agent="a", symbol="XXX"),
            entry("a-y", unit(1, 0, 0), agent="a", symbol="YYY"),
            entry("a-none", unit(1, 0, 0), agent="a"),
            entry("b-x", unit(1, 0, 0), agent="b", symbol="XXX"),
        ],
    )
    q = unit(1, 0, 0)
    assert {h.id for h in index.query("episodes", q, n=9, agents=["a"], created_before=LATE)} == {
        "a-x",
        "a-y",
        "a-none",
    }
    by_symbol = index.query("episodes", q, n=9, agents=["a"], symbol="XXX", created_before=LATE)
    assert [h.id for h in by_symbol] == ["a-x"]
    both = index.query("episodes", q, n=9, agents=["a", "b"], symbol="XXX", created_before=LATE)
    assert {h.id for h in both} == {"a-x", "b-x"}
    assert index.query("episodes", q, n=9, agents=[], created_before=LATE) == []


def test_query_can_be_restricted_to_ids(index: VectorIndex) -> None:
    index.add("lessons", [entry(f"l{i}", unit(1, i, 0)) for i in range(4)])
    got = index.query(
        "lessons", unit(1, 0, 0), n=9, agents=["a"], ids=["l3", "l1"], created_before=LATE
    )
    assert [h.id for h in got] == ["l1", "l3"]
    assert (
        index.query("lessons", unit(1, 0, 0), n=9, agents=["a"], ids=[], created_before=LATE) == []
    )


def test_query_on_an_empty_collection_or_with_large_n(index: VectorIndex) -> None:
    assert index.query("episodes", unit(1, 0, 0), n=5, agents=["a"], created_before=LATE) == []
    index.add("episodes", [entry("e1", unit(1, 0, 0))])
    assert len(index.query("episodes", unit(0, 1, 0), n=50, agents=["a"], created_before=LATE)) == 1
    with pytest.raises(ValueError, match="n"):
        index.query("episodes", unit(1, 0, 0), n=0, agents=["a"], created_before=LATE)


def test_query_can_return_the_stored_vectors(index: VectorIndex) -> None:
    index.add("lessons", [entry("l1", unit(1, 2, 0))])
    (hit,) = index.query(
        "lessons", unit(1, 0, 0), n=1, agents=["a"], created_before=LATE, with_vectors=True
    )
    assert hit.vector is not None and np.allclose(hit.vector, unit(1, 2, 0), atol=1e-6)


def test_zero_query_finds_nothing_and_zero_vectors_are_refused(index: VectorIndex) -> None:
    index.add("episodes", [entry("e1", unit(1, 0, 0))])
    zero = np.zeros(3, dtype=np.float32)
    assert index.query("episodes", zero, n=5, agents=["a"], created_before=LATE) == []
    with pytest.raises(ValueError, match="zero"):
        index.add("episodes", [entry("e2", zero)])


def test_bad_kind_dimension_and_naive_times_are_refused(index: VectorIndex) -> None:
    with pytest.raises(ValueError, match="kind"):
        index.add("memories", [entry("e1", unit(1, 0, 0))])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="width"):
        index.add("episodes", [entry("e1", unit(1, 0, 0, 0))])
    with pytest.raises(ValueError, match="width"):
        index.query("episodes", unit(1, 0), n=1, agents=["a"], created_before=LATE)
    with pytest.raises(ValueError, match="naive"):
        index.query(
            "episodes", unit(1, 0, 0), n=1, agents=["a"], created_before=datetime(2024, 3, 1)
        )
    with pytest.raises(ValueError, match="dim"):
        VectorIndex("x", dim=0)


def test_ids_delete_and_add_nothing(index: VectorIndex) -> None:
    index.add("episodes", [])
    index.add("episodes", [entry("e1", unit(1, 0, 0)), entry("e2", unit(0, 1, 0))])
    assert index.ids("episodes") == {"e1", "e2"}
    index.delete("episodes", ["e1", "missing"])
    index.delete("episodes", [])
    assert index.ids("episodes") == {"e2"}


def test_ephemeral_indexes_do_not_share_data() -> None:
    first, second = VectorIndex("emb", dim=3), VectorIndex("emb", dim=3)
    try:
        first.add("episodes", [entry("e1", unit(1, 0, 0))])
        assert second.count("episodes") == 0
    finally:
        first.close()
        second.close()


def test_persistent_index_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "chroma"
    first = VectorIndex("emb", dim=3, path=path)
    first.add("episodes", [entry("e1", unit(1, 0, 0), symbol="XXX")])
    first.close()
    again = VectorIndex("emb", dim=3, path=path)
    assert again.ids("episodes") == {"e1"} and not again.rebuilt
    (hit,) = again.query(
        "episodes", unit(1, 0, 0), n=1, agents=["a"], symbol="XXX", created_before=LATE
    )
    assert hit.id == "e1"
    again.close()


def test_another_embedder_is_refused_unless_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "chroma"
    VectorIndex("emb-a", dim=3, path=path).add("episodes", [entry("e1", unit(1, 0, 0))])
    with pytest.raises(ConfigError, match="emb-a"):
        VectorIndex("emb-b", dim=3, path=path)
    with pytest.raises(ConfigError, match="dim"):
        VectorIndex("emb-a", dim=4, path=path)
    rebuilt = VectorIndex("emb-b", dim=4, path=path, rebuild=True)
    assert rebuilt.rebuilt and rebuilt.count("episodes") == 0
    assert rebuilt.embedder_name == "emb-b" and rebuilt.dim == 4
    assert not VectorIndex("emb-b", dim=4, path=path).rebuilt


def test_telemetry_is_off(index: VectorIndex) -> None:
    assert index.settings.anonymized_telemetry is False


def test_closed_index_refuses_work() -> None:
    idx = VectorIndex("emb", dim=3)
    idx.close()
    idx.close()  # idempotent
    with pytest.raises(ValueError, match="closed"):
        idx.count("episodes")
