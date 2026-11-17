"""Stage 28 Task 1: LangGraph dependencies, the `orchestration` config section, the error kind."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core import errors
from atlas.core.config import AtlasConfig, OrchestrationConfig, load_config


def _floor(requirements: list[str], name: str) -> str:
    for req in requirements:
        if req.split(">=")[0].strip() == name:
            return req.split(">=")[1].strip()
    raise AssertionError(f"no '{name}>=' requirement")


def test_langgraph_and_its_sqlite_checkpointer_are_runtime_dependencies(repo_root: Path) -> None:
    # Spec §11: orchestration is LangGraph; the brief's checkpointer is SQLite. Floors are the
    # versions probed on 2026-11-16 (plan D1).
    project = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]
    assert _floor(project["dependencies"], "langgraph") == "1.2"
    assert _floor(project["dependencies"], "langgraph-checkpoint-sqlite") == "3.1"


def test_langgraph_imports() -> None:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import StateGraph
    from langgraph.types import Send

    assert StateGraph and Send and SqliteSaver


def test_orchestration_config_defaults() -> None:
    orchestration = AtlasConfig().orchestration
    assert orchestration == OrchestrationConfig()
    assert orchestration.max_concurrency == 4


def test_orchestration_config_in_fund_yaml_matches_defaults(repo_root: Path) -> None:
    loaded = load_config("dev", config_dir=repo_root / "config").orchestration
    assert loaded == OrchestrationConfig()


@pytest.mark.parametrize(
    "bad", [{"max_concurrency": 0}, {"max_concurrency": -1}, {"unknown_key": 1}]
)
def test_orchestration_config_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OrchestrationConfig.model_validate(bad)


def test_max_concurrency_can_be_set_from_the_environment(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS__ORCHESTRATION__MAX_CONCURRENCY", "2")
    assert load_config("dev", config_dir=repo_root / "config").orchestration.max_concurrency == 2


def test_orchestration_error_is_an_atlas_error_sibling() -> None:
    assert issubclass(errors.OrchestrationError, errors.AtlasError)
    for other in (errors.DataError, errors.LookAheadError, errors.LLMError, errors.ConfigError):
        assert not issubclass(errors.OrchestrationError, other)
        assert not issubclass(other, errors.OrchestrationError)
