"""Stage 17 Task 10 (brief task 5, part 2): `atlas research signal`."""

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import typer
from research_world import ResearchWorld, build_research_world
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.cli.research_cmd import parse_bound
from atlas.core.audit import AuditLog, audit_path
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.research import FIGURES

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> ResearchWorld:
    root = tmp_path_factory.mktemp("cli") / "lake"
    return build_research_world(Lake(root, registry=DEFAULT_REGISTRY.copy()))


IN_SAMPLE = {"ATLAS__BACKTEST__IN_SAMPLE_END": "2030-12-31"}
"""The research world's sessions end in 2024: with this the signal tests are in sample. The
default `backtest.in_sample_end` (2021-12-31) is used by the hold-out tests (`holdout=True`)."""


def _env(lake_root: Path, *, holdout: bool = False) -> dict[str, str]:
    env = {**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(lake_root)}
    return env if holdout else {**env, **IN_SAMPLE}


def invoke(lake_root: Path, *args: str, holdout: bool = False) -> object:
    return CliRunner().invoke(app, ["research", *args], env=_env(lake_root, holdout=holdout))


def test_research_signal_help() -> None:
    result = CliRunner().invoke(app, ["research", "--help"], env=BASE_ENV)
    assert result.exit_code == 0 and "signal" in _plain(result.output)
    result = CliRunner().invoke(app, ["research", "signal", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for option in (
        "--name",
        "--from",
        "--to",
        "--frequency",
        "--universe",
        "--out",
        "--allow-holdout",
        "--regimes",
        "--cached-factors",
    ):
        assert option in text


def test_year_bounds_parse() -> None:
    assert parse_bound("2016") == date(2016, 1, 1)
    assert parse_bound("2021", end=True) == date(2021, 12, 31)
    assert parse_bound("2019-03-04") == date(2019, 3, 4)
    assert parse_bound("2019-03-04", end=True) == date(2019, 3, 4)
    for bad in ("16", "2019-13-01", "March"):
        with pytest.raises(typer.BadParameter):
            parse_bound(bad)


def test_research_signal_writes_tearsheet_to_lake_runs(world: ResearchWorld) -> None:
    root = world.lake.root
    args = [
        "signal",
        "--name",
        "reversal_5d",
        "--from",
        str(world.day(75)),
        "--to",
        str(world.day(110)),
    ]
    result = invoke(root, *args, "--frequency", "weekly")
    assert result.exit_code == 0, result.output
    out = root / "runs" / "research" / "reversal_5d"
    for figure in FIGURES:
        assert (out / f"{figure}.png").is_file()
    summary = json.loads((out / "tearsheet.json").read_text())
    assert summary["signal"] == "reversal_5d" and summary["frequency"] == "weekly"
    assert summary["labels"]["horizons"] == [1, 5, 21, 63]
    run = summary["run"]  # audit finding A: the command runs inside a run context
    assert len(run["run_id"]) == 26 and run["stage"] == "research.signal"
    assert run["mode"] == "dev" and run["config_fingerprint"]
    text = _plain(result.output)
    assert "IC spearman" in text and "Long-short backtest" in text and str(out) in text
    assert f"Run {run['run_id']}" in text
    log = [json.loads(line) for line in (out / "research.log").read_text().splitlines()]
    evaluated = [r for r in log if r["event"] == "research.signal_evaluated"]
    assert len(evaluated) == 1 and evaluated[0]["run_id"] == run["run_id"]
    assert all(r["run_id"] == run["run_id"] for r in log)


def test_research_signal_out_option(world: ResearchWorld, tmp_path: Path) -> None:
    out = tmp_path / "sheet"
    args = ["signal", "--name", "rsi_14", "--from", str(world.day(80)), "--to", str(world.day(100))]
    result = invoke(world.lake.root, *args, "--frequency", "daily", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert json.loads((out / "tearsheet.json").read_text())["n_dates"] == 21


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--name", "nope", "--from", "2024", "--to", "2024"], "unknown signal"),
        (["--name", "reversal_5d", "--from", "2024-02-01", "--to", "2024-01-01"], "after"),
        (["--name", "reversal_5d", "--from", "2024-01-06", "--to", "2024-01-07"], "no trading"),
        (
            ["--name", "reversal_5d", "--from", "2024", "--to", "2024", "--frequency", "hourly"],
            "daily",
        ),
        (["--name", "reversal_5d", "--from", "24", "--to", "2024"], "YYYY"),
    ],
    ids=["bad-name", "from-after-to", "no-dates", "bad-frequency", "bad-date"],
)
def test_research_signal_bad_arguments_exit_2(
    world: ResearchWorld, args: list[str], message: str
) -> None:
    result = invoke(world.lake.root, "signal", *args)
    assert result.exit_code == 2
    assert message in _plain(result.output)


def test_research_signal_empty_lake_exit_1(tmp_path: Path) -> None:
    result = invoke(
        tmp_path / "empty", "signal", "--name", "reversal_5d", "--from", "2024-01", "--to", "2024"
    )
    assert result.exit_code == 2  # "2024-01" is not a date
    result = invoke(
        tmp_path / "empty",
        "signal",
        "--name",
        "reversal_5d",
        "--from",
        "2024-01-02",
        "--to",
        "2024-01-31",
    )
    assert result.exit_code == 1
    assert "Research failed" in _plain(result.output)


# --- Phase 2 checkpoint, Tier 2: the hold-out guard, `--regimes`, the header -----------------


def _signal_args(world: ResearchWorld, first: int = 75, last: int = 110) -> list[str]:
    days = [str(world.day(first)), str(world.day(last))]
    return ["signal", "--name", "reversal_5d", "--from", days[0], "--to", days[1]]


def _audit_events(root: Path, run_id: str) -> list[dict[str, object]]:
    path = audit_path(root, run_id)
    if not path.is_file():
        return []
    assert AuditLog.verify_chain(path)
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_research_signal_refuses_a_window_that_reads_the_holdout(
    world: ResearchWorld, tmp_path: Path
) -> None:
    out = tmp_path / "sheet"
    result = invoke(world.lake.root, *_signal_args(world), "--out", str(out), holdout=True)
    assert result.exit_code == 2
    text = " ".join(_plain(result.output).split())
    assert "2021-12-31" in text and "--allow-holdout" in text
    assert "in_sample_end" in text and "2021-10-01" in text  # the latest --to that fits
    assert not out.exists()


def test_allow_holdout_runs_and_records_the_override(world: ResearchWorld, tmp_path: Path) -> None:
    out = tmp_path / "sheet"
    args = [*_signal_args(world), "--frequency", "weekly", "--out", str(out), "--allow-holdout"]
    result = invoke(world.lake.root, *args, holdout=True)
    assert result.exit_code == 0, result.output
    text = " ".join(_plain(result.output).split())
    assert "Hold-out" in text and "audit log" in text
    summary = json.loads((out / "tearsheet.json").read_text())
    assert summary["labels"]["reads_holdout"] is True
    events = _audit_events(world.lake.root, summary["run"]["run_id"])
    assert [e["event_type"] for e in events] == ["OVERRIDE"]
    payload = events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["override"] == "allow_holdout" and payload["command"] == "research signal"
    assert payload["signal"] == "reversal_5d" and payload["in_sample_end"] == "2021-12-31"
    assert payload["from"] == str(world.day(75)) and payload["to"] == str(world.day(110))
    assert payload["last_label_session"] == summary["labels"]["last_session"] or (
        payload["last_label_session"] > summary["labels"]["last_session"]  # past the lake's end
    )
    assert events[0]["actor"] == "cli" and events[0]["run_id"] == summary["run"]["run_id"]


def test_allow_holdout_inside_the_sample_records_nothing(
    world: ResearchWorld, tmp_path: Path
) -> None:
    out = tmp_path / "sheet"
    args = [*_signal_args(world), "--frequency", "weekly", "--out", str(out), "--allow-holdout"]
    result = invoke(world.lake.root, *args)
    assert result.exit_code == 0, result.output
    assert "Hold-out" not in _plain(result.output)
    summary = json.loads((out / "tearsheet.json").read_text())
    assert summary["labels"]["reads_holdout"] is False
    assert _audit_events(world.lake.root, summary["run"]["run_id"]) == []


def test_an_override_that_cannot_be_recorded_stops_the_run(
    world: ResearchWorld, tmp_path: Path
) -> None:
    root = tmp_path / "lake"
    shutil.copytree(world.lake.root, root, ignore=shutil.ignore_patterns("runs"))
    runs = root / "runs"
    runs.mkdir()
    out = tmp_path / "sheet"
    args = [*_signal_args(world), "--frequency", "weekly", "--out", str(out), "--allow-holdout"]
    runs.chmod(0o555)  # the run's audit log cannot be created
    try:
        result = invoke(root, *args, holdout=True)
    finally:
        runs.chmod(0o755)
    assert result.exit_code == 1
    text = " ".join(_plain(result.output).split())
    assert "Research failed" in text and "audit log" in text
    assert not (out / "tearsheet.json").exists()


@pytest.fixture(scope="module")
def regime_days(world: ResearchWorld) -> list[date]:
    """Stores a regime for sessions 76..110 of the research world (not for session 75)."""
    from atlas.quant.regime import RegimeState, RegimeStore

    days = [world.day(i) for i in range(76, 111)]
    states = [
        RegimeState(
            as_of=day,
            data_date=world.day(75 + i),
            vol_regime="LOW" if i % 2 == 0 else "HIGH",
            trend_regime="UP" if i % 3 else "DOWN",
            hmm_probs=None,
            hmm_labels=(),
            macro_quadrant=None,
            stress_flag=False,
            risk_scalar=1.0,
        )
        for i, day in enumerate(days)
    ]
    RegimeStore(world.lake).write(states)
    return days


def test_regimes_option_reads_the_stored_history(
    world: ResearchWorld, regime_days: list[date], tmp_path: Path
) -> None:
    out = tmp_path / "sheet"
    args = [*_signal_args(world), "--frequency", "daily", "--out", str(out), "--regimes", "vol"]
    result = invoke(world.lake.root, *args)
    assert result.exit_code == 0, result.output
    by_regime = json.loads((out / "tearsheet.json").read_text())["by_regime"]
    assert by_regime["available"] is True and by_regime["source"] == "vol_regime"
    assert set(by_regime["h1"]) == {"HIGH", "LOW"}
    assert by_regime["h1"]["LOW"]["n_dates"] == 18 and by_regime["h1"]["HIGH"]["n_dates"] == 17
    assert by_regime["dates_without_regime"] == 1  # session 75
    text = " ".join(_plain(result.output).split())
    assert "IC by vol_regime" in text and "HIGH" in text and "LOW" in text
    assert "1 of 36 dates have no stored regime" in text


def test_regimes_option_other_kinds_and_errors(
    world: ResearchWorld, regime_days: list[date], tmp_path: Path
) -> None:
    out = tmp_path / "sheet"
    args = [*_signal_args(world), "--frequency", "weekly", "--out", str(out)]
    result = invoke(world.lake.root, *args, "--regimes", "trend")
    assert result.exit_code == 0, result.output
    by_regime = json.loads((out / "tearsheet.json").read_text())["by_regime"]
    assert by_regime["source"] == "trend_regime" and set(by_regime["h1"]) <= {"UP", "DOWN"}
    bad = invoke(world.lake.root, *args, "--regimes", "hmm")
    assert bad.exit_code == 2 and "vol, trend, quadrant" in " ".join(_plain(bad.output).split())
    none = invoke(world.lake.root, *args, "--regimes", "quadrant")  # no quadrant stored
    assert none.exit_code == 1
    assert "atlas regime build" in " ".join(_plain(none.output).split())


def test_header_counts_the_dates_with_an_ic(world: ResearchWorld, tmp_path: Path) -> None:
    out = tmp_path / "sheet"  # audit H: the first dates have no signal yet, so no IC
    args = [*_signal_args(world, 2, 30), "--frequency", "daily", "--out", str(out)]
    result = invoke(world.lake.root, *args)
    assert result.exit_code == 0, result.output
    summary = json.loads((out / "tearsheet.json").read_text())
    with_ic = summary["ic"][f"h{summary['headline_horizon']}"]["spearman"]["n_dates"]
    assert summary["n_dates"] == 29 and 0 < with_ic < 29
    text = " ".join(_plain(result.output).split())
    assert f"{with_ic} dates with an IC (29 requested)" in text


def test_header_is_plain_when_every_date_has_an_ic(world: ResearchWorld, tmp_path: Path) -> None:
    out = tmp_path / "sheet"
    args = [*_signal_args(world, 80, 100), "--frequency", "daily", "--out", str(out)]
    result = invoke(world.lake.root, *args)
    assert result.exit_code == 0, result.output
    text = " ".join(_plain(result.output).split())
    assert "21 dates with an IC" in text and "requested" not in text


# --- Phase 2 checkpoint, Tier 3 item 4: `--cached-factors` -------------------------------------


FACTOR_SETTINGS = {
    "backtest": {"in_sample_end": "2030-12-31"},
    "quant": {"factors": {"min_names": 5}, "research": {"min_names": 5}},
}
"""The Stage 16 factor world has 12 names: the default 20-name minimums would leave every
factor NaN."""


@pytest.fixture(scope="module")
def factor_lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    """The factor world (bars to 2024-02-29) with its December and January month-end panels
    cached in the lake itself."""
    from factor_world import build_factor_world

    from atlas.core.config import AtlasConfig
    from atlas.quant.factors import build_factor_cache

    lake = Lake(tmp_path_factory.mktemp("cli_factors") / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    config = AtlasConfig.model_validate(FACTOR_SETTINGS)
    build_factor_cache(lake, [date(2023, 12, 29), date(2024, 1, 31)], config=config)
    return lake


def _factor_signal(lake: Lake, tmp_path: Path, out: Path, *extra: str) -> object:
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "fund.yaml").write_text(yaml.safe_dump(FACTOR_SETTINGS))
    args = ["--config-dir", str(config_dir), "research", "signal", "--name", "earnings_yield",
            "--from", "2024-01-03", "--to", "2024-02-01", "--frequency", "monthly",
            "--out", str(out), *extra]  # fmt: skip
    env = {**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(lake.root)}
    return CliRunner().invoke(app, args, env=env)


def test_cached_factors_reads_the_stored_panels(factor_lake: Lake, tmp_path: Path) -> None:
    out = tmp_path / "sheet"
    result = _factor_signal(factor_lake, tmp_path, out, "--cached-factors")
    assert result.exit_code == 0, result.output
    summary = json.loads((out / "tearsheet.json").read_text())
    assert summary["factor_cache"] == {
        "dates_from_cache": 2,
        "dates_computed": 0,
        "oldest_panel_sessions": 2,  # 2023-12-29 for the 2024-01-03 date
        "newest_panel_sessions": 1,  # 2024-01-31 for the 2024-02-01 date
    }
    assert summary["n_symbols"] >= 10  # real values, not an all-NaN panel
    text = " ".join(_plain(result.output).split())
    assert "Factor cache: 2 of 2 dates read stored panels (1 to 2 sessions old)" in text
    plain = tmp_path / "plain"
    result = _factor_signal(factor_lake, tmp_path, plain)
    assert result.exit_code == 0, result.output
    assert json.loads((plain / "tearsheet.json").read_text())["factor_cache"] is None
    assert "Factor cache" not in _plain(result.output)


def test_cached_factors_is_refused_for_a_price_feature(world: ResearchWorld) -> None:
    result = invoke(world.lake.root, *_signal_args(world), "--cached-factors")
    assert result.exit_code == 2
    assert "fundamental factor" in " ".join(_plain(result.output).split())


# --- Stage 23: `atlas research pairs --as-of` -------------------------------------------------


@pytest.fixture(scope="module")
def pairs_lake(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object]:
    from statarb_world import build_pairs_lake, planted_universe

    root = tmp_path_factory.mktemp("cli_pairs") / "lake"
    universe = planted_universe(101)
    build_pairs_lake(Lake(root, registry=DEFAULT_REGISTRY.copy()), universe)
    return root, universe


def test_research_pairs_help() -> None:
    result = CliRunner().invoke(app, ["research", "pairs", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for option in ("--as-of", "--universe", "--out"):
        assert option in text


def test_research_pairs_lists_current_pairs(pairs_lake: tuple[Path, object]) -> None:
    from statarb_world import AS_OF

    from atlas.core.config import AtlasConfig
    from atlas.data.view import DataView
    from atlas.quant.statarb import pairs_snapshot

    root, universe = pairs_lake
    result = invoke(root, "pairs", "--as-of", AS_OF.isoformat())
    assert result.exit_code == 0, result.output
    text = _plain(result.output)
    snap = pairs_snapshot(DataView(Lake(root), AS_OF, AtlasConfig()))
    assert snap.pairs
    for pid in snap.table["pair_id"]:
        assert pid in text
    for header in ("pair", "sector", "beta", "half-life", "p-value", "z", "signal"):
        assert header in text
    assert f"{len(snap.pairs)} pairs" in text
    out = root / "runs" / "research" / "pairs" / AS_OF.isoformat()
    table = pd.read_csv(out / "pairs.csv")
    assert list(table["pair_id"]) == list(snap.table["pair_id"])
    summary = json.loads((out / "pairs.json").read_text())
    assert summary["as_of"] == AS_OF.isoformat() and summary["n_pairs"] == len(snap.pairs)
    assert summary["universe"] == "sp500"
    assert summary["run"]["stage"] == "research.pairs" and len(summary["run"]["run_id"]) == 26
    assert summary["signals"] == [s.model_dump() for s in snap.signals]
    assert summary["config"]["formation"] == 504
    assert set(universe.planted) <= set(table["pair_id"])  # type: ignore[attr-defined]


def test_research_pairs_out_option(pairs_lake: tuple[Path, object], tmp_path: Path) -> None:
    root, _ = pairs_lake
    target = tmp_path / "mine"
    result = invoke(root, "pairs", "--as-of", "2024-03-01", "--out", str(target))
    assert result.exit_code == 0, result.output
    assert (target / "pairs.csv").is_file() and (target / "pairs.json").is_file()


def test_research_pairs_bad_arguments_exit_2(pairs_lake: tuple[Path, object]) -> None:
    root, _ = pairs_lake
    assert invoke(root, "pairs", "--as-of", "2024-13-01").exit_code == 2
    assert invoke(root, "pairs").exit_code == 2
    assert invoke(root, "pairs", "--as-of", "2024-03-01", "--universe", "nope").exit_code == 2


def test_research_pairs_empty_lake_exit_1(tmp_path: Path) -> None:
    result = invoke(tmp_path / "empty", "pairs", "--as-of", "2024-03-01")
    assert result.exit_code == 1
    assert "Pairs failed" in _plain(result.output)


def test_research_pairs_no_members_says_so(tmp_path: Path) -> None:
    from statarb_world import build_pairs_lake, planted_universe

    root = tmp_path / "lake"
    build_pairs_lake(
        Lake(root, registry=DEFAULT_REGISTRY.copy()),
        planted_universe(102),
        end_date=date(2015, 1, 2),
    )
    result = invoke(root, "pairs", "--as-of", "2024-03-01")
    assert result.exit_code == 0, result.output
    assert "No pairs selected at 2024-03-01" in _plain(result.output)
