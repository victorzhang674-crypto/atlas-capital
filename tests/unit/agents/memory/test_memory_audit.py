"""Stage 27 audit: point-in-time holes found in the self-review, fixed test-first."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from atlas.agents.memory.embedder import HashEmbedder
from atlas.agents.memory.retrieval import AgentMemory, effective_quality
from atlas.agents.memory.store import GLOBAL
from atlas.core.calendar import MARKET_TZ
from atlas.core.config import AgentsConfig, AtlasConfig, LessonCurationConfig, MemoryConfig
from atlas.core.errors import LookAheadError
from atlas.core.pit import AsOf

AGENT = "fundamental_analyst"
T0 = datetime(2024, 3, 4, 17, tzinfo=UTC)


def test_a_datetime_as_of_is_refused() -> None:
    # A1: a datetime is a `date` subclass; midnight would pass as a day and silently move the
    # cutoff to that day's 08:00, later than the moment meant.
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=T0)
        midnight = datetime(2024, 3, 5, tzinfo=MARKET_TZ)
        with pytest.raises(TypeError, match="datetime"):
            memory.recall(AGENT, "earnings beat", midnight)  # type: ignore[arg-type]
    finally:
        memory.close()


def test_a_lesson_citing_an_outcome_settled_after_it_raises() -> None:
    # A2: in a re-run backtest the outcome may already be stored; a lesson dated before the
    # settlement could then carry the future outcome.
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ep = memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=T0)
        settled = T0 + timedelta(days=7)
        memory.settle_episode(ep.id, {"r": 0.1}, settled_at=settled)
        with pytest.raises(LookAheadError, match=ep.id):
            memory.add_lesson(
                AGENT,
                "lesson",
                created_at=settled - timedelta(seconds=1),
                source_episode_ids=[ep.id],
            )
        assert memory.store.lesson_ids() == set()
        assert memory.add_lesson(
            AGENT, "lesson", created_at=settled, source_episode_ids=[ep.id]
        ).stored
        unsettled = memory.record_episode(AGENT, "rates", {"a": 2}, created_at=T0)
        assert memory.add_lesson(
            AGENT,
            "rates lesson",
            created_at=T0 + timedelta(days=1),
            source_episode_ids=[unsettled.id],
        ).stored
    finally:
        memory.close()


WORDS = ["earnings", "beat", "guidance", "rates", "hike", "supply", "chips", "merger"]

_lesson = st.tuples(
    st.sampled_from([AGENT, GLOBAL, "news_analyst"]),
    st.lists(st.sampled_from(WORDS), min_size=1, max_size=4, unique=True),
    st.integers(min_value=0, max_value=400),  # created, days after T0
    st.none() | st.floats(min_value=0, max_value=1),
)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    lessons=st.lists(_lesson, min_size=1, max_size=15),
    cutoff_days=st.integers(min_value=0, max_value=450),
    cap=st.integers(min_value=1, max_value=4),
)
def test_lesson_visibility_matches_a_point_in_time_reference(
    lessons: list[tuple[str, list[str], int, float | None]], cutoff_days: int, cap: int
) -> None:
    # A3: supersession, decay, min quality and the cap, against a brute-force reference.
    curation = LessonCurationConfig(max_per_agent=cap, min_quality=0.05, half_life_days=90)
    cfg = AtlasConfig(agents=AgentsConfig(memory=MemoryConfig(embedder="hash", lessons=curation)))
    memory = AgentMemory(HashEmbedder(), config=cfg)
    try:
        for agent, words, days, quality in sorted(lessons, key=lambda x: x[2]):
            memory.add_lesson(
                agent, " ".join(words), created_at=T0 + timedelta(days=days), quality_score=quality
            )
        cutoff = T0 + timedelta(days=cutoff_days, hours=1)
        point = AsOf(day=cutoff.astimezone(MARKET_TZ).date(), cutoff=cutoff)
        reference: dict[str, list[tuple[float, datetime, str]]] = {}
        for lesson in memory.store.lessons([AGENT, GLOBAL]):
            if lesson.created_at >= cutoff:
                continue
            if lesson.superseded_at is not None and lesson.superseded_at < cutoff:
                continue
            q = effective_quality(lesson, cutoff, curation)
            if q >= curation.min_quality:
                reference.setdefault(lesson.agent, []).append((q, lesson.created_at, lesson.id))
        expected = set()
        for items in reference.values():
            items.sort(key=lambda x: (-x[0], -x[1].timestamp(), x[2]))
            expected |= {x[2] for x in items[:cap]}
        assert set(memory.visible_lessons(AGENT, point)) == expected
        for m in memory.recall(AGENT, " ".join(WORDS), point, k=50, kinds=["lesson"]):
            assert m.id in expected and m.agent in {AGENT, GLOBAL}
            assert m.created_at < cutoff
    finally:
        memory.close()


# --- A4: branches the first pass left untested ------------------------------------------


def test_an_index_entry_without_a_record_is_skipped_in_recall() -> None:
    from atlas.agents.memory.index import IndexEntry

    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        kept = memory.record_episode(AGENT, "earnings beat again", {"a": 1}, created_at=T0)
        vector = memory.embedder.embed(["earnings beat"])[0]  # the orphan ranks first
        memory.index.add(
            "episodes", [IndexEntry(id="epi_orphan", vector=vector, agent=AGENT, created_at=T0)]
        )
        later = AsOf(day=(T0 + timedelta(days=2)).date(), cutoff=T0 + timedelta(days=2))
        assert [m.id for m in memory.recall(AGENT, "earnings beat", later, k=5)] == [kept.id]
        assert memory.store.database is None
    finally:
        memory.close()


def test_lesson_recall_stops_at_k() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        for word in ("one", "two", "three", "four"):
            memory.add_lesson(AGENT, f"earnings beat drift {word}", created_at=T0)
        later = AsOf(day=(T0 + timedelta(days=2)).date(), cutoff=T0 + timedelta(days=2))
        got = memory.recall(AGENT, "earnings beat drift", later, k=2, kinds=["lesson"])
        assert len(got) == 2
    finally:
        memory.close()


def test_hits_without_vectors_are_never_duplicates() -> None:
    from atlas.agents.memory.index import Hit

    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        vec = memory.embedder.embed(["x"])[0]
        assert not memory._duplicates_a_kept_lesson(Hit("a", 0.9), [Hit("b", 0.9, vec)])
        assert not memory._duplicates_a_kept_lesson(Hit("a", 0.9, vec), [Hit("b", 0.9)])
        assert memory._duplicates_a_kept_lesson(Hit("a", 0.9, vec), [Hit("b", 0.9, vec)])
    finally:
        memory.close()


def test_json_object_needs_a_mapping() -> None:
    from atlas.agents.memory.store import json_object

    with pytest.raises(ValueError, match="mapping"):
        json_object(["a", 1])


def test_rebuilding_a_new_index_reports_it() -> None:
    from atlas.agents.memory.index import VectorIndex

    index = VectorIndex("emb", dim=3, rebuild=True)
    try:
        assert index.rebuilt and index.count("episodes") == 0
    finally:
        index.close()


# --- A5: gaps shown by the mutation run --------------------------------------------------


def test_a_settled_episode_row_round_trips() -> None:
    from atlas.agents.memory.store import Episode, episode_from_row, episode_row

    settled = Episode(
        id="epi_01HQ0000000000000000000000",
        agent=AGENT,
        created_at=T0,
        situation_text="s",
        decision={"a": 1},
        outcome={"r": 0.5},
        settled_at=T0 + timedelta(days=1),
    )
    row = episode_row(settled)
    assert row["outcome_json"] == '{"r":0.5}'
    assert episode_from_row(row) == settled


def test_clip_keeps_a_line_of_exactly_max_chars_and_cuts_to_max_chars() -> None:
    from atlas.agents.memory.render import _clip

    assert _clip("x" * 50, 50) == "x" * 50
    cut = _clip("x" * 51, 50)
    assert len(cut) == 50 and cut.endswith("…")


def test_an_unrated_lesson_renders_quality_na() -> None:
    from atlas.agents.memory.render import render_memory
    from atlas.agents.memory.retrieval import Memory

    lesson = Memory(
        kind="lesson",
        id="les_x",
        agent=GLOBAL,
        created_at=T0,
        text="t",
        similarity=0.5,
        quality=None,
    )
    assert "quality n/a" in render_memory(lesson).splitlines()[0]


def test_a_memory_outcome_comes_with_its_settled_at() -> None:
    from pydantic import ValidationError

    from atlas.agents.memory.retrieval import Memory

    base = {
        "kind": "episode",
        "id": "epi_x",
        "agent": AGENT,
        "created_at": T0,
        "text": "t",
        "similarity": 0.5,
    }
    with pytest.raises(ValidationError, match="both"):
        Memory.model_validate({**base, "outcome": {"r": 1}})
    with pytest.raises(ValidationError, match="both"):
        Memory.model_validate({**base, "settled_at": T0})


def test_sync_embeds_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.agents.memory import retrieval

    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ids = [
            memory.record_episode(AGENT, f"text {i}", {"i": i}, created_at=T0).id for i in range(5)
        ]
        memory.index.delete("episodes", ids)
        calls: list[int] = []
        real = memory.embedder.embed

        def counting(texts):  # type: ignore[no-untyped-def]
            calls.append(len(texts))
            return real(texts)

        monkeypatch.setattr(retrieval, "_SYNC_BATCH", 2)
        monkeypatch.setattr(memory.embedder, "embed", counting)
        assert memory.sync() == 5
        assert calls == [2, 2, 1] and memory.index.ids("episodes") == set(ids)
    finally:
        memory.close()


def test_minilm_empty_result_is_float32(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import numpy as np

    from atlas.agents.memory.embedder import MiniLMEmbedder

    assert MiniLMEmbedder(model_dir=tmp_path).embed([]).dtype == np.float32


# --- A6: more gaps shown by the mutation run (index, times) ------------------------------


class _NoOffset(__import__("datetime").tzinfo):
    """A tzinfo that knows no offset: Python treats such datetimes as naive."""

    def utcoffset(self, dt):  # type: ignore[no-untyped-def]
        return None


def test_a_tzinfo_without_an_offset_counts_as_naive() -> None:
    from atlas.agents.memory.index import micros

    odd = datetime(2024, 3, 4, tzinfo=_NoOffset())
    with pytest.raises(ValueError, match="naive"):
        micros(odd)
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ep = memory.record_episode(AGENT, "earnings", {"a": 1}, created_at=T0)
        with pytest.raises(ValueError, match="naive"):
            memory.settle_episode(ep.id, {"r": 1}, settled_at=odd)
    finally:
        memory.close()


def test_micros_counts_microseconds_since_the_epoch() -> None:
    from atlas.agents.memory.index import micros

    assert micros(datetime(1970, 1, 2, tzinfo=UTC)) == 86_400_000_000
    assert micros(datetime(1970, 1, 1, 0, 0, 1, 5, tzinfo=UTC)) == 1_000_005


def test_index_lifecycle_details(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import numpy as np

    from atlas.agents.memory.index import IndexEntry, VectorIndex

    one = VectorIndex("emb", dim=1)  # the smallest width works
    one.add("episodes", [IndexEntry("e", np.ones(1, dtype=np.float32), AGENT, T0)])
    names = {c.name for c in one._client.list_collections()}
    assert {f"{one._prefix}-episodes", f"{one._prefix}-lessons"} <= names
    one.close()  # an ephemeral index frees its collections
    assert not {f"{one._prefix}-episodes", f"{one._prefix}-lessons"} & {
        c.name for c in one._client.list_collections()
    }

    persistent = VectorIndex("emb", dim=3, path=tmp_path / "chroma")
    assert persistent.rebuilt is False
    # Stable names: an index written by an earlier run must be found again.
    assert {c.name for c in persistent._client.list_collections()} == {
        "atlas-episodes",
        "atlas-lessons",
    }
    persistent.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_telemetry_is_off_in_the_client(tmp_path, persistent: bool) -> None:  # type: ignore[no-untyped-def]
    from atlas.agents.memory.index import VectorIndex

    index = VectorIndex("emb", dim=3, path=tmp_path / "chroma" if persistent else None)
    try:
        assert index._client.get_settings().anonymized_telemetry is False
        assert index.settings.anonymized_telemetry is False
    finally:
        index.close()


# --- A7: retrieval gaps shown by the reclassified mutants --------------------------------


def _later(days: float = 30) -> AsOf:
    at = T0 + timedelta(days=days)
    return AsOf(day=at.astimezone(MARKET_TZ).date(), cutoff=at)


def test_equally_similar_episodes_rank_newer_first() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        old = memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=T0)
        new = memory.record_episode(
            AGENT, "earnings beat", {"a": 2}, created_at=T0 + timedelta(days=1)
        )
        got = memory.recall(AGENT, "earnings beat", _later(), k=2)
        assert [m.id for m in got] == [new.id, old.id]
    finally:
        memory.close()


def test_run_id_and_tags_are_stored() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ep = memory.record_episode(AGENT, "earnings", {"a": 1}, created_at=T0, run_id="01RUN")
        assert memory.store.episode(ep.id).run_id == "01RUN"  # type: ignore[union-attr]
        les = memory.add_lesson(AGENT, "lesson", created_at=T0, tags=["earnings", "drift"])
        stored = memory.store.lesson(les.lesson_id)
        assert stored is not None and stored.tags == ("earnings", "drift")
        (m,) = memory.recall(AGENT, "lesson", _later(), kinds=["lesson"])
        assert m.tags == ("earnings", "drift")
    finally:
        memory.close()


def test_an_explicit_quality_is_used_not_the_default() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())  # default_quality 0.5
    try:
        text = "earnings beats with raised guidance drift up"
        memory.add_lesson(AGENT, text, created_at=T0, quality_score=0.55)
        better = memory.add_lesson(AGENT, text, created_at=T0, quality_score=0.6)
        assert better.stored and better.supersedes is not None
        assert better.similarity is not None and better.similarity > 0.92
    finally:
        memory.close()


def test_a_superseded_lesson_is_not_the_near_duplicate_found() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        base = "earnings beats with raised guidance drift up for many weeks after"
        old = memory.add_lesson(AGENT, base, created_at=T0, quality_score=0.2)
        new = memory.add_lesson(
            AGENT, base + " report", created_at=T0 + timedelta(days=1), quality_score=0.9
        )
        assert new.supersedes == old.lesson_id
        # Identical to the superseded text: the closest active lesson is still `new`.
        third = memory.add_lesson(AGENT, base, created_at=T0 + timedelta(days=2), quality_score=0.5)
        assert not third.stored and third.duplicate_of == new.lesson_id
    finally:
        memory.close()


# --- A8: the last gaps from the reclassified survivors -----------------------------------


def test_hash_embedder_values_are_pinned() -> None:
    # An index stored as `hash-256-v1` must match the vectors of every later version: a change
    # to the hashing needs a new name (and a rebuild).
    import numpy as np

    vector = HashEmbedder().embed(["Earnings earnings, beat; LAWSUIT margin"])[0]
    nonzero = {int(i): round(float(vector[i]), 6) for i in np.nonzero(vector)[0]}
    assert nonzero == {64: -0.377964, 150: -0.755929, 224: -0.377964, 246: 0.377964}
    assert HashEmbedder().dim == 256 and HashEmbedder().name == "hash-256-v1"
    assert HashEmbedder(dim=8).embed(["x"]).shape == (1, 8)
    stop, plain = HashEmbedder().embed(["the earnings of a beat", "earnings beat"])
    assert np.array_equal(stop, plain)  # stop words are ignored


def test_lesson_recall_keeps_the_most_similar_when_it_stops_early() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ids = {}
        for text in ("earnings beat drift", "earnings beat", "earnings"):
            ids[text] = memory.add_lesson(
                AGENT, text, created_at=T0, source_episode_ids=[]
            ).lesson_id
        (top,) = memory.recall(AGENT, "earnings beat drift", _later(), k=1, kinds=["lesson"])
        assert top.id == ids["earnings beat drift"] and top.source_episode_ids == ()
    finally:
        memory.close()


def test_a_duplicate_does_not_stop_the_lesson_scan() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        text = "earnings beats with raised guidance drift up"
        memory.add_lesson(AGENT, text, created_at=T0, quality_score=0.8)
        memory.add_lesson(GLOBAL, text, created_at=T0, quality_score=0.8)
        other = memory.add_lesson(GLOBAL, "earnings guidance cut", created_at=T0).lesson_id
        got = memory.recall(AGENT, text, _later(), k=5, kinds=["lesson"])
        assert len(got) == 2 and other in {m.id for m in got}
    finally:
        memory.close()


def test_recalled_lessons_carry_their_sources() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ep = memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=T0)
        memory.add_lesson(AGENT, "earnings lesson", created_at=T0, source_episode_ids=[ep.id])
        (m,) = memory.recall(AGENT, "earnings lesson", _later(), kinds=["lesson"])
        assert m.source_episode_ids == (ep.id,)
    finally:
        memory.close()


def test_the_similarity_floor_is_inclusive() -> None:
    from atlas.agents.memory.index import Hit

    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())  # min_similarity 0.3
    try:
        ep = memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=T0)
        memory.index.query = lambda *a, **k: [Hit(id=ep.id, similarity=0.3)]  # type: ignore[method-assign]
        got = memory.recall(AGENT, "earnings beat", _later(), kinds=["episode"])
        assert [m.id for m in got] == [ep.id]
    finally:
        memory.close()


def test_hash_buckets_add_up_when_tokens_collide() -> None:
    import numpy as np

    # At dim 8, "beat" and "margin" share bucket 0 (both negative), "supply" is in bucket 1.
    vector = HashEmbedder(dim=8).embed(["beat margin supply"])[0]
    expected = np.array([-2, -1, 0, 0, 0, 0, 0, 0], dtype=np.float32) / np.sqrt(5)
    assert np.allclose(vector, expected, atol=1e-6)


def test_the_recall_time_dedupe_threshold_is_strict() -> None:
    import numpy as np

    from atlas.agents.memory.index import Hit

    curation = LessonCurationConfig(dedupe_cosine=0.5)
    cfg = AtlasConfig(agents=AgentsConfig(memory=MemoryConfig(embedder="hash", lessons=curation)))
    memory = AgentMemory(HashEmbedder(), config=cfg)
    try:
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.5, np.sqrt(0.75)], dtype=np.float32)  # a @ b == 0.5 exactly
        assert float(a @ b) == 0.5
        assert not memory._duplicates_a_kept_lesson(Hit("x", 0.9, b), [Hit("y", 0.9, a)])
    finally:
        memory.close()
