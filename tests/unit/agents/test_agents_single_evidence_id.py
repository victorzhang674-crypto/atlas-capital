"""Stage 31: a nested model with a string field `evidence_id` cites that one id.

The brief's `catalysts[{date?, description, evidence_id, expected_impact}]` names a single
`evidence_id`. The citation check treats it as a citation: an id the run never showed the model
is unknown (repair turn, then `low_quality`), exactly as for `evidence_ids`.
"""

from pydantic import BaseModel, ConfigDict

from atlas.agents.evidence import can_cite, check_citations


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_id: str


class Holder(BaseModel):
    items: tuple[Item, ...]
    note: str = ""


def test_a_single_evidence_id_is_a_citation() -> None:
    report = check_citations(Holder(items=(Item(text="a", evidence_id="news:1"),)), {"news:1"})
    assert report.cited == ("news:1",) and report.ok


def test_an_unknown_single_id_is_unknown() -> None:
    holder = Holder(items=(Item(text="a", evidence_id="news:1"), Item(text="b", evidence_id="x:9")))
    report = check_citations(holder, {"news:1"})
    assert report.cited == ("news:1", "x:9") and report.unknown == ("x:9",) and not report.ok


def test_single_ids_join_tags_in_first_seen_order() -> None:
    holder = Holder(items=(Item(text="a", evidence_id="news:2"),), note="see [E:news:1]")
    assert check_citations(holder, {"news:1", "news:2"}).cited == ("news:2", "news:1")


def test_an_empty_single_id_is_an_uncited_claim() -> None:
    holder = Holder(items=(Item(text="a", evidence_id=""),))
    report = check_citations(holder, {"news:1"})
    # the empty id is an uncited claim, and the output then cites nothing at all
    assert report.uncited_claims == ("<root>", "items[0]") and report.cited == ()


def test_a_schema_with_a_single_id_can_cite() -> None:
    assert can_cite(Holder) is True
    holder = Holder(items=())
    report = check_citations(holder, {"news:1"})
    assert report.uncited_claims == ("<root>",)  # a citing schema that cites nothing at all


def test_a_non_string_field_of_that_name_is_not_a_citation() -> None:
    class Odd(BaseModel):
        evidence_id: int

    assert check_citations(Odd(evidence_id=3), {"3"}).cited == ()
