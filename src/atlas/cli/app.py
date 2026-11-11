"""The `atlas` command (Typer). Stage 14 adds the `data` group, Stage 17 `research`, Stage 21
`regime`, the Phase 2 checkpoint `factors` (Stage 16) and `risk-model` (Stage 19); later stages
add theirs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from atlas.cli.data_cmd import data_app
from atlas.cli.factors_cmd import factors_app
from atlas.cli.regime_cmd import regime_app
from atlas.cli.research_cmd import research_app
from atlas.cli.risk_model_cmd import risk_model_app

app = typer.Typer(
    name="atlas",
    help="Atlas Capital: a multi-agent hedge fund simulator (research and paper trading only).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
app.add_typer(data_app, name="data")
app.add_typer(research_app, name="research")
app.add_typer(regime_app, name="regime")
app.add_typer(factors_app, name="factors")
app.add_typer(risk_model_app, name="risk-model")


@dataclass(frozen=True)
class CliOptions:
    """The global options every command reads: the config `profile` and `config_dir`."""

    profile: str
    config_dir: Path


@app.callback()
def _options(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option(help="Config profile: dev, backtest or paper.")] = "dev",
    config_dir: Annotated[
        Path, typer.Option(help="Directory holding fund.yaml and the other config files.")
    ] = Path("config"),
) -> None:
    """Atlas Capital command line."""
    ctx.obj = CliOptions(profile=profile, config_dir=config_dir)


def main() -> None:
    """Entry point of the `atlas` script (`[project.scripts]`)."""
    app()


__all__ = ["CliOptions", "app", "main"]
