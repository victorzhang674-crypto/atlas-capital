"""The vector index of agent memory: one ChromaDB collection per kind (`episodes`,
`lessons`), cosine space, our own embeddings (Chroma never loads a model), telemetry off.

Every query takes `created_before` (a cutoff) and returns only entries created strictly
before it: the point-in-time filter cannot be forgotten. Each entry carries `agent`,
`symbol` (`""` when none: Chroma metadata has no null) and `created_us` (microseconds since
the epoch, UTC). The records in `atlas.agents.memory.store` are the source of truth; this
index can be rebuilt from them.

A persistent index lives in a folder (the lake's `agents.memory.index_dir`). Without one it
is ephemeral; in-process ephemeral Chroma clients share one state, so each ephemeral index
uses collection names of its own.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from ulid import ULID

from atlas.core.errors import ConfigError
from atlas.core.logging import get_logger

Kind = Literal["episodes", "lessons"]
KINDS: tuple[Kind, ...] = ("episodes", "lessons")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_log = get_logger(__name__)


def micros(ts: datetime) -> int:
    """`ts` (timezone-aware) as whole microseconds since the Unix epoch, UTC."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime {ts!r}; pass a timezone-aware datetime")
    delta = ts - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


@dataclass(frozen=True)
class IndexEntry:
    """One vector to index: the record `id`, its unit `vector`, and the metadata every
    query filters on."""

    id: str
    vector: npt.NDArray[np.float32]
    agent: str
    created_at: datetime
    symbol: str | None = None


@dataclass(frozen=True)
class Hit:
    """One query result: the record `id`, its cosine `similarity` to the query (in [-1, 1])
    and, if asked for, its stored `vector`."""

    id: str
    similarity: float
    vector: npt.NDArray[np.float32] | None = None


class VectorIndex:
    """ChromaDB collections for episodes and lessons, made with embedder `embedder_name`
    (width `dim`); persistent under `path`, else ephemeral.

    Opening a persistent index built with another embedder or width raises `ConfigError`,
    unless `rebuild=True`, which empties both collections (`rebuilt` is then `True`; refill
    them from the records). Not thread-safe on its own: `AgentMemory` serialises writes.
    """

    def __init__(
        self, embedder_name: str, dim: int, path: Path | None = None, *, rebuild: bool = False
    ) -> None:
        if dim < 1:
            raise ValueError(f"dim must be positive, got {dim}")
        import chromadb  # imported on first use: importing atlas.agents stays light
        from chromadb.config import Settings

        self._embedder_name = embedder_name
        self._dim = dim
        self._settings: Any = Settings(anonymized_telemetry=False)
        if path is not None:
            path.mkdir(parents=True, exist_ok=True)
            self._client: Any = chromadb.PersistentClient(path=str(path), settings=self._settings)
            self._prefix = "atlas"
        else:
            self._client = chromadb.EphemeralClient(settings=self._settings)
            self._prefix = f"atlas-{str(ULID()).lower()}"
        self._ephemeral = path is None
        self._rebuilt = False
        self._collections: dict[str, Any] | None = {
            kind: self._open(kind, rebuild) for kind in KINDS
        }

    def _metadata(self) -> dict[str, Any]:
        return {"hnsw:space": "cosine", "embedder": self._embedder_name, "dim": self._dim}

    def _open(self, kind: Kind, rebuild: bool) -> Any:
        name = f"{self._prefix}-{kind}"
        existing = {c.name for c in self._client.list_collections()}
        if name in existing:
            collection = self._client.get_collection(name, embedding_function=None)
            meta = collection.metadata or {}
            same = meta.get("embedder") == self._embedder_name and meta.get("dim") == self._dim
            if same and not rebuild:
                return collection
            if not rebuild:
                raise ConfigError(
                    f"memory index {name!r} was built with embedder {meta.get('embedder')!r} "
                    f"(dim {meta.get('dim')}), not {self._embedder_name!r} (dim {self._dim}); "
                    "open it with rebuild=True to re-embed every record"
                )
            self._client.delete_collection(name)
            self._rebuilt = True
            _log.info("memory.index.rebuild", collection=name, embedder=self._embedder_name)
        elif rebuild:
            self._rebuilt = True
        return self._client.create_collection(
            name, metadata=self._metadata(), embedding_function=None
        )

    @property
    def embedder_name(self) -> str:
        """The embedder the vectors come from."""
        return self._embedder_name

    @property
    def dim(self) -> int:
        """The vector width."""
        return self._dim

    @property
    def rebuilt(self) -> bool:
        """`True` if opening emptied the collections (`rebuild=True`)."""
        return self._rebuilt

    @property
    def settings(self) -> Any:
        """The Chroma settings (telemetry off)."""
        return self._settings

    def _collection(self, kind: str) -> Any:
        if self._collections is None:
            raise ValueError("the memory index is closed")
        if kind not in KINDS:
            raise ValueError(f"unknown memory kind {kind!r}; use one of {KINDS}")
        return self._collections[kind]

    def _vector(self, vector: npt.ArrayLike) -> npt.NDArray[np.float32]:
        array = np.asarray(vector, dtype=np.float32)
        if array.shape != (self._dim,):
            raise ValueError(f"expected a vector of width {self._dim}, got shape {array.shape}")
        return array

    def add(self, kind: Kind, entries: Sequence[IndexEntry]) -> None:
        """Index `entries` (an id already indexed is overwritten). `ValueError` for a vector
        of another width or a zero vector (it has no direction)."""
        collection = self._collection(kind)
        if not entries:
            return
        vectors = [self._vector(e.vector) for e in entries]
        if any(not np.any(v) for v in vectors):
            raise ValueError("cannot index a zero vector")
        collection.upsert(
            ids=[e.id for e in entries],
            embeddings=[v.tolist() for v in vectors],
            metadatas=[
                {"agent": e.agent, "symbol": e.symbol or "", "created_us": micros(e.created_at)}
                for e in entries
            ],
        )

    def query(
        self,
        kind: Kind,
        vector: npt.ArrayLike,
        *,
        n: int,
        agents: Sequence[str],
        created_before: datetime,
        symbol: str | None = None,
        ids: Sequence[str] | None = None,
        with_vectors: bool = False,
    ) -> list[Hit]:
        """The `n` entries most similar to `vector` among those of `agents`, created strictly
        before `created_before`, of `symbol` (if given) and among `ids` (if given); most
        similar first. A zero `vector` finds nothing."""
        collection = self._collection(kind)
        if n < 1:
            raise ValueError(f"n must be positive, got {n}")
        query_vector = self._vector(vector)
        before = micros(created_before)
        if not agents or (ids is not None and not ids) or not np.any(query_vector):
            return []
        size = collection.count()
        if size == 0:
            return []
        clauses: list[dict[str, Any]] = [
            {"agent": {"$in": list(agents)}},
            {"created_us": {"$lt": before}},
        ]
        if symbol is not None:
            clauses.append({"symbol": {"$eq": symbol}})
        include = ["distances", "embeddings"] if with_vectors else ["distances"]
        result = collection.query(
            query_embeddings=[query_vector.tolist()],
            n_results=min(n, size),
            where={"$and": clauses},
            ids=list(ids) if ids is not None else None,
            include=include,
        )
        found_ids = result["ids"][0]
        distances = result["distances"][0]
        embeddings = result["embeddings"][0] if with_vectors else [None] * len(found_ids)
        return [
            Hit(
                id=i,
                similarity=float(np.clip(1.0 - d, -1.0, 1.0)),
                vector=None if e is None else np.asarray(e, dtype=np.float32),
            )
            for i, d, e in zip(found_ids, distances, embeddings, strict=True)
        ]

    def ids(self, kind: Kind) -> set[str]:
        """Every indexed id of `kind`."""
        return set(self._collection(kind).get(include=[])["ids"])

    def count(self, kind: Kind) -> int:
        """How many entries of `kind` are indexed."""
        return int(self._collection(kind).count())

    def delete(self, kind: Kind, ids: Sequence[str]) -> None:
        """Remove `ids` from the index (unknown ids are ignored)."""
        collection = self._collection(kind)
        if ids:
            collection.delete(ids=list(ids))

    def close(self) -> None:
        """Release the index; an ephemeral one is deleted. Idempotent."""
        if self._collections is None:
            return
        if self._ephemeral:
            for collection in self._collections.values():
                self._client.delete_collection(collection.name)
        self._collections = None
