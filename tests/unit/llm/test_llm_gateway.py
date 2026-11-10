"""The gateway contract (brief task 1), exercised with FakeLLM only."""

from collections.abc import Callable

import pytest

from atlas.core.config import AtlasConfig, LLMConfig
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.hygiene import DATA_POLICY
from atlas.llm.schemas import LLMResult, Message, ToolSpec, Usage

MakeGateway = Callable[..., LLMGateway]


def test_result_fields(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default="x" * 40)  # 10 output tokens
    gateway = make_gateway(fake)
    result = gateway.complete([Message.user("y" * 30)])
    assert isinstance(result, LLMResult)
    assert result.content == "x" * 40
    assert result.parsed is None
    assert result.model == "deepseek-flash"  # default tier is quick
    assert result.cache_hit is False
    assert result.attempts == 1
    assert result.tool_calls == ()
    assert result.finish_reason == "stop"
    # FakeLLM counts 4 characters per token over the messages it receives (policy included).
    sent = fake.calls[0].messages
    prompt_tokens = -(-sum(len(m.content) for m in sent) // 4)
    assert result.usage == Usage(input_tokens=prompt_tokens, output_tokens=10)
    # Off-peak deepseek-flash: 0.15 USD per 1M input tokens, 0.60 per 1M output tokens.
    assert result.cost_usd == pytest.approx((prompt_tokens * 0.15 + 10 * 0.6) / 1e6, rel=1e-12)
    assert result.latency_ms == pytest.approx(250.0)  # two timer readings 0.25 s apart


@pytest.mark.parametrize(
    ("tier", "model"), [("quick", "deepseek-flash"), ("deep", "deepseek-v4-pro")]
)
def test_tier_picks_the_model(make_gateway: MakeGateway, tier: str, model: str) -> None:
    fake = FakeLLM(default="ok")
    result = make_gateway(fake).complete([Message.user("hi")], tier=tier)
    assert result.model == model
    assert (fake.calls[0].model, fake.calls[0].tier) == (model, tier)


def test_anthropic_provider_config_uses_the_claude_tiers(make_gateway: MakeGateway) -> None:
    config = AtlasConfig(llm=LLMConfig(provider="anthropic"))
    fake = FakeLLM(default="ok")
    assert make_gateway(fake, config).complete([Message.user("hi")], tier="deep").model == (
        "claude-opus-5-5"
    )


def test_request_carries_the_call_settings(make_gateway: MakeGateway, cfg: AtlasConfig) -> None:
    fake = FakeLLM(default="ok")
    tool = ToolSpec(name="get_news", description="News for a symbol", parameters={"type": "object"})
    make_gateway(fake).complete(
        [Message.system("You are an analyst."), Message.user("hi")],
        tier="deep",
        tools=[tool],
        temperature=0.3,
        max_tokens=500,
        metadata={"agent": "analyst", "step": "draft", "symbol": "AAA"},
    )
    sent = fake.calls[0]
    assert sent.tools == (tool,)
    assert sent.temperature == 0.3
    assert sent.max_tokens == 500
    assert sent.schema is None
    assert sent.thinking == "enabled"  # llm.thinking.deep
    assert sent.timeout_seconds == cfg.llm.request_timeout_seconds
    assert dict(sent.metadata) == {"agent": "analyst", "step": "draft", "symbol": "AAA"}


def test_defaults_of_a_call(make_gateway: MakeGateway, cfg: AtlasConfig) -> None:
    fake = FakeLLM(default="ok")
    make_gateway(fake).complete([Message.user("hi")])
    sent = fake.calls[0]
    assert sent.temperature == 0.0
    assert sent.max_tokens == cfg.llm.budgets.per_agent_call_max_tokens
    assert sent.tools == ()
    assert sent.thinking == "disabled"  # llm.thinking.quick
    assert dict(sent.metadata) == {}


def test_every_call_carries_the_data_policy_once(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default="ok")
    gateway = make_gateway(fake)
    gateway.complete([Message.system("You are an analyst."), Message.user("hi")])
    gateway.complete([Message.user("hi")])
    for sent in fake.calls:
        assert sent.messages[0].role == "system"
        assert sent.messages[0].content.count(DATA_POLICY) == 1
        assert sum(DATA_POLICY in m.content for m in sent.messages) == 1


def test_fake_is_keyed_through_the_gateway(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({("analyst", "draft"): "draft", ("pm", "size"): "size"})
    gateway = make_gateway(fake)
    assert gateway.complete(
        [Message.user("x")], metadata={"agent": "pm", "step": "size"}
    ).content == ("size")
    assert (
        gateway.complete(
            [Message.user("x")], metadata={"agent": "analyst", "step": "draft"}
        ).content
        == "draft"
    )


@pytest.mark.parametrize(
    ("kwargs", "pattern"),
    [
        ({"tier": "medium"}, "tier"),
        ({"temperature": -0.1}, "temperature"),
        ({"temperature": 2.5}, "temperature"),
        ({"max_tokens": 0}, "max_tokens"),
    ],
)
def test_bad_arguments_are_rejected(
    make_gateway: MakeGateway, kwargs: dict[str, object], pattern: str
) -> None:
    fake = FakeLLM(default="ok")
    with pytest.raises(ValueError, match=pattern):
        make_gateway(fake).complete([Message.user("hi")], **kwargs)  # type: ignore[arg-type]
    assert fake.calls == []


def test_empty_messages_are_rejected(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default="ok")
    with pytest.raises(ValueError, match="messages"):
        make_gateway(fake).complete([])
    assert fake.calls == []


def test_fake_llm_fixture_is_shared(fake_llm: FakeLLM) -> None:  # stage protocol §5: `fake_llm`
    fake_llm.add("analyst", None, "ok")
    gateway = LLMGateway(fake_llm, AtlasConfig())  # no lake, no clock: usable in any agent test
    assert gateway.complete([Message.user("hi")], metadata={"agent": "analyst"}).content == "ok"


def test_public_names_are_exported() -> None:
    import atlas.llm as llm

    for name in llm.__all__:
        assert getattr(llm, name) is not None
    assert {"LLMGateway", "FakeLLM", "Message", "CostLedger", "build_gateway", "data_block"} <= set(
        llm.__all__
    )
    assert llm.__all__ == sorted(llm.__all__, key=lambda n: (not n.isupper(), n))


# --- Audit (mutation survivors): argument boundaries ---------------------------------------


def test_boundary_arguments_are_accepted(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default="ok")
    gateway = make_gateway(fake)
    gateway.complete([Message.user("hi")], temperature=2.0)  # the upper bound is allowed
    gateway.complete([Message.user("hi")], temperature=0.0)
    gateway.complete([Message.user("hi")], max_tokens=1)  # the smallest cap is allowed
    assert [c.temperature for c in fake.calls] == [2.0, 0.0, 0.0]
    assert fake.calls[2].max_tokens == 1
    with pytest.raises(ValueError, match="temperature"):
        gateway.complete([Message.user("hi")], temperature=float("nan"))
