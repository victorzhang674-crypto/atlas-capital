"""Test helpers for the Stage 28 orchestration tests: contract factories and spy teams."""

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from ulid import ULID

from atlas.core.types import (
    AnalystReport,
    AssetView,
    Breach,
    CapitalAllocation,
    Catalyst,
    Claim,
    ComplianceResult,
    DebateRole,
    DebateTurn,
    Direction,
    IdPrefix,
    InvestmentThesis,
    MacroOutlook,
    Order,
    OrderStatus,
    OrderType,
    PodProposal,
    ReturnDistribution,
    RiskDecision,
    RiskStatus,
    Severity,
    Side,
    Signal,
    TargetPortfolio,
    ThesisStatus,
    TimeInForce,
    View,
    Violation,
)
from atlas.orchestration.errors import NodeError
from atlas.orchestration.research_graph import ResearchTask, ResearchTeam
from atlas.orchestration.stubs import ANALYSTS, stub_team
from atlas.quant.regime.state import RegimeState

DAY = date(2024, 3, 1)


def report(symbol: str, analyst: str, *, confidence: float = 0.5, day: date = DAY) -> AnalystReport:
    """A valid `AnalystReport` on `symbol` by `analyst`."""
    return AnalystReport(
        ticker=symbol,
        as_of=day,
        analyst=analyst,
        summary=f"{analyst} view on {symbol}",
        signals=(
            Signal(name="stub", direction=Direction.LONG, strength=0.5, evidence_ids=("e1",)),
        ),
        risks=("stub risk",),
        confidence=confidence,
    )


def thesis(symbol: str, *, conviction: float = 0.5, day: date = DAY) -> InvestmentThesis:
    """A valid `InvestmentThesis` on `symbol` (fixed id per symbol and conviction)."""
    seed = (symbol + str(conviction)).encode().ljust(16, b"_")[:16]
    return InvestmentThesis(
        id=f"{IdPrefix.THESIS}_{ULID.from_bytes(seed)}",
        ticker=symbol,
        as_of=day,
        direction=Direction.LONG,
        exp_return=ReturnDistribution(p10=-0.05, p50=0.02, p90=0.08),
        horizon_days=20,
        conviction=conviction,
        kill_criteria=("price_below(1)",),
        evidence=("e1",),
    )


def node_error(node: str, symbol: str | None = None, *, message: str = "boom") -> NodeError:
    """A `NodeError` from `node` (on `symbol`)."""
    return NodeError(node=node, symbol=symbol, as_of=DAY, kind="RuntimeError", message=message)


# --- spy teams --------------------------------------------------------------------------

Hook = Callable[[], None]
"""Run before a stub call: may raise, wait on a barrier or sleep."""


class Spy:
    """Wraps the stub team: logs every call `(symbol, node)`, runs hooks before calls, can
    replace outputs, and tracks how many calls and symbols are active at once."""

    def __init__(
        self,
        analysts: Sequence[str] = ANALYSTS,
        *,
        hooks: Mapping[tuple[str, str], Hook] | None = None,
        outputs: Mapping[tuple[str, str], Callable[[Any], Any]] | None = None,
        hold_seconds: float = 0.0,
    ) -> None:
        self.analysts = tuple(analysts)
        self.hooks = dict(hooks or {})
        self.outputs = dict(outputs or {})
        self.hold_seconds = hold_seconds
        self.calls: list[tuple[str, str]] = []
        self.seen: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()
        self._active = 0
        self._active_symbols: dict[str, int] = {}
        self.peak_calls = 0
        self.peak_symbols = 0
        self._stubs = stub_team(self.analysts)

    def _call(self, task: ResearchTask, node: str, make: Callable[[], Any]) -> Any:
        with self._lock:
            self.calls.append((task.symbol, node))
            self._active += 1
            self._active_symbols[task.symbol] = self._active_symbols.get(task.symbol, 0) + 1
            self.peak_calls = max(self.peak_calls, self._active)
            self.peak_symbols = max(self.peak_symbols, len(self._active_symbols))
        try:
            for key in ((task.symbol, node), ("*", node)):
                if key in self.hooks:
                    self.hooks[key]()
            if self.hold_seconds:
                time.sleep(self.hold_seconds)
            out = make()
            for key in ((task.symbol, node), ("*", node)):
                if key in self.outputs:
                    out = self.outputs[key](out)
            return out
        finally:
            with self._lock:
                self._active -= 1
                self._active_symbols[task.symbol] -= 1
                if not self._active_symbols[task.symbol]:
                    del self._active_symbols[task.symbol]

    def team(self) -> ResearchTeam:
        """A `ResearchTeam` whose slots go through this spy."""

        def analyst(name: str) -> Callable[[ResearchTask], Any]:
            def run(task: ResearchTask) -> Any:
                return self._call(task, name, lambda: self._stubs.analysts[name](task))

            return run

        def debate(task: ResearchTask, reports: Mapping[str, Any]) -> Any:
            self.seen[(task.symbol, "debate")] = dict(reports)
            return self._call(task, "debate", lambda: self._stubs.debate(task, reports))

        def director(task: ResearchTask, reports: Mapping[str, Any], turns: Any) -> Any:
            self.seen[(task.symbol, "director")] = (dict(reports), list(turns))
            return self._call(task, "director", lambda: self._stubs.director(task, reports, turns))

        return ResearchTeam(
            analysts={name: analyst(name) for name in self.analysts},
            debate=debate,
            director=director,
        )

    def calls_of(self, symbol: str) -> list[str]:
        """The nodes called for `symbol`, in call order."""
        return [node for s, node in self.calls if s == symbol]


def raiser(exc: BaseException) -> Hook:
    """A hook that raises `exc`."""

    def hook() -> None:
        raise exc

    return hook


def barrier_hook(parties: int, timeout: float = 5.0) -> Hook:
    """A hook that waits until `parties` calls wait at once (raises `BrokenBarrierError` after
    `timeout` seconds if they never do: proof the calls were not running in parallel)."""
    barrier = threading.Barrier(parties)

    def hook() -> None:
        barrier.wait(timeout)

    return hook


# --- a full fund state (every contract type the state can hold) -------------------------


def full_fund_state() -> dict[str, Any]:
    """A `FundState` with a value in every field."""
    view = View(ticker="AAA", exp_return=0.12, confidence=0.55, horizon_days=63)
    asset_view = AssetView(etf="TLT", direction=Direction.SHORT, conviction=0.7, horizon_days=40)
    breach = Breach(limit="max_gross", value=2.1, threshold=2.0, detail="gross 210%")
    order = Order(
        id=f"{IdPrefix.ORDER}_{ULID.from_bytes(b'order-0000000001')}",
        symbol="BRK.B",
        side=Side.SELL,
        quantity=Decimal("150.5"),
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        limit_price=Decimal("410.25"),
        status=OrderStatus.PARTIALLY_FILLED,
        filled_quantity=Decimal("50.25"),
        pods=("fundamental_ls",),
        as_of=DAY,
        created_at=datetime(2024, 3, 1, 13, 0, tzinfo=UTC),
    )
    violation = Violation(rule="wash_sale", severity=Severity.WARN, message="re-buy within 30d")
    return {
        "as_of": DAY,
        "regime": RegimeState(
            as_of=DAY,
            data_date=date(2024, 2, 29),
            vol_regime="NORMAL",
            trend_regime="UP",
            hmm_probs=(0.7, 0.2, 0.1),
            hmm_labels=("calm", "normal", "turbulent"),
            macro_quadrant="Goldilocks",
            stress_flag=False,
            risk_scalar=0.9,
            credit_source="oas",
        ),
        "macro_outlook": MacroOutlook(
            as_of=DAY,
            regime_summary="Late cycle",
            growth="slowing",
            inflation="sticky",
            policy="on hold",
            credit="tight",
            risks=("recession",),
            asset_views=(asset_view,),
            sector_tilts={"Energy": 0.5},
            risk_appetite=0.4,
            evidence=("fred:CPI",),
        ),
        "research_queue": ["AAA", "BBB"],
        "reports": {"AAA": {"news": report("AAA", "news")}},
        "theses": {
            "AAA": thesis("AAA").model_copy(
                update={
                    "catalysts": (Catalyst(description="earnings", expected_date=DAY),),
                    "status": ThesisStatus.ACTIVE,
                }
            )
        },
        "proposals": {
            "fundamental_ls": PodProposal(
                id=f"{IdPrefix.PROPOSAL}_{ULID.from_bytes(b'proposal-0000001')}",
                pod="fundamental_ls",
                as_of=DAY,
                views=(view,),
                target_weights={"AAA": 0.03},
                rationale="quality",
            )
        },
        "allocation": CapitalAllocation(
            as_of=DAY,
            pod_weights={"fundamental_ls": 0.3, "systematic": 0.7},
            risk_budgets={"fundamental_ls": 0.25, "systematic": 0.75},
            rationale="risk parity",
        ),
        "target": TargetPortfolio(
            as_of=DAY,
            weights={"AAA": 0.04},
            hedges={"SPY": -0.01},
            expected_vol=0.1,
            expected_beta=0.1,
        ),
        "risk_decision": RiskDecision(
            status=RiskStatus.RESIZE,
            adjusted_weights={"AAA": 0.03},
            breaches=(breach,),
            metrics={"gross": 1.9},
        ),
        "compliance": ComplianceResult(approved_orders=(order,), blocked=(), warnings=(violation,)),
        "orders": [order],
        "errors": [node_error("news", "BBB")],
    }


def full_research_state() -> dict[str, Any]:
    """A `ResearchState` with a value in every field."""
    reports = {"news": report("AAA", "news")}
    claim = Claim(text="margins", evidence_ids=("e1",), strength=0.5)
    return {
        "symbol": "AAA",
        "as_of": DAY,
        "analyst_reports": reports,
        "debate": [DebateTurn(speaker=DebateRole.BULL, claims=(claim,))],
        "thesis": thesis("AAA"),
        "reports": {"AAA": reports},
        "theses": {"AAA": thesis("AAA")},
        "errors": [node_error("quant", "AAA")],
    }
