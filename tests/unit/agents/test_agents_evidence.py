"""Stage 26 Task 4: evidence tags, the evidence ledger and the citation check."""

from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel

from atlas.agents.evidence import (
    CitationReport,
    EvidenceLedger,
    cap_confidence,
    check_citations,
    evidence_line,
    evidence_tag,
)
from atlas.core.types.research import (
    AnalystReport,
    Claim,
    DebateRole,
    DebateTurn,
    Direction,
    Evidence,
    Signal,
)

T = datetime(2024, 2, 28, 15, tzinfo=UTC)
KNOWN = {"news:1", "news:2", "fund:AAA:2023-12-31:revenue_ttm"}


def report(
    *signals: Signal, summary: str = "AAA looks fine.", confidence: float = 0.8
) -> AnalystReport:
    return AnalystReport(
        ticker="AAA",
        as_of=date(2024, 3, 1),
        analyst="echo",
        summary=summary,
        signals=signals,
        risks=("none",),
        confidence=confidence,
    )


def signal(*ids: str, name: str = "news_tone") -> Signal:
    return Signal(name=name, direction=Direction.LONG, strength=0.5, evidence_ids=ids)


def test_tags_and_lines() -> None:
    assert evidence_tag("news:1") == "[E:news:1]"
    for bad in ("", "has space", "a]b", "x" * 201):
        with pytest.raises(ValueError, match="evidence id"):
            evidence_tag(bad)
    item = Evidence(id="news:1", source="alpaca", as_of=T, text="AAA  beats\nestimates ")
    assert evidence_line(item) == "[E:news:1] 2024-02-28 15:00Z AAA beats estimates"


def test_valid_output() -> None:
    result = check_citations(report(signal("news:1"), signal("news:2", "news:1")), KNOWN)
    assert result == CitationReport(cited=("news:1", "news:2"), unknown=(), uncited_claims=())
    assert result.ok


def test_invalid_unknown_id_in_evidence_ids() -> None:
    result = check_citations(report(signal("news:1"), signal("news:99")), KNOWN)
    assert not result.ok
    assert result.unknown == ("news:99",)
    assert result.cited == ("news:1", "news:99")


def test_invalid_unknown_inline_tag() -> None:
    result = check_citations(
        report(
            signal("news:1"),
            summary="Revenue grew [E:fund:AAA:2023-12-31:revenue_ttm] and [E:news:7].",
        ),
        KNOWN,
    )
    assert result.unknown == ("news:7",)
    assert "fund:AAA:2023-12-31:revenue_ttm" in result.cited


def test_missing_citation_is_an_uncited_claim() -> None:
    result = check_citations(report(signal("news:1"), signal(name="valuation")), KNOWN)
    assert not result.ok
    assert result.uncited_claims == ("signals[1]",)
    assert result.unknown == ()


def test_an_evidence_list_counts_as_citations() -> None:
    class Outlook(BaseModel):
        summary: str
        evidence: tuple[str, ...]

    assert check_citations(Outlook(summary="ok", evidence=("news:1",)), KNOWN).ok
    bad = check_citations(Outlook(summary="ok", evidence=("news:1", "macro:x")), KNOWN)
    assert bad.unknown == ("macro:x",)


def test_nested_claims_have_paths() -> None:
    turn = DebateTurn(
        speaker=DebateRole.BULL,
        claims=(
            Claim(text="Strong news [E:news:2]", evidence_ids=(), strength=0.6),
            Claim(text="Growth", evidence_ids=("news:1",), strength=0.5),
        ),
    )
    result = check_citations(turn, KNOWN)
    # A claim whose only citation is an inline tag still has an empty `evidence_ids`.
    assert result.uncited_claims == ("claims[0]",)
    assert result.cited == ("news:2", "news:1")


def test_outputs_without_claims_or_citations() -> None:
    class Plain(BaseModel):
        text: str
        score: float

    assert check_citations(Plain(text="hello", score=1.0), KNOWN).ok
    assert check_citations(Plain(text="hello", score=1.0), set()).ok


def test_describe_lists_the_problems() -> None:
    result = CitationReport(cited=("a",), unknown=("news:9",), uncited_claims=("signals[0]",))
    text = result.describe()
    assert "news:9" in text and "signals[0]" in text
    assert CitationReport(cited=(), unknown=(), uncited_claims=()).describe() == "ok"


def test_cap_confidence() -> None:
    high = report(signal("news:1"), confidence=0.8)
    capped = cap_confidence(high, 0.3)
    assert capped.confidence == pytest.approx(0.3) and isinstance(capped, AnalystReport)
    low = report(signal("news:1"), confidence=0.1)
    assert cap_confidence(low, 0.3) is low

    class NoConfidence(BaseModel):
        text: str

    plain = NoConfidence(text="x")
    assert cap_confidence(plain, 0.3) is plain


def test_ledger_keeps_first_seen_order_and_rejects_conflicts() -> None:
    ledger = EvidenceLedger()
    a = Evidence(id="news:1", source="alpaca", as_of=T, text="A")
    b = Evidence(id="news:2", source="alpaca", as_of=T, text="B")
    ledger.add([a, b, a])
    assert ledger.ids == ("news:1", "news:2") and "news:1" in ledger and len(ledger) == 2
    assert ledger.get("news:2") == b and ledger.get("zzz") is None
    assert ledger.items == (a, b)
    with pytest.raises(ValueError, match="news:1"):
        ledger.add([Evidence(id="news:1", source="alpaca", as_of=T, text="changed")])
    assert ledger.select(["news:2", "nope", "news:1"]) == (b, a)
