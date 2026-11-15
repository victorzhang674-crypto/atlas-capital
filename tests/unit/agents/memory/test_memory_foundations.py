"""Stage 27 Task 1: the `agents.memory` config section, the episode id and ChromaDB."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import (
    AtlasConfig,
    LessonCurationConfig,
    MemoryConfig,
    MemoryRenderConfig,
    load_config,
)


def test_memory_config_defaults() -> None:
    memory = AtlasConfig().agents.memory
    assert memory == MemoryConfig()
    assert memory.index_dir == "memory/chroma"
    assert memory.model_dir == Path("~/.cache/atlas/models")
    assert memory.embedder == "minilm"
    assert memory.default_k == 5
    assert 0 <= memory.min_similarity < 1
    assert memory.lessons == LessonCurationConfig()
    assert memory.lessons.dedupe_cosine == pytest.approx(0.92)
    assert memory.lessons.max_per_agent == 50
    assert memory.lessons.half_life_days == pytest.approx(180)
    assert memory.lessons.min_quality == pytest.approx(0.05)
    assert memory.lessons.default_quality == pytest.approx(0.5)
    assert memory.render == MemoryRenderConfig()
    assert memory.render.max_chars == 240


def test_memory_config_in_fund_yaml_matches_defaults(repo_root: Path) -> None:
    assert load_config("dev", config_dir=repo_root / "config").agents.memory == MemoryConfig()


@pytest.mark.parametrize(
    "bad",
    [
        {"index_dir": "/abs/chroma"},
        {"index_dir": "../outside"},
        {"index_dir": ""},
        {"embedder": "bert"},
        {"default_k": 0},
        {"min_similarity": -1.1},
        {"min_similarity": 1.0},
        {"min_similarity": float("nan")},
        {"lessons": {"dedupe_cosine": 0}},
        {"lessons": {"dedupe_cosine": 1.01}},
        {"lessons": {"max_per_agent": 0}},
        {"lessons": {"half_life_days": 0}},
        {"lessons": {"min_quality": 1.5}},
        {"lessons": {"default_quality": -0.1}},
        {"render": {"max_chars": 10}},
        {"unknown_key": 1},
    ],
)
def test_memory_config_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MemoryConfig.model_validate(bad)


def test_chromadb_is_a_runtime_dependency(repo_root: Path) -> None:
    # Spec §11: vector memory is ChromaDB (local); Stage 27 is its first user.
    project = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]
    assert any(dep.startswith("chromadb>=1.5") for dep in project["dependencies"])


def test_memory_settings_can_be_set_from_the_environment(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The model folder must be movable without editing YAML (e.g. a sandbox, CI).
    monkeypatch.setenv("ATLAS__AGENTS__MEMORY__MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("ATLAS__AGENTS__MEMORY__EMBEDDER", "hash")
    memory = load_config("dev", config_dir=repo_root / "config").agents.memory
    assert memory.model_dir == tmp_path / "models" and memory.embedder == "hash"


def test_every_config_section_can_be_set_from_the_environment() -> None:
    # Stage 26 added `agents` but not to the env allow-list (found in Stage 27).
    from atlas.core import config as config_module

    sections = set(AtlasConfig.model_fields) - {"secrets", "profile"}
    assert sections == config_module._SECTIONS
