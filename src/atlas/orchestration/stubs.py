"""Deterministic stub agents for the research graph (Stage 28; replaced from Stage 29 on).

Every output is a pure function of the task (symbol, decision day) and, for the debate and
the director, of the reports they are given: no network, no LLM, no clock, no randomness. The
numbers come from BLAKE2b hashes, so they are equal across processes (Python's `hash()` is
salted per process), which lets a resumed run be compared with an uninterrupted one.
"""

import hashlib
from collections.abc import Mapping, Sequence

from ulid import ULID

from atlas.core.types import (
    AnalystReport,
    Claim,
    DebateRole,
    DebateTurn,
    Direction,
    IdPrefix,
    InvestmentThesis,
    ReturnDistribution,
    Signal,
)
from atlas.orchestration.research_graph import AnalystFn, ResearchTask, ResearchTeam

ANALYSTS = ("fundamental", "technical", "news", "sentiment", "quant")
"""The research analysts of spec §6, in graph order."""

_DIRECTIONS = (Direction.LONG, Direction.SHORT, Direction.FLAT)


def _digest(*parts: object) -> bytes:
    return hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=16).digest()


def _unit(*parts: object) -> float:
    """A number in [0, 1) fixed by `parts`."""
    return int.from_bytes(_digest(*parts)[:8], "big") / 2**64


def _evidence_id(analyst: str, task: ResearchTask) -> str:
    return f"stub:{analyst}:{task.symbol}:{task.as_of.isoformat()}"


def stub_analyst(name: str) -> AnalystFn:
    """A stub analyst called `name`: one signal citing one stub evidence id. Its direction,
    `strength` and the report's `confidence` (both in [0, 1)) are fixed by a hash of the
    analyst, the symbol and the day."""

    def run(task: ResearchTask) -> AnalystReport:
        u = _unit(name, task.symbol, task.as_of)
        return AnalystReport(
            ticker=task.symbol,
            as_of=task.as_of,
            analyst=name,
            summary=f"stub {name} report on {task.symbol}",
            signals=(
                Signal(
                    name=f"{name}_stub",
                    direction=_DIRECTIONS[int(u * 3)],
                    strength=round(u, 6),
                    evidence_ids=(_evidence_id(name, task),),
                ),
            ),
            risks=(f"stub {name} risk",),
            confidence=round(_unit(name, task.symbol, task.as_of, "confidence"), 6),
        )

    run.__name__ = f"stub_{name}"
    return run


def stub_debate(task: ResearchTask, reports: Mapping[str, AnalystReport]) -> list[DebateTurn]:
    """A bull turn citing the LONG signals and a bear turn citing the others."""
    signals = [signal for report in reports.values() for signal in report.signals]
    bull = tuple(s for s in signals if s.direction is Direction.LONG)
    bear = tuple(s for s in signals if s.direction is not Direction.LONG)

    def claims(side: tuple[Signal, ...]) -> tuple[Claim, ...]:
        return tuple(
            Claim(
                text=f"{s.name} points {s.direction}",
                evidence_ids=s.evidence_ids,
                strength=s.strength,
            )
            for s in side
        )

    return [
        DebateTurn(speaker=DebateRole.BULL, claims=claims(bull)),
        DebateTurn(speaker=DebateRole.BEAR, claims=claims(bear)),
    ]


def stub_director(
    task: ResearchTask,
    reports: Mapping[str, AnalystReport],
    debate: Sequence[DebateTurn],
) -> InvestmentThesis:
    """A thesis on the strength-weighted majority signal direction, citing every report's
    evidence. `exp_return` quantiles are simple returns over `horizon_days` = 20 trading days
    (not annualised): a band of half-width 2-10% (hashed) around a centre of half that width
    in the thesis direction (0 for FLAT); `conviction` is the reports' mean `confidence`."""
    signals = [signal for report in reports.values() for signal in report.signals]
    score = sum(s.direction.sign * s.strength for s in signals)
    direction = Direction.LONG if score > 0 else Direction.SHORT if score < 0 else Direction.FLAT
    spread = 0.02 + 0.08 * _unit("spread", task.symbol, task.as_of)
    centre = direction.sign * 0.5 * spread
    confidences = [report.confidence for report in reports.values()]
    return InvestmentThesis(
        id=f"{IdPrefix.THESIS}_{ULID.from_bytes(_digest('thesis', task.symbol, task.as_of))}",
        ticker=task.symbol,
        as_of=task.as_of,
        direction=direction,
        exp_return=ReturnDistribution(
            p10=round(centre - spread, 6), p50=round(centre, 6), p90=round(centre + spread, 6)
        ),
        horizon_days=20,
        conviction=round(sum(confidences) / len(confidences), 6),
        kill_criteria=("stub_kill_criterion",),
        evidence=tuple(sorted({e for s in signals for e in s.evidence_ids})),
    )


def stub_team(analysts: Sequence[str] = ANALYSTS) -> ResearchTeam:
    """A `ResearchTeam` of stub agents with the given analyst roster."""
    return ResearchTeam(
        analysts={name: stub_analyst(name) for name in analysts},
        debate=stub_debate,
        director=stub_director,
    )
