"""Hard LLM budgets (spec A8): a per-call output token cap, a daily cap and a per-run cap.

A call is refused before it reaches the provider when its worst case would cross a cap.
Callers treat `BudgetExceeded` like any `LLMError`: fail safe, no new research.
"""

import math
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime

from atlas.core.config import LLMBudgets, ModelPrice
from atlas.core.errors import BudgetExceeded
from atlas.llm.cost import TOKENS_PER_PRICE_UNIT, CostLedger, rates_at
from atlas.llm.schemas import ProviderRequest


class BudgetGuard:
    """Enforces `budgets` against the spend in `ledger` plus the calls still in flight.

    `reserve` holds a call's estimated worst-case cost (USD) until the call has been
    recorded, so parallel calls cannot jointly overshoot a cap. Thread-safe.
    """

    def __init__(self, budgets: LLMBudgets, ledger: CostLedger) -> None:
        self._budgets = budgets
        self._ledger = ledger
        self._lock = threading.Lock()
        self._pending_day: defaultdict[date, float] = defaultdict(float)
        self._pending_run: defaultdict[str | None, float] = defaultdict(float)

    def check_max_tokens(self, max_tokens: int) -> None:
        """Raise `BudgetExceeded` if `max_tokens` is above `per_agent_call_max_tokens`."""
        cap = self._budgets.per_agent_call_max_tokens
        if max_tokens > cap:
            raise BudgetExceeded(
                f"max_tokens {max_tokens} exceeds llm.budgets.per_agent_call_max_tokens ({cap})",
                scope="per_agent_call_max_tokens",
                limit=cap,
                spent=max_tokens,
            )

    def estimate_usd(self, request: ProviderRequest, price: ModelPrice, ts: datetime) -> float:
        """Worst-case cost in USD of one response to `request` at the rates of instant `ts`:
        `max_tokens` output tokens plus the prompt, estimated as its characters divided by
        `estimate_chars_per_token` (rounded up) and charged at the uncached input rate."""
        rates = rates_at(price, ts)
        chars = sum(len(m.content) for m in request.messages)
        prompt_tokens = math.ceil(chars / self._budgets.estimate_chars_per_token)
        return (
            prompt_tokens * rates.input + request.max_tokens * rates.output
        ) / TOKENS_PER_PRICE_UNIT

    @contextmanager
    def reserve(self, estimate_usd: float, *, day: date, run_id: str | None) -> Iterator[None]:
        """Hold `estimate_usd` against the budgets of UTC day `day` and run `run_id` for the
        duration of the block.

        Raises `BudgetExceeded` (scope `daily_usd` or `per_run_usd`) without holding anything
        when spend + reservations in flight + this estimate would exceed a cap. Record the
        call's real cost in the ledger before leaving the block.
        """
        with self._lock:
            day_spent, run_spent = self._ledger.spent_on(day), self._ledger.spent_in_run(run_id)
            day_cap, run_cap = self._budgets.daily_usd, self._budgets.per_run_usd
            self._check("daily_usd", day_cap, day_spent, self._pending_day[day], estimate_usd)
            self._check("per_run_usd", run_cap, run_spent, self._pending_run[run_id], estimate_usd)
            self._pending_day[day] += estimate_usd
            self._pending_run[run_id] += estimate_usd
        try:
            yield
        finally:
            with self._lock:
                self._release(self._pending_day, day, estimate_usd)
                self._release(self._pending_run, run_id, estimate_usd)

    @staticmethod
    def _check(scope: str, limit: float, spent: float, pending: float, estimate: float) -> None:
        if spent + pending + estimate > limit:
            raise BudgetExceeded(
                f"LLM budget llm.budgets.{scope} ({limit:.4f} USD) would be exceeded: "
                f"{spent:.4f} USD spent, {pending:.4f} USD in flight, "
                f"this call may cost up to {estimate:.4f} USD",
                scope=scope,
                limit=limit,
                spent=spent,
            )

    @staticmethod
    def _release[K](pending: defaultdict[K, float], key: K, amount: float) -> None:
        pending[key] -= amount
        if pending[key] <= 1e-12:
            del pending[key]
