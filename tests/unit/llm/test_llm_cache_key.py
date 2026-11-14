"""The cache key (Stage 25 D1): what changes it, and what must not."""

import re
from dataclasses import replace

import pytest
from pydantic import BaseModel, Field

from atlas.core.types.base import FrozenDict
from atlas.llm import Message, ProviderRequest, ToolSpec
from atlas.llm.cache import cache_key, key_payload, schema_fingerprint


class Verdict(BaseModel):
    action: str
    confidence: float


class VerdictV2(BaseModel):
    action: str
    confidence: float
    rationale: str = ""


class Described(BaseModel):
    action: str = Field(description="buy, sell or hold")
    confidence: float


def make(**overrides: object) -> ProviderRequest:
    base = ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=(Message.system("You are an analyst."), Message.user("Rate AAPL.")),
        max_tokens=800,
        timeout_seconds=60.0,
        schema=Verdict,
        tools=(
            ToolSpec(
                name="lookup",
                description="Look up a fact.",
                parameters=FrozenDict({"type": "object", "properties": {"a": {}, "b": {}}}),
            ),
        ),
        temperature=0.0,
        thinking="disabled",
        metadata=FrozenDict({"agent": "analyst", "step": "rate", "prompt_version": "v1"}),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def key(request: ProviderRequest | None = None, provider: str = "fake") -> str:
    return cache_key(request or make(), provider=provider)


def test_key_is_a_sha256_hex_digest_and_is_stable() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", key())
    assert key() == key(make())


def test_the_key_is_pinned() -> None:
    # Changing how keys are built silently voids every recorded backtest: bump deliberately
    # (KEY_VERSION 2 since the Stage 25 audit: the token cap and a float temperature).
    assert key() == ("811c14fce624a02ce17e2d46e9cb77b1c7036c75a355364ce1dd8a38046910d1")


def test_dict_order_is_irrelevant() -> None:
    reordered = ToolSpec(
        name="lookup",
        description="Look up a fact.",
        parameters=FrozenDict({"properties": {"b": {}, "a": {}}, "type": "object"}),
    )
    assert key(make(tools=(reordered,))) == key()


def test_a_prompt_template_version_bump_changes_the_key() -> None:
    bumped = make(metadata=FrozenDict({"agent": "analyst", "step": "rate", "prompt_version": "v2"}))
    assert key(bumped) != key()


def test_a_missing_prompt_version_differs_from_a_set_one() -> None:
    assert key(make(metadata=FrozenDict({}))) != key()


@pytest.mark.parametrize(
    "change",
    [
        {"model": "deepseek-v4-pro"},
        {"messages": (Message.system("You are an analyst."), Message.user("Rate MSFT."))},
        {"messages": (Message.user("Rate AAPL."),)},
        {"temperature": 0.7},
        {"thinking": "enabled"},
        {"thinking": "omit"},
        {"schema": None},
        {"schema": VerdictV2},
        {"tools": ()},
    ],
    ids=lambda c: next(iter(c)) + ":" + str(next(iter(c.values())))[:30],
)
def test_everything_the_model_sees_changes_the_key(change: dict[str, object]) -> None:
    assert key(make(**change)) != key()


def test_the_provider_changes_the_key() -> None:
    assert key(provider="openai_compatible") != key(provider="fake")


def test_a_tool_description_and_parameters_change_the_key() -> None:
    tool = make().tools[0]
    assert key(make(tools=(replace_tool(tool, description="Other."),))) != key()
    assert (
        key(make(tools=(replace_tool(tool, parameters=FrozenDict({"type": "object"})),))) != key()
    )


def replace_tool(tool: ToolSpec, **fields: object) -> ToolSpec:
    return tool.model_copy(update=fields)


def test_tool_order_changes_the_key() -> None:
    a = ToolSpec(name="a", description="A.", parameters=FrozenDict({}))
    b = ToolSpec(name="b", description="B.", parameters=FrozenDict({}))
    assert key(make(tools=(a, b))) != key(make(tools=(b, a)))


def test_labels_and_the_timeout_do_not_change_the_key() -> None:
    # agent / step / run labels and the timeout do not change what the model is asked.
    other = make(
        metadata=FrozenDict({"agent": "pm", "step": "other", "prompt_version": "v1", "x": "y"}),
        timeout_seconds=5.0,
        tier="deep",
    )
    assert key(other) == key()


def test_the_token_cap_changes_the_key() -> None:  # audit A2
    # The cap is sent to the provider: an answer cut at 100 tokens is not the answer at 800.
    assert key(make(max_tokens=100)) != key()


def test_an_int_temperature_is_the_same_value_as_its_float() -> None:  # audit A3
    assert key(make(temperature=1)) == key(make(temperature=1.0))
    assert key_payload(make(temperature=1), provider="fake")["temperature"] == 1.0


def test_tool_call_turns_are_part_of_the_key() -> None:
    from atlas.llm import ToolCall

    call = ToolCall(id="c1", name="lookup", arguments=FrozenDict({"a": 1}))
    base = (Message.user("go"), Message.assistant("", tool_calls=(call,)), Message.tool("c1", "42"))
    changed = (
        Message.user("go"),
        Message.assistant("", tool_calls=(call,)),
        Message.tool("c1", "43"),
    )
    assert key(make(messages=base)) != key(make(messages=changed))


def test_schema_fingerprint_follows_the_json_schema_not_the_name() -> None:
    assert schema_fingerprint(Verdict) == schema_fingerprint(Verdict)
    assert schema_fingerprint(Verdict) != schema_fingerprint(VerdictV2)
    assert schema_fingerprint(Verdict) != schema_fingerprint(Described)  # a field description


def test_payload_names_the_schema_and_carries_the_inputs() -> None:
    payload = key_payload(make(), provider="fake")
    assert payload["schema"] == {"name": "Verdict", "fingerprint": schema_fingerprint(Verdict)}
    assert payload["provider"] == "fake" and payload["model"] == "deepseek-flash"
    assert payload["prompt_version"] == "v1" and payload["temperature"] == 0.0
    assert key_payload(make(schema=None), provider="fake")["schema"] is None


def test_the_key_without_a_prompt_version_is_pinned() -> None:  # audit (mutation survivors)
    # An absent prompt_version hashes as "" (not null): recordings made before Stage 26's
    # prompt registry must keep their keys.
    assert key(make(metadata=FrozenDict({}))) == (
        "268f04f669a8be8ac9b8fd41420a2dd8f05be09514104b9a1587e21dd82518c9"
    )
    assert key_payload(make(metadata=FrozenDict({})), provider="fake")["prompt_version"] == ""


def test_request_json_is_the_preimage_of_the_key() -> None:  # audit (mutation survivors)
    import hashlib
    import json

    from atlas.llm.cache import request_json

    text = request_json(make(), provider="openai_compatible")
    assert hashlib.sha256(text.encode()).hexdigest() == key(provider="openai_compatible")
    assert json.loads(text)["provider"] == "openai_compatible"
