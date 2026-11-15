"""Memory records: episodes and lessons, kept in the lake's native `episodes` and `lessons`
tables, or in memory without a lake.

The records are the source of truth; the vector index (`atlas.agents.memory.index`) can be
rebuilt from them. Times are what the caller says (the simulated decision time in a
backtest), never the wall clock. Nothing stored is rewritten except two one-way steps, each
known from its own timestamp: an episode settles once (`outcome`, `settled_at`) and a lesson
is superseded once (`superseded_by`, `superseded_at`).
"""

import json
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Self

from pydantic import field_validator, model_validator

from atlas.core.audit import canonical_json
from atlas.core.errors import DataError
from atlas.core.storage import Database
from atlas.core.types.base import AtlasModel, FrozenMap, NonEmptyStr, UtcDatetime
from atlas.core.types.ids import EpisodeId, LessonId
from atlas.core.types.performance import Lesson

GLOBAL = "global"
"""The lesson scope every agent sees; not an agent name."""


def json_object(value: object) -> dict[str, Any]:
    """`value` (a mapping) as its canonical JSON round trip: `Decimal` → string, aware
    datetimes → UTC ISO text. `ValueError` if it is not a mapping or not JSON-encodable
    (NaN, sets, naive datetimes, ...)."""
    if not isinstance(value, Mapping):
        raise ValueError(f"expected a JSON object (a mapping), got {type(value).__name__}")
    try:
        decoded: dict[str, Any] = json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not encodable as JSON: {exc}") from exc
    return decoded


def _require_aware(name: str, ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{name} is a naive datetime {ts!r}; pass a timezone-aware one")


class Episode(AtlasModel):
    """One remembered decision of an agent.

    `situation_text` is what the agent saw (it is embedded for recall); `decision` what it
    decided (a JSON object). `created_at` (UTC) is when the episode became known: the
    decision moment. `outcome` (a JSON object) and `settled_at` arrive together, later, when
    the outcome is known; `settled_at` is after `created_at`. `symbol` is optional;
    `run_id` names the fund run that made it.
    """

    id: EpisodeId
    agent: NonEmptyStr
    symbol: NonEmptyStr | None = None
    created_at: UtcDatetime
    situation_text: NonEmptyStr
    decision: FrozenMap[str, Any]
    outcome: FrozenMap[str, Any] | None = None
    settled_at: UtcDatetime | None = None
    run_id: str | None = None

    @field_validator("agent")
    @classmethod
    def _not_global(cls, value: str) -> str:
        if value == GLOBAL:
            raise ValueError(f"{GLOBAL!r} is a lesson scope, not an agent")
        return value

    @field_validator("decision", "outcome", mode="before")
    @classmethod
    def _json_object(cls, value: object) -> object:
        return None if value is None else json_object(value)

    @model_validator(mode="after")
    def _settlement(self) -> Self:
        if (self.outcome is None) != (self.settled_at is None):
            raise ValueError("outcome and settled_at must be given both or neither")
        if self.settled_at is not None and self.settled_at <= self.created_at:
            raise ValueError(
                f"settled_at {self.settled_at.isoformat()} must be after created_at "
                f"{self.created_at.isoformat()}"
            )
        return self


class StoredLesson(Lesson):
    """A `Lesson` as stored: plus `superseded_by` / `superseded_at`, set together once a
    better near-duplicate replaced it (from `superseded_at` on, never before)."""

    superseded_by: LessonId | None = None
    superseded_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _supersession(self) -> Self:
        if (self.superseded_by is None) != (self.superseded_at is None):
            raise ValueError("superseded_by and superseded_at must be given both or neither")
        if self.superseded_at is not None and self.superseded_at < self.created_at:
            raise ValueError(
                f"superseded_at {self.superseded_at.isoformat()} is before created_at "
                f"{self.created_at.isoformat()}"
            )
        return self


def episode_row(episode: Episode) -> dict[str, object]:
    """`episode` as one `episodes` row (decision and outcome as canonical JSON text)."""
    return {
        "id": episode.id,
        "agent": episode.agent,
        "symbol": episode.symbol,
        "created_at": episode.created_at,
        "situation_text": episode.situation_text,
        "decision_json": canonical_json(dict(episode.decision)),
        "outcome_json": None if episode.outcome is None else canonical_json(dict(episode.outcome)),
        "settled_at": episode.settled_at,
        "run_id": episode.run_id,
    }


def episode_from_row(row: Mapping[str, Any]) -> Episode:
    """The `Episode` of an `episodes` row (`episode_row`'s inverse)."""
    outcome = row["outcome_json"]
    return Episode(
        id=row["id"],
        agent=row["agent"],
        symbol=row["symbol"],
        created_at=row["created_at"],
        situation_text=row["situation_text"],
        decision=json.loads(row["decision_json"]),
        outcome=None if outcome is None else json.loads(outcome),
        settled_at=row["settled_at"],
        run_id=row["run_id"],
    )


def lesson_row(lesson: StoredLesson) -> dict[str, object]:
    """`lesson` as one `lessons` row (tags and source ids as JSON lists)."""
    return {
        "id": lesson.id,
        "agent": lesson.agent,
        "created_at": lesson.created_at,
        "text": lesson.text,
        "tags": canonical_json(list(lesson.tags)),
        "source_episode_ids": canonical_json(list(lesson.source_episode_ids)),
        "quality_score": lesson.quality_score,
        "superseded_by": lesson.superseded_by,
        "superseded_at": lesson.superseded_at,
    }


def lesson_from_row(row: Mapping[str, Any]) -> StoredLesson:
    """The `StoredLesson` of a `lessons` row (`lesson_row`'s inverse)."""
    data = dict(row)
    data["tags"] = json.loads(data["tags"])
    data["source_episode_ids"] = json.loads(data["source_episode_ids"])
    return StoredLesson.model_validate(data)


def _records(frame: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = (
        frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
    )
    return rows


class MemoryStore:
    """Episodes and lessons in the `database`'s native tables, or in memory without one
    (unit tests, one-off scripts). Thread-safe."""

    def __init__(self, database: Database | None = None) -> None:
        self._database = database
        self._lock = threading.RLock()
        self._episodes: dict[str, Episode] = {}
        self._lessons: dict[str, StoredLesson] = {}

    @property
    def database(self) -> Database | None:
        """The database holding the records (`None`: in memory)."""
        return self._database

    # --- episodes ---------------------------------------------------------------------

    def add_episode(self, episode: Episode) -> None:
        """Store a new, unsettled `episode` (`DataError` if its id is stored already;
        `ValueError` if it is settled: outcomes arrive through `settle_episode`)."""
        if episode.settled_at is not None:
            raise ValueError(f"episode {episode.id} is settled: add it first, then settle it")
        if self._database is not None:
            self._database.insert_episodes([episode_row(episode)])
            return
        with self._lock:
            if episode.id in self._episodes:
                raise DataError(f"episodes already holds {episode.id}")
            self._episodes[episode.id] = episode

    def settle_episode(
        self, episode_id: str, outcome: Mapping[str, Any], *, settled_at: datetime
    ) -> Episode:
        """Record the `outcome` (a JSON object) of episode `episode_id`, known from the aware
        `settled_at`, and return the settled episode.

        `DataError` if the episode is unknown or already settled; `ValueError` if
        `settled_at` is naive or not after the episode's `created_at`, or the outcome is
        not a JSON object. Nothing is stored on an error.
        """
        _require_aware("settled_at", settled_at)
        payload = json_object(outcome)
        with self._lock:
            current = self.episode(episode_id)
            if current is None:
                raise DataError(f"no episode {episode_id!r} to settle")
            if current.settled_at is not None:
                raise DataError(f"episode {episode_id!r} is already settled")
            settled = Episode.model_validate(
                {**current.model_dump(), "outcome": payload, "settled_at": settled_at}
            )
            if self._database is not None:
                self._database.settle_episode(episode_id, canonical_json(payload), settled_at)
            else:
                self._episodes[episode_id] = settled
        return settled

    def episode(self, episode_id: str) -> Episode | None:
        """The stored episode `episode_id`, or `None`."""
        return self.episodes([episode_id]).get(episode_id)

    def episodes(self, ids: Iterable[str]) -> dict[str, Episode]:
        """The stored episodes among `ids`, by id (unknown ids are left out)."""
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return {}
        if self._database is None:
            with self._lock:
                return {i: self._episodes[i] for i in wanted if i in self._episodes}
        frame = self._database.query(
            "SELECT * FROM episodes WHERE list_contains($ids, id)", {"ids": wanted}
        )
        found = {row["id"]: episode_from_row(row) for row in _records(frame)}
        return {i: found[i] for i in wanted if i in found}

    def episode_ids(self) -> set[str]:
        """The ids of every stored episode."""
        if self._database is None:
            with self._lock:
                return set(self._episodes)
        return set(self._database.query("SELECT id FROM episodes")["id"])

    def all_episodes(self) -> Iterator[Episode]:
        """Every stored episode (for rebuilding the index)."""
        if self._database is None:
            with self._lock:
                items = list(self._episodes.values())
            yield from items
            return
        frame = self._database.query("SELECT * FROM episodes ORDER BY created_at, id")
        for row in _records(frame):
            yield episode_from_row(row)

    # --- lessons ----------------------------------------------------------------------

    def add_lesson(self, lesson: StoredLesson, *, supersedes: str | None = None) -> None:
        """Store a new `lesson`; if `supersedes` names a stored lesson of the same scope
        created at or before it, mark that one superseded by `lesson` at its `created_at`
        (one step: both or nothing).

        `DataError` for a stored id, or a `supersedes` lesson that is unknown or already
        superseded; `ValueError` for a lesson that is itself superseded, or a `supersedes`
        lesson of another scope or created after `lesson`.
        """
        if lesson.superseded_by is not None:
            raise ValueError(f"lesson {lesson.id} is superseded: store it as a new lesson")
        with self._lock:
            if supersedes is not None:
                old = self.lesson(supersedes)
                if old is not None:
                    if old.agent != lesson.agent:
                        raise ValueError(
                            f"lesson {supersedes} is in scope {old.agent!r}, not {lesson.agent!r}"
                        )
                    if old.created_at > lesson.created_at:
                        raise ValueError(
                            f"lesson {supersedes} was created after {lesson.id}: a lesson can "
                            "supersede only one created before it"
                        )
            if self._database is not None:
                self._database.insert_lesson(lesson_row(lesson), supersedes=supersedes)
                return
            if lesson.id in self._lessons:
                raise DataError(f"lessons already holds {lesson.id}")
            if supersedes is not None:
                old = self._lessons.get(supersedes)
                if old is None:
                    raise DataError(f"no lesson {supersedes!r} to supersede")
                if old.superseded_by is not None:
                    raise DataError(f"lesson {supersedes!r} is already superseded")
                self._lessons[supersedes] = old.model_copy(
                    update={"superseded_by": lesson.id, "superseded_at": lesson.created_at}
                )
            self._lessons[lesson.id] = lesson

    def lesson(self, lesson_id: str) -> StoredLesson | None:
        """The stored lesson `lesson_id`, or `None`."""
        if self._database is None:
            with self._lock:
                return self._lessons.get(lesson_id)
        frame = self._database.query("SELECT * FROM lessons WHERE id = ?", [lesson_id])
        rows = _records(frame)
        return lesson_from_row(rows[0]) if rows else None

    def lessons(self, scopes: Sequence[str]) -> list[StoredLesson]:
        """Every stored lesson of the given scopes (agent names and/or `global`), superseded
        ones included, oldest first."""
        wanted = list(dict.fromkeys(scopes))
        if not wanted:
            return []
        if self._database is None:
            with self._lock:
                items = [x for x in self._lessons.values() if x.agent in wanted]
            return sorted(items, key=lambda x: (x.created_at, x.id))
        frame = self._database.query(
            "SELECT * FROM lessons WHERE list_contains($scopes, agent) ORDER BY created_at, id",
            {"scopes": wanted},
        )
        return [lesson_from_row(row) for row in _records(frame)]

    def lesson_ids(self) -> set[str]:
        """The ids of every stored lesson."""
        if self._database is None:
            with self._lock:
                return set(self._lessons)
        return set(self._database.query("SELECT id FROM lessons")["id"])

    def all_lessons(self) -> Iterator[StoredLesson]:
        """Every stored lesson (for rebuilding the index)."""
        if self._database is None:
            with self._lock:
                items = list(self._lessons.values())
            yield from items
            return
        frame = self._database.query("SELECT * FROM lessons ORDER BY created_at, id")
        for row in _records(frame):
            yield lesson_from_row(row)


__all__ = [
    "GLOBAL",
    "Episode",
    "MemoryStore",
    "StoredLesson",
    "episode_from_row",
    "episode_row",
    "json_object",
    "lesson_from_row",
    "lesson_row",
]
