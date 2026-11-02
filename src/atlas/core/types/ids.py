"""Ids for objects Atlas creates: `<prefix>_<ULID>`, for example `th_01JB3R5K8Y2T0Q9VZ6XW4M1N7C`.

The ULID part sorts by creation time. Ids that come from a source (evidence such as
`news:<article_id>`) are not minted here.
"""

import re
from enum import StrEnum
from typing import Annotated

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


def new_id(prefix: str) -> str:
    """A new id `<prefix>_<ULID>`; `prefix` is 2-5 lowercase letters (see `IdPrefix`).

    Raises `ValueError` for any other prefix.
    """
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"id prefix must be 2-5 lowercase letters, got {prefix!r}")
    return f"{prefix}_{ULID()}"


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
