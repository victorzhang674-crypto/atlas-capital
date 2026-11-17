"""The LangGraph state of the fund graph and of one symbol's research subgraph (Stage 28).

`FundState` is the fund-level state (the brief's keys). A field that parallel nodes write has
a reducer that merges the writes: `reports` (`merge_reports`, symbol → analyst → report),
`theses` and `proposals` (`merge_keyed`), `errors` (`append_errors`). Every other field has a
single writer and keeps LangGraph's last-value channel, so two parallel writes to it raise
LangGraph's `InvalidUpdateError`.

The reducers never mutate their inputs and, for disjoint writes, give the same result in any
order. Writing the same value twice under one key is accepted (idempotent); writing a
*different* value under a key that already has one raises `OrchestrationError`: two nodes
that disagree are a bug, never something to merge silently.

Sequences are lists, not tuples: a checkpoint stores a top-level tuple as a list, so a
resumed state would otherwise differ from an uninterrupted one.
"""

from collections.abc import Mapping
from datetime import date
from enum import Enum
from typing import Annotated, TypedDict, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from atlas.core.errors import OrchestrationError
from atlas.core.types import (
    AnalystReport,
    CapitalAllocation,
    ComplianceResult,
    DebateTurn,
    InvestmentThesis,
    MacroOutlook,
    Order,
    PodProposal,
    RiskDecision,
    Symbol,
    TargetPortfolio,
)
from atlas.orchestration.errors import NodeError
from atlas.quant.regime.state import RegimeState


def merge_keyed[K, V](left: Mapping[K, V] | None, right: Mapping[K, V] | None) -> dict[K, V]:
    """`left` updated with `right`, as a new dict; a key present on both sides with different
    values raises `OrchestrationError` (an equal value is accepted)."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if key in merged and merged[key] != value:
            raise OrchestrationError(f"conflicting writes for {key!r}")
        merged[key] = value
    return merged


def merge_reports(
    left: Mapping[str, Mapping[str, AnalystReport]] | None,
    right: Mapping[str, Mapping[str, AnalystReport]] | None,
) -> dict[str, dict[str, AnalystReport]]:
    """Merge two `symbol → analyst → report` maps into a new one; a conflicting report for
    one symbol and analyst raises `OrchestrationError` naming both."""
    merged = {symbol: dict(by_analyst) for symbol, by_analyst in (left or {}).items()}
    for symbol, by_analyst in (right or {}).items():
        try:
            merged[symbol] = merge_keyed(merged.get(symbol), by_analyst)
        except OrchestrationError as exc:
            raise OrchestrationError(f"conflicting reports on {symbol!r}: {exc}") from exc
    return merged


def append_errors(left: list[NodeError] | None, right: list[NodeError] | None) -> list[NodeError]:
    """`left` then `right` as a new list (either side may be `None`)."""
    return [*(left or ()), *(right or ())]


Reports = Annotated[dict[Symbol, dict[str, AnalystReport]], merge_reports]
"""symbol → analyst name → report, merged across parallel writers."""
Theses = Annotated[dict[Symbol, InvestmentThesis], merge_keyed]
"""symbol → thesis, merged across parallel writers."""
Errors = Annotated[list[NodeError], append_errors]
"""Failed nodes, in the order their updates were applied."""


class FundState(TypedDict, total=False):
    """The fund graph's state for one decision day `as_of`.

    `regime` (Stage 21), `macro_outlook` (Stage 32), `research_queue` (symbols to research
    today, Stage 33), `reports` and `theses` (written by the research subgraphs), `proposals`
    (pod id → proposal, Stages 39-44), `allocation` (CIO), `target` (portfolio
    construction), `risk_decision`, `compliance`, `orders`, `errors` (failed nodes).
    """

    as_of: date
    regime: RegimeState
    macro_outlook: MacroOutlook
    research_queue: list[Symbol]
    reports: Reports
    theses: Theses
    proposals: Annotated[dict[str, PodProposal], merge_keyed]
    allocation: CapitalAllocation
    target: TargetPortfolio
    risk_decision: RiskDecision
    compliance: ComplianceResult
    orders: list[Order]
    errors: Errors


class ResearchInput(TypedDict):
    """What the fund graph sends to one research subgraph (a `Send` payload)."""

    symbol: Symbol
    as_of: date


class ResearchOutput(TypedDict, total=False):
    """What one research subgraph hands back to the fund state."""

    reports: Reports
    theses: Theses
    errors: Errors


class ResearchState(TypedDict, total=False):
    """One symbol's research subgraph state: the input, the work in progress
    (`analyst_reports`, `debate`, `thesis`) and the output fields."""

    symbol: Symbol
    as_of: date
    analyst_reports: Annotated[dict[str, AnalystReport], merge_keyed]
    debate: list[DebateTurn]
    thesis: InvestmentThesis
    reports: Reports
    theses: Theses
    errors: Errors


def state_types() -> tuple[tuple[str, str], ...]:
    """`(module, class name)` of every Pydantic model and enum a `FundState` or
    `ResearchState` value can hold (nested fields included), sorted: the checkpoint
    serializer's allow-list (`atlas.orchestration.checkpoint.serializer`)."""
    found: set[type] = set()

    def visit(hint: object) -> None:
        if get_origin(hint) is not None:
            for arg in get_args(hint):
                visit(arg)
            return
        if not isinstance(hint, type) or hint in found:
            return
        if issubclass(hint, BaseModel):
            found.add(hint)
            for info in hint.model_fields.values():
                visit(info.annotation)
        elif issubclass(hint, Enum):
            found.add(hint)

    for schema in (FundState, ResearchState):
        for hint in get_type_hints(schema, include_extras=True).values():
            visit(hint)
    return tuple(sorted((cls.__module__, cls.__qualname__) for cls in found))
