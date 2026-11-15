"""Text embedders for agent memory: text in, L2-normalised float32 vectors out.

`HashEmbedder` is deterministic and needs nothing (tests, offline use): a hashed bag of words.
`MiniLMEmbedder` is all-MiniLM-L6-v2, the small sentence-transformers model, run in ONNX
through chromadb's bundled wrapper (onnxruntime, no torch). Its model (~79 MB, SHA-256
checked by chromadb) is downloaded once, on the first `embed`, into `model_dir`;
constructing one never touches the network, and onnxruntime's telemetry events are
switched off before the model loads.
"""

import hashlib
import re
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from atlas.core.config import MemoryConfig

Vectors = npt.NDArray[np.float32]
"""A `(n, dim)` array of embeddings, one row per text."""

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    ]
)
"""Words the hash embedder ignores, unless a text has nothing else."""


@runtime_checkable
class Embedder(Protocol):
    """Turns texts into vectors.

    `name` identifies the model (the vector index refuses to mix embedders); `dim` is the
    vector width. `embed(texts)` returns a `(len(texts), dim)` float32 array whose rows have
    unit L2 norm (a row of zeros for a text with nothing to embed).
    """

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> Vectors: ...


def normalise(vectors: npt.ArrayLike, dim: int) -> Vectors:
    """`vectors` (one row per text) as float32 with unit-norm rows (zero rows stay zero);
    `ValueError` unless it is a `(n, dim)` array."""
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != dim:
        raise ValueError(f"expected an (n, {dim}) array of vectors, got shape {array.shape}")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return np.divide(array, norms, out=np.zeros_like(array), where=norms > 0)


class HashEmbedder:
    """A deterministic bag-of-words embedder (the brief's fake, also fine offline).

    Lower-cased word tokens (letters and digits) minus `STOP_WORDS` (kept if a text has only
    stop words) are hashed with BLAKE2b into `dim` buckets with a sign, counted, and the
    vector is L2-normalised. Texts that share words are close; nothing depends on the
    process (no `hash()` salt). Blank text gives a zero vector.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim < 8:
            raise ValueError(f"dim must be at least 8, got {dim}")
        self._dim = dim

    @property
    def name(self) -> str:
        """`hash-<dim>-v1`."""
        return f"hash-{self._dim}-v1"

    @property
    def dim(self) -> int:
        """The vector width."""
        return self._dim

    def _tokens(self, text: str) -> list[str]:
        words = _TOKEN_RE.findall(text.lower())
        kept = [w for w in words if w not in STOP_WORDS]
        if kept:
            return kept
        if words:
            return words
        stripped = text.strip()
        return [stripped] if stripped else []

    def _vector(self, text: str) -> npt.NDArray[np.float32]:
        vector = np.zeros(self._dim, dtype=np.float32)
        for token, count in Counter(self._tokens(text)).items():
            digest = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest())
            sign = 1.0 if digest & 1 else -1.0
            vector[(digest >> 1) % self._dim] += sign * count
        return vector

    def embed(self, texts: Sequence[str]) -> Vectors:
        """One unit-norm row per text (see the class docstring)."""
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return normalise(np.stack([self._vector(t) for t in texts]), self._dim)


def _onnx_minilm_class() -> Callable[..., Any]:
    """chromadb's bundled ONNX all-MiniLM-L6-v2 (imported only when first needed)."""
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    return ONNXMiniLM_L6_V2


class MiniLMEmbedder:
    """all-MiniLM-L6-v2 (384-d) in ONNX on the CPU, model files under
    `<model_dir>/all-MiniLM-L6-v2` (`~` expanded), downloaded on the first `embed` if
    missing. Thread-safe."""

    MODEL = "all-MiniLM-L6-v2"
    DIM = 384

    def __init__(self, model_dir: Path) -> None:
        self._model_path = Path(model_dir).expanduser() / self.MODEL
        self._lock = threading.Lock()
        self._function: Callable[[list[str]], Any] | None = None

    @property
    def name(self) -> str:
        """`all-MiniLM-L6-v2`."""
        return self.MODEL

    @property
    def dim(self) -> int:
        """384."""
        return self.DIM

    @property
    def model_path(self) -> Path:
        """The folder holding the model files."""
        return self._model_path

    def _model(self) -> Callable[[list[str]], Any]:
        with self._lock:
            if self._function is None:
                import onnxruntime

                onnxruntime.disable_telemetry_events()  # no platform telemetry from us
                function = _onnx_minilm_class()(preferred_providers=["CPUExecutionProvider"])
                function.DOWNLOAD_PATH = self._model_path
                self._function = function
            return self._function

    def embed(self, texts: Sequence[str]) -> Vectors:
        """One unit-norm row per text (the first call may download the model)."""
        if not texts:
            return np.zeros((0, self.DIM), dtype=np.float32)
        model = self._model()
        with self._lock:  # one ONNX run at a time
            rows = model(list(texts))
        return normalise(np.stack([np.asarray(r, dtype=np.float32) for r in rows]), self.DIM)


def make_embedder(config: MemoryConfig) -> Embedder:
    """The embedder `config.embedder` names (`minilm` uses `config.model_dir`)."""
    if config.embedder == "hash":
        return HashEmbedder()
    return MiniLMEmbedder(config.model_dir)
