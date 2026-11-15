"""`AgentMemory`: write episodes and lessons, recall the most similar ones, point-in-time.

Visibility follows `AsOf.knows`, the rule for every dataset: a memory is visible at a
decision moment iff it was created strictly before the cutoff; an episode's outcome (and
`settled_at`) iff it settled strictly before the cutoff. An agent sees its own episodes, its
own lessons and the `global` lessons, never another agent's.

Lesson curation (all point-in-time; nothing is rewritten in the past):
- effective quality at a cutoff = `quality_score * 0.5^(age_days / half_life_days)`, the age
  measured from `created_at` to the cutoff (`default_quality` for an unrated lesson);
- a new lesson closer than `dedupe_cosine` to an active lesson of its scope is stored only if
  its quality beats the old one's effective quality, and then supersedes it from its own
  `created_at` on;
- at recall each scope keeps its `max_per_agent` best lessons by effective quality, drops
  those under `min_quality`, and shows an agent lesson and a global one that are
  near-duplicates once.

Times are the caller's (the simulated decision time in a backtest), never the wall clock.
"""

import threading
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal, Self

from pydantic import Field, model_validator
from ulid import ULID

from atlas.agents.memory.embedder import Embedder
from atlas.agents.memory.index import KINDS, Hit, IndexEntry, Kind, VectorIndex
from atlas.agents.memory.store import GLOBAL, Episode, MemoryStore, StoredLesson
from atlas.core.config import AtlasConfig, LessonCurationConfig, MemoryConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf
from atlas.core.storage import Database
from atlas.core.types.base import AtlasModel, FrozenMap, UtcDatetime
from atlas.core.types.ids import IdPrefix

MemoryKind = Literal["episode", "lesson"]
MEMORY_KINDS: tuple[MemoryKind, ...] = ("episode", "lesson")

_SYNC_BATCH = 256
_ONE_MICROSECOND = timedelta(microseconds=1)

_log = get_logger(__name__)


class Memory(AtlasModel):
    """One recalled memory, as it was known at the recall's cutoff.

    `kind` is `episode` or `lesson`; `agent` the agent it belongs to (`global` for shared
    lessons); `text` the situation (episode) or the lesson; `similarity` its cosine
    similarity to the query. Episodes carry `symbol` and `decision`, and `outcome` /
    `settled_at` only if it settled before the cutoff. Lessons carry `tags`,
    `source_episode_ids` and `quality`, their effective quality at the cutoff.
    """

    kind: MemoryKind
    id: str
    agent: str
    created_at: UtcDatetime
    text: str
    similarity: float
    symbol: str | None = None
    decision: FrozenMap[str, Any] | None = None
    outcome: FrozenMap[str, Any] | None = None
    settled_at: UtcDatetime | None = None
    tags: tuple[str, ...] = ()
    source_episode_ids: tuple[str, ...] = ()
    quality: float | None = None

    @model_validator(mode="after")
    def _outcome_with_settled_at(self) -> Self:
        if (self.outcome is None) != (self.settled_at is None):
            raise ValueError("outcome and settled_at must be given both or neither")
        return self


class LessonWrite(AtlasModel):
    """What `add_lesson` did: `stored` the new lesson (`lesson_id` is its id), possibly
    superseding the near-duplicate `supersedes`; or did not store it because it duplicates
    `duplicate_of` without beating it (`lesson_id` is then that lesson's id). `similarity`
    is the cosine to the near-duplicate, if there was one."""

    stored: bool
    lesson_id: str
    supersedes: str | None = None
    duplicate_of: str | None = None
    similarity: float | None = Field(default=None)


def effective_quality(
    lesson: StoredLesson, cutoff: datetime, config: LessonCurationConfig
) -> float:
    """`lesson`'s quality decayed to `cutoff`: `quality * 0.5^(age_days / half_life_days)`
    with the age from `created_at` to `cutoff` (never negative); `config.default_quality`
    for an unrated lesson."""
    quality = lesson.quality_score if lesson.quality_score is not None else config.default_quality
    age_days = max((cutoff - lesson.created_at).total_seconds(), 0.0) / 86_400
    return float(quality * 0.5 ** (age_days / config.half_life_days))


def _rank_key(memory: Memory) -> tuple[float, float, str]:
    return (-memory.similarity, -memory.created_at.timestamp(), memory.id)


class AgentMemory:
    """Agent memory over a `MemoryStore` (the records: the `database`'s native tables, or
    memory) and a `VectorIndex` (ChromaDB under `<lake>/<agents.memory.index_dir>`, or
    ephemeral without a database), embedding text with `embedder`.

    Opening it adds records missing from the index (`sync`). An index built with another
    embedder raises `ConfigError` unless `rebuild=True`, which re-embeds every record.
    `config` defaults to `AtlasConfig()` (its `calendar` turns a `date` into its decision
    moment; `agents.memory` holds the thresholds). Thread-safe.
    """

    def __init__(
        self,
        embedder: Embedder,
        *,
        database: Database | None = None,
        config: AtlasConfig | None = None,
        rebuild: bool = False,
    ) -> None:
        self._config = config or AtlasConfig()
        self._settings: MemoryConfig = self._config.agents.memory
        self._embedder = embedder
        self._lock = threading.RLock()
        self._store = MemoryStore(database)
        path = None if database is None else database.lake.root / self._settings.index_dir
        self._index = VectorIndex(embedder.name, embedder.dim, path, rebuild=rebuild)
        self.sync()

    @property
    def store(self) -> MemoryStore:
        """The records (source of truth)."""
        return self._store

    @property
    def index(self) -> VectorIndex:
        """The vector index."""
        return self._index

    @property
    def embedder(self) -> Embedder:
        """The embedder of every vector in the index."""
        return self._embedder

    @property
    def settings(self) -> MemoryConfig:
        """`agents.memory` of the config."""
        return self._settings

    def close(self) -> None:
        """Release the index (an ephemeral one is deleted); the records stay."""
        with self._lock:
            self._index.close()

    # --- writes -------------------------------------------------------------------------

    def _vector(self, text: str) -> Any:
        return self._embedder.embed([text])[0]

    def record_episode(
        self,
        agent: str,
        situation_text: str,
        decision: Mapping[str, Any],
        *,
        created_at: datetime,
        symbol: str | None = None,
        run_id: str | None = None,
    ) -> Episode:
        """Remember that `agent` faced `situation_text` and decided `decision` (a JSON
        object) at `created_at` (aware; the decision moment); return the stored episode."""
        episode = Episode(
            id=f"{IdPrefix.EPISODE}_{ULID()}",
            agent=agent,
            symbol=symbol,
            created_at=created_at,
            situation_text=situation_text,
            decision=decision,
            run_id=run_id,
        )
        vector = self._vector(episode.situation_text)
        with self._lock:
            self._store.add_episode(episode)
            self._index.add("episodes", [self._episode_entry(episode, vector)])
        return episode

    def settle_episode(
        self, episode_id: str, outcome: Mapping[str, Any], *, settled_at: datetime
    ) -> Episode:
        """Record the outcome of an episode, known from `settled_at` (see
        `MemoryStore.settle_episode`); return the settled episode."""
        with self._lock:
            return self._store.settle_episode(episode_id, outcome, settled_at=settled_at)

    def add_lesson(
        self,
        agent: str,
        text: str,
        *,
        created_at: datetime,
        tags: Sequence[str] = (),
        source_episode_ids: Sequence[str] = (),
        quality_score: float | None = None,
    ) -> LessonWrite:
        """Store a lesson for `agent` (or `global`), known from `created_at`, unless it is a
        near-duplicate of an active lesson of the same scope that it does not beat (see the
        module docstring).

        Its source episodes must be stored (`DataError`) and created, and settled if they
        are, at or before it (`LookAheadError`): a lesson cannot draw on a later outcome.
        """
        lesson = StoredLesson(
            id=f"{IdPrefix.LESSON}_{ULID()}",
            agent=agent,
            created_at=created_at,
            text=text,
            tags=tuple(tags),
            source_episode_ids=tuple(source_episode_ids),
            quality_score=quality_score,
        )
        vector = self._vector(lesson.text)
        with self._lock:
            self._check_sources(lesson)
            duplicate = self._near_duplicate(lesson, vector)
            supersedes = None
            similarity = None
            if duplicate is not None:
                old, similarity = duplicate
                curation = self._settings.lessons
                fresh = lesson.quality_score
                fresh = curation.default_quality if fresh is None else fresh
                if fresh <= effective_quality(old, lesson.created_at, curation):
                    _log.info("memory.lesson.duplicate", lesson=old.id, similarity=similarity)
                    return LessonWrite(
                        stored=False,
                        lesson_id=old.id,
                        duplicate_of=old.id,
                        similarity=similarity,
                    )
                supersedes = old.id
            self._store.add_lesson(lesson, supersedes=supersedes)
            self._index.add("lessons", [self._lesson_entry(lesson, vector)])
        return LessonWrite(
            stored=True, lesson_id=lesson.id, supersedes=supersedes, similarity=similarity
        )

    def _check_sources(self, lesson: StoredLesson) -> None:
        found = self._store.episodes(lesson.source_episode_ids)
        missing = [i for i in lesson.source_episode_ids if i not in found]
        if missing:
            raise DataError(f"lesson {lesson.id} cites unknown episodes {missing}")
        late = [
            e.id
            for e in found.values()
            if e.created_at > lesson.created_at
            or (e.settled_at is not None and e.settled_at > lesson.created_at)
        ]
        if late:
            raise LookAheadError(
                f"lesson {lesson.id} (created {lesson.created_at.isoformat()}) cites episodes "
                f"created or settled after it: {late}"
            )

    def _near_duplicate(
        self, lesson: StoredLesson, vector: Any
    ) -> tuple[StoredLesson, float] | None:
        """The active same-scope lesson created at or before `lesson` that is closest to it,
        if closer than `dedupe_cosine`."""
        active = {
            x.id: x
            for x in self._store.lessons([lesson.agent])
            if x.superseded_by is None and x.created_at <= lesson.created_at
        }
        if not active:
            return None
        hits = self._index.query(
            "lessons",
            vector,
            n=1,
            agents=[lesson.agent],
            ids=sorted(active),
            created_before=lesson.created_at + _ONE_MICROSECOND,
        )
        if not hits or hits[0].similarity <= self._settings.lessons.dedupe_cosine:
            return None
        return active[hits[0].id], hits[0].similarity

    # --- recall -------------------------------------------------------------------------

    def _point(self, as_of: AsOf | date) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):  # a date subclass: midnight would pass as a day
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._config.calendar)

    def recall(
        self,
        agent: str,
        query_text: str,
        as_of: AsOf | date,
        k: int | None = None,
        symbol: str | None = None,
        *,
        kinds: Collection[MemoryKind] = MEMORY_KINDS,
    ) -> list[Memory]:
        """The `k` (default `agents.memory.default_k`) memories of `agent` most similar to
        `query_text`, as known at `as_of` (an `AsOf`, or a trading day at the configured
        decision time): its episodes (of `symbol` only, if given), its lessons and the global
        ones; `kinds` limits them. Most similar first (then newer, then id); memories under
        `min_similarity` are left out.

        `LookAheadError` if the index returns a memory not visible at `as_of`.
        """
        if agent == GLOBAL:
            raise ValueError(f"{GLOBAL!r} is a lesson scope; recall for an agent")
        if not query_text.strip():
            raise ValueError("query_text is blank")
        size = self._settings.default_k if k is None else k
        if size < 1:
            raise ValueError(f"k must be positive, got {size}")
        unknown = set(kinds) - set(MEMORY_KINDS)
        if unknown:
            raise ValueError(f"unknown kinds {sorted(unknown)}; use {MEMORY_KINDS}")
        if not kinds:
            return []
        point = self._point(as_of)
        vector = self._vector(query_text)
        found: list[Memory] = []
        with self._lock:
            if "episode" in kinds:
                found += self._recall_episodes(agent, vector, point, size, symbol)
            if "lesson" in kinds:
                found += self._recall_lessons(agent, vector, point, size)
        floor = self._settings.min_similarity
        return sorted((m for m in found if m.similarity >= floor), key=_rank_key)[:size]

    def _recall_episodes(
        self, agent: str, vector: Any, point: AsOf, k: int, symbol: str | None
    ) -> list[Memory]:
        hits = self._index.query(
            "episodes", vector, n=k, agents=[agent], symbol=symbol, created_before=point.cutoff
        )
        records = self._store.episodes(h.id for h in hits)
        memories = []
        for hit in hits:
            episode = records.get(hit.id)
            if episode is None:
                _log.warning("memory.index.orphan", kind="episodes", id=hit.id)
                continue
            if not point.knows(episode.created_at):
                raise LookAheadError(
                    f"memory index returned episode {episode.id} created "
                    f"{episode.created_at.isoformat()}, at or after the cutoff "
                    f"{point.cutoff.isoformat()}"
                )
            if episode.agent != agent or (symbol is not None and episode.symbol != symbol):
                raise DataError(f"memory index returned episode {episode.id} of another filter")
            settled = episode.settled_at is not None and point.knows(episode.settled_at)
            memories.append(
                Memory(
                    kind="episode",
                    id=episode.id,
                    agent=episode.agent,
                    created_at=episode.created_at,
                    text=episode.situation_text,
                    similarity=hit.similarity,
                    symbol=episode.symbol,
                    decision=episode.decision,
                    outcome=episode.outcome if settled else None,
                    settled_at=episode.settled_at if settled else None,
                )
            )
        return memories

    def visible_lessons(self, agent: str, point: AsOf) -> dict[str, tuple[StoredLesson, float]]:
        """The lessons `agent` may recall at `point`, by id, with their effective quality:
        created before the cutoff, not superseded before it, quality at least `min_quality`,
        and among the `max_per_agent` best of their scope (ties: newer, then id)."""
        curation = self._settings.lessons
        by_scope: dict[str, list[tuple[StoredLesson, float]]] = {}
        for lesson in self._store.lessons([agent, GLOBAL]):
            if not point.knows(lesson.created_at):
                continue
            if lesson.superseded_at is not None and point.knows(lesson.superseded_at):
                continue
            quality = effective_quality(lesson, point.cutoff, curation)
            if quality >= curation.min_quality:
                by_scope.setdefault(lesson.agent, []).append((lesson, quality))
        kept: dict[str, tuple[StoredLesson, float]] = {}
        for items in by_scope.values():
            items.sort(key=lambda x: (-x[1], -x[0].created_at.timestamp(), x[0].id))
            kept.update({lesson.id: (lesson, q) for lesson, q in items[: curation.max_per_agent]})
        return kept

    def _recall_lessons(self, agent: str, vector: Any, point: AsOf, k: int) -> list[Memory]:
        allowed = self.visible_lessons(agent, point)
        if not allowed:
            return []
        hits = self._index.query(
            "lessons",
            vector,
            n=len(allowed),
            agents=[agent, GLOBAL],
            ids=sorted(allowed),
            created_before=point.cutoff,
            with_vectors=True,
        )
        for hit in hits:
            if hit.id not in allowed:
                raise LookAheadError(
                    f"memory index returned lesson {hit.id}, not visible at the cutoff "
                    f"{point.cutoff.isoformat()}"
                )
        # Rank before deduping, so a tie keeps the documented winner (newer, then id), not
        # whichever the index happened to list first.
        hits.sort(key=lambda h: (-h.similarity, -allowed[h.id][0].created_at.timestamp(), h.id))
        kept: list[Hit] = []
        for hit in hits:
            if self._duplicates_a_kept_lesson(hit, kept):
                continue
            kept.append(hit)
            if len(kept) == k:
                break
        memories = []
        for hit in kept:
            lesson, quality = allowed[hit.id]
            memories.append(
                Memory(
                    kind="lesson",
                    id=lesson.id,
                    agent=lesson.agent,
                    created_at=lesson.created_at,
                    text=lesson.text,
                    similarity=hit.similarity,
                    tags=lesson.tags,
                    source_episode_ids=lesson.source_episode_ids,
                    quality=quality,
                )
            )
        return memories

    def _duplicates_a_kept_lesson(self, hit: Hit, kept: Sequence[Hit]) -> bool:
        if hit.vector is None:
            return False
        threshold = self._settings.lessons.dedupe_cosine
        return any(
            other.vector is not None and float(hit.vector @ other.vector) > threshold
            for other in kept
        )

    # --- index maintenance --------------------------------------------------------------

    @staticmethod
    def _episode_entry(episode: Episode, vector: Any) -> IndexEntry:
        return IndexEntry(
            id=episode.id,
            vector=vector,
            agent=episode.agent,
            created_at=episode.created_at,
            symbol=episode.symbol,
        )

    @staticmethod
    def _lesson_entry(lesson: StoredLesson, vector: Any) -> IndexEntry:
        return IndexEntry(
            id=lesson.id, vector=vector, agent=lesson.agent, created_at=lesson.created_at
        )

    def sync(self) -> int:
        """Make the index match the records: embed and add records it lacks, drop entries
        with no record. Returns how many records were added."""
        added = 0
        with self._lock:
            for kind in KINDS:
                stored = (
                    self._store.episode_ids() if kind == "episodes" else self._store.lesson_ids()
                )
                indexed = self._index.ids(kind)
                orphans = sorted(indexed - stored)
                if orphans:
                    _log.warning("memory.index.orphans", kind=kind, count=len(orphans))
                    self._index.delete(kind, orphans)
                missing = stored - indexed
                if missing:
                    added += self._reindex(kind, missing)
        if added:
            _log.info("memory.index.sync", added=added)
        return added

    def _reindex(self, kind: Kind, ids: set[str]) -> int:
        if kind == "episodes":
            episodes = [e for e in self._store.all_episodes() if e.id in ids]
            texts = [e.situation_text for e in episodes]
            make = self._episode_entry
            records: list[Any] = episodes
        else:
            lessons = [x for x in self._store.all_lessons() if x.id in ids]
            texts = [x.text for x in lessons]
            make = self._lesson_entry  # type: ignore[assignment]
            records = lessons
        for start in range(0, len(records), _SYNC_BATCH):
            chunk = records[start : start + _SYNC_BATCH]
            vectors = self._embedder.embed(texts[start : start + _SYNC_BATCH])
            self._index.add(kind, [make(r, v) for r, v in zip(chunk, vectors, strict=True)])
        return len(records)


__all__ = [
    "MEMORY_KINDS",
    "AgentMemory",
    "LessonWrite",
    "Memory",
    "MemoryKind",
    "effective_quality",
]
