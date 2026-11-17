"""Stage 28 Task 4: the deterministic stub research team."""

from datetime import date

import pytest
from graph_world import DAY

from atlas.core.types import AnalystReport, DebateTurn, InvestmentThesis
from atlas.orchestration.research_graph import ResearchTask
from atlas.orchestration.stubs import ANALYSTS, stub_team

TASK = ResearchTask(symbol="AAA", as_of=DAY)


def test_the_default_analysts_are_the_five_of_the_spec() -> None:
    # Spec §6: fundamental, technical, news & event, sentiment, quant.
    assert ANALYSTS == ("fundamental", "technical", "news", "sentiment", "quant")
    assert tuple(stub_team().analysts) == ANALYSTS


def test_stub_outputs_are_valid_contracts_for_the_task() -> None:
    team = stub_team()
    reports = {name: run(TASK) for name, run in team.analysts.items()}
    for name, report in reports.items():
        assert isinstance(report, AnalystReport)
        assert (report.ticker, report.as_of, report.analyst) == ("AAA", DAY, name)
    turns = team.debate(TASK, reports)
    assert turns and all(isinstance(turn, DebateTurn) for turn in turns)
    thesis = team.director(TASK, reports, turns)
    assert isinstance(thesis, InvestmentThesis)
    assert (thesis.ticker, thesis.as_of) == ("AAA", DAY)
    cited = {e for report in reports.values() for s in report.signals for e in s.evidence_ids}
    assert set(thesis.evidence) <= cited and thesis.evidence


def test_stubs_are_deterministic_across_teams() -> None:
    def run_all(team_index: int) -> tuple[object, ...]:
        team = stub_team()
        reports = {name: run(TASK) for name, run in team.analysts.items()}
        turns = team.debate(TASK, reports)
        return (reports, turns, team.director(TASK, reports, turns))

    assert run_all(1) == run_all(2)


@pytest.mark.parametrize(
    "other", [ResearchTask(symbol="BBB", as_of=DAY), ResearchTask("AAA", date(2024, 3, 4))]
)
def test_stub_outputs_depend_on_symbol_and_day(other: ResearchTask) -> None:
    team = stub_team()
    a = {name: run(TASK) for name, run in team.analysts.items()}
    b = {name: run(other) for name, run in team.analysts.items()}
    assert [r.confidence for r in a.values()] != [r.confidence for r in b.values()]
    thesis_a = team.director(TASK, a, team.debate(TASK, a))
    thesis_b = team.director(other, b, team.debate(other, b))
    assert thesis_a.id != thesis_b.id


def test_a_custom_roster() -> None:
    team = stub_team(("alpha", "beta"))
    assert tuple(team.analysts) == ("alpha", "beta")
    assert team.analysts["beta"](TASK).analyst == "beta"
