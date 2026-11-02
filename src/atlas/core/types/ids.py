"""Ids for objects Atlas creates: `<prefix>_<ULID>`, for example `th_01JB3R5K8Y2T0Q9VZ6XW4M1N7C`.

The ULID part sorts by creation time. Ids that come from a source (evidence such as
`news:<article_id>`) are not minted here.

Every ULID Atlas mints (these ids and audit event ids) comes from the current `IdSource`, held
in a `ContextVar` like the clock. The default `RandomIds` is random and wall-clock stamped. A
backtest installs `SeededIds(seed, clock)` with `use_ids(...)`, so a re-run with the same seed
on the same simulated clock mints the same ids (spec principle 3, Stage 56). A new
`threading.Thread` starts with `RandomIds`; run its target via `contextvars.copy_context().run`
to carry the current source across.
"""

import random
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field
from ulid import ULID

_PREFIX_RE = re.compile(r"[a-z]{2,5}")
# Crockford base32; the first character carries the top 3 bits of a 48-bit time, so 0-7 only.
_ULID_PATTERN = "[0-7][0-9A-HJKMNP-TV-Z]{25}"


class IdPrefix(StrEnum):
    """Prefix of each kind of minted id."""

    THESIS = "th"
    PROPOSAL = "prop"
    ORDER = "ord"
    FILL = "fill"
    PREDICTION = "pred"
    LESSON = "les"
    LEDGER = "led"


class IdSource(Protocol):
    """Where new ULIDs come from (see the module docstring)."""

    def ulid(self) -> ULID:
        """A new ULID."""
        ...


class RandomIds:
    """The default source: 80 random bits and the wall-clock millisecond (python-ulid)."""

    def ulid(self) -> ULID:
        """A new random ULID stamped with the current wall-clock time."""
        return ULID()


class _HasNow(Protocol):
    def now(self) -> datetime: ...


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_RANDOM_BITS = 80


class SeededIds:
    """Reproducible ULIDs: the time is `clock.now()` (milliseconds) and the 80 random bits come
    from `random.Random(seed)`. Within one millisecond the random part counts up by one (the
    ULID monotonic rule), so ids sort in creation order as long as the clock never moves back
    (`SimClock` never does). Same seed + same clock instants → same ids. Thread-safe.
    """

    def __init__(self, seed: int, clock: _HasNow) -> None:
        """`seed` seeds the random part; `clock` is any object with an aware `now()`."""
        self._rng = random.Random(seed)
        self._clock = clock
        self._lock = threading.Lock()
        self._last: tuple[int, int] | None = None  # (millisecond, random part) of the last id

    def ulid(self) -> ULID:
        """The next ULID; `ValueError` if `clock.now()` is naive."""
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError(f"naive datetime {now!r} from the clock; ids need an aware time")
        millis = (now - _EPOCH) // timedelta(milliseconds=1)
        with self._lock:
            if self._last is not None and self._last[0] == millis:
                bits = self._last[1] + 1
                if bits >= 1 << _RANDOM_BITS:
                    raise ValueError(f"random part exhausted within millisecond {millis}")
            else:
                bits = self._rng.getrandbits(_RANDOM_BITS)
            self._last = (millis, bits)
        return ULID.from_bytes(millis.to_bytes(6, "big") + bits.to_bytes(10, "big"))


_DEFAULT_IDS = RandomIds()
_IDS: ContextVar[IdSource] = ContextVar("atlas_ids")


def get_ids() -> IdSource:
    """The current id source (`RandomIds` unless one is installed with `use_ids`)."""
    return _IDS.get(_DEFAULT_IDS)


@contextmanager
def use_ids(source: IdSource) -> Iterator[IdSource]:
    """Install `source` for the duration of the `with` block; the previous one is restored on
    exit, also on an exception."""
    token = _IDS.set(source)
    try:
        yield source
    finally:
        _IDS.reset(token)


def new_ulid() -> ULID:
    """A new ULID from the current id source."""
    return get_ids().ulid()


def new_id(prefix: str) -> str:
    """A new id `<prefix>_<ULID>` from the current id source; `prefix` is 2-5 lowercase
    letters (see `IdPrefix`).

    Raises `ValueError` for any other prefix.
    """
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"id prefix must be 2-5 lowercase letters, got {prefix!r}")
    return f"{prefix}_{new_ulid()}"


def id_pattern(prefix: str) -> str:
    """Anchored regex that matches exactly the ids `new_id(prefix)` produces."""
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"id prefix must be 2-5 lowercase letters, got {prefix!r}")
    return f"^{prefix}_{_ULID_PATTERN}$"


ThesisId = Annotated[str, Field(pattern=id_pattern(IdPrefix.THESIS))]
"""Id of an `InvestmentThesis` (`th_…`)."""
ProposalId = Annotated[str, Field(pattern=id_pattern(IdPrefix.PROPOSAL))]
"""Id of a `PodProposal` (`prop_…`)."""
OrderId = Annotated[str, Field(pattern=id_pattern(IdPrefix.ORDER))]
"""Id of an `Order` (`ord_…`)."""
FillId = Annotated[str, Field(pattern=id_pattern(IdPrefix.FILL))]
"""Id of a `Fill` (`fill_…`)."""
PredictionId = Annotated[str, Field(pattern=id_pattern(IdPrefix.PREDICTION))]
"""Id of a `Prediction` (`pred_…`)."""
LessonId = Annotated[str, Field(pattern=id_pattern(IdPrefix.LESSON))]
"""Id of a `Lesson` (`les_…`)."""
LedgerId = Annotated[str, Field(pattern=id_pattern(IdPrefix.LEDGER))]
"""Id of a `LedgerEntry` (`led_…`)."""
