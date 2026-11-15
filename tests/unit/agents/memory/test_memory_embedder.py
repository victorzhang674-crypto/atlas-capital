"""Stage 27 Task 3 (brief task 2): the `Embedder` interface and the deterministic fake."""

import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from atlas.agents.memory import embedder as embedder_module
from atlas.agents.memory.embedder import Embedder, HashEmbedder, MiniLMEmbedder, make_embedder
from atlas.core.config import MemoryConfig


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b)


def test_hash_embedder_shape_norm_and_name() -> None:
    emb = HashEmbedder(dim=64)
    vectors = emb.embed(["Apple beat earnings", "rates rose", "x"])
    assert vectors.shape == (3, 64) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert emb.name == "hash-64-v1" and emb.dim == 64
    assert isinstance(emb, Embedder)
    assert emb.embed([]).shape == (0, 64)


def test_hash_embedder_is_deterministic_across_instances_and_processes() -> None:
    text = "Guidance raised; margins expanded"
    here = HashEmbedder().embed([text])[0]
    assert np.array_equal(here, HashEmbedder().embed([text])[0])
    code = (
        "from atlas.agents.memory.embedder import HashEmbedder;"
        f"print(','.join(repr(float(x)) for x in HashEmbedder().embed([{text!r}])[0]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    other = np.array([float(x) for x in out.strip().split(",")], dtype=np.float32)
    assert np.array_equal(here, other)  # no per-process hash() salt


def test_hash_embedder_relates_related_texts() -> None:
    emb = HashEmbedder()
    query, near, far = emb.embed(
        [
            "earnings beat with raised guidance",
            "the company beat earnings and raised its guidance",
            "the central bank raised interest rates",
        ]
    )
    assert cos(query, near) > 0.5 > cos(query, far)
    assert cos(query, query) == pytest.approx(1.0)


def test_hash_embedder_case_and_punctuation_do_not_matter() -> None:
    emb = HashEmbedder()
    a, b = emb.embed(["Earnings BEAT!", "earnings beat"])
    assert np.allclose(a, b)


def test_hash_embedder_empty_text_is_zero_and_stop_words_alone_are_not() -> None:
    emb = HashEmbedder()
    blank, stops, punct = emb.embed(["   ", "the and of", "!!!"])
    assert not blank.any()
    assert np.linalg.norm(stops) == pytest.approx(1.0)
    assert np.linalg.norm(punct) == pytest.approx(1.0)


def test_hash_embedder_rejects_tiny_dims() -> None:
    with pytest.raises(ValueError, match="dim"):
        HashEmbedder(dim=4)


class _FakeOnnx:
    """Stands in for chromadb's ONNXMiniLM_L6_V2: records where it would download."""

    instances: ClassVar[list["_FakeOnnx"]] = []

    def __init__(self, preferred_providers: list[str] | None = None) -> None:
        self.preferred_providers = preferred_providers
        self.DOWNLOAD_PATH: Any = None
        self.calls: list[list[str]] = []
        _FakeOnnx.instances.append(self)

    def __call__(self, texts: list[str]) -> list[np.ndarray]:
        self.calls.append(list(texts))
        return [np.full(384, 2.0, dtype=np.float32) for _ in texts]


def test_minilm_constructs_without_downloading(tmp_path: Path) -> None:
    emb = MiniLMEmbedder(model_dir=tmp_path / "models")
    assert emb.name == "all-MiniLM-L6-v2" and emb.dim == 384
    assert emb.model_path == tmp_path / "models" / "all-MiniLM-L6-v2"
    assert not (tmp_path / "models").exists()
    assert isinstance(emb, Embedder)


def test_minilm_expands_the_home_folder() -> None:
    emb = MiniLMEmbedder(model_dir=Path("~/models"))
    assert emb.model_path == Path.home() / "models" / "all-MiniLM-L6-v2"


def test_minilm_loads_the_model_once_into_model_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeOnnx.instances.clear()
    monkeypatch.setattr(embedder_module, "_onnx_minilm_class", lambda: _FakeOnnx)
    emb = MiniLMEmbedder(model_dir=tmp_path)
    first = emb.embed(["a", "b"])
    second = emb.embed(["c"])
    assert emb.embed([]).shape == (0, 384)
    assert len(_FakeOnnx.instances) == 1
    fake = _FakeOnnx.instances[0]
    assert tmp_path / "all-MiniLM-L6-v2" == fake.DOWNLOAD_PATH
    assert fake.preferred_providers == ["CPUExecutionProvider"]
    assert fake.calls == [["a", "b"], ["c"]]
    assert first.shape == (2, 384) and second.shape == (1, 384)
    assert first.dtype == np.float32
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0)  # normalised again


def test_minilm_wrong_width_from_the_model_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Narrow(_FakeOnnx):
        def __call__(self, texts: list[str]) -> list[np.ndarray]:
            return [np.ones(10, dtype=np.float32) for _ in texts]

    monkeypatch.setattr(embedder_module, "_onnx_minilm_class", lambda: _Narrow)
    with pytest.raises(ValueError, match="384"):
        MiniLMEmbedder(model_dir=tmp_path).embed(["a"])


def test_onnx_class_is_chromadbs_bundled_minilm() -> None:
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    assert embedder_module._onnx_minilm_class() is ONNXMiniLM_L6_V2


def test_make_embedder_from_config(tmp_path: Path) -> None:
    hashed = make_embedder(MemoryConfig(embedder="hash"))
    assert isinstance(hashed, HashEmbedder)
    minilm = make_embedder(MemoryConfig(embedder="minilm", model_dir=tmp_path))
    assert isinstance(minilm, MiniLMEmbedder)
    assert minilm.model_path == tmp_path / "all-MiniLM-L6-v2"


def test_minilm_switches_onnxruntime_telemetry_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import onnxruntime

    calls: list[str] = []
    monkeypatch.setattr(onnxruntime, "disable_telemetry_events", lambda: calls.append("off"))
    monkeypatch.setattr(embedder_module, "_onnx_minilm_class", lambda: _FakeOnnx)
    emb = MiniLMEmbedder(model_dir=tmp_path)
    assert calls == []  # nothing loads before the first embed
    emb.embed(["a"])
    emb.embed(["b"])
    assert calls == ["off"]
