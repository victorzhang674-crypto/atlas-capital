"""Phase 3 checkpoint (Stage 24/25 follow-up): a replay needs no API key.

In cache mode `replay_only` no provider call can happen, so `build_gateway` builds the gateway
without the vendor's key: an `OfflineProvider` stands in, with the configured provider's name
and endpoint so the cache keys of earlier recordings match.
"""

import pytest
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, LLMCacheConfig, LLMConfig, Secrets
from atlas.core.errors import CacheMiss, ConfigError, LLMError
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.llm import FakeLLM, LLMGateway, Message, OfflineProvider, build_gateway, build_provider
from atlas.llm.cache import cache_key
from atlas.llm.providers.anthropic import AnthropicProvider
from atlas.llm.providers.openai_compat import OpenAICompatibleProvider
from atlas.llm.schemas import ProviderRequest

ASK = [Message.user("Rate AAPL.")]
META = {"agent": "analyst", "step": "rate"}


def config(mode: str, *, provider: str = "openai_compatible", key: bool = False) -> AtlasConfig:
    secrets = Secrets(
        deepseek_api_key=SecretStr("sk-test-not-real") if key else None,
        anthropic_api_key=SecretStr("sk-ant-test-not-real") if key else None,
    )
    llm = LLMConfig(provider=provider, cache=LLMCacheConfig(mode=mode))  # type: ignore[arg-type]
    return AtlasConfig(llm=llm, secrets=secrets)


def test_replay_only_builds_without_an_api_key() -> None:
    gateway = build_gateway(config("replay_only"))
    provider = gateway.provider
    assert gateway.cache_mode == "replay_only"
    assert isinstance(provider, OfflineProvider)
    assert provider.name == "openai_compatible"
    assert provider.endpoint == "https://api.deepseek.com"
    assert repr(provider) == (
        "OfflineProvider(name='openai_compatible', endpoint='https://api.deepseek.com')"
    )


def test_the_offline_provider_has_the_real_providers_cache_identity() -> None:
    real = OpenAICompatibleProvider("https://api.deepseek.com/", SecretStr("sk-test-not-real"))
    offline = build_provider(config("replay_only"))
    request = ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=tuple(ASK),
        max_tokens=100,
        timeout_seconds=30.0,
        metadata=FrozenDict(META),
    )
    assert (offline.name, offline.endpoint) == (real.name, real.endpoint)  # type: ignore[attr-defined]
    assert cache_key(request, provider=offline.name, endpoint=offline.endpoint) == cache_key(  # type: ignore[attr-defined]
        request, provider=real.name, endpoint=real.endpoint
    )


def test_anthropic_replay_only_builds_without_a_key_too() -> None:
    provider = build_gateway(config("replay_only", provider="anthropic")).provider
    assert isinstance(provider, OfflineProvider)
    assert (provider.name, provider.endpoint) == ("anthropic", "")


@pytest.mark.parametrize("mode", ["read_write", "record", "off"])
def test_every_other_mode_still_needs_the_key(mode: str) -> None:
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        build_gateway(config(mode))
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_gateway(config(mode, provider="anthropic"))


def test_replay_only_with_a_key_keeps_the_real_provider() -> None:
    assert isinstance(
        build_gateway(config("replay_only", key=True)).provider, OpenAICompatibleProvider
    )
    anthropic = build_gateway(config("replay_only", provider="anthropic", key=True)).provider
    assert isinstance(anthropic, AnthropicProvider)


def test_a_keyless_replay_reads_what_was_recorded_on_the_lake(tmp_path) -> None:  # type: ignore[no-untyped-def]
    lake = Lake(tmp_path / "lake")
    keyless = build_gateway(config("replay_only"), lake=lake)
    assert isinstance(keyless.provider, OfflineProvider)
    # A recording session on the same lake, with the same provider identity (the vendor is
    # played by a FakeLLM that carries the real provider's name and endpoint).
    vendor = FakeLLM({"analyst": "buy"})
    vendor.name = keyless.provider.name  # type: ignore[misc]
    vendor.endpoint = keyless.provider.endpoint  # type: ignore[attr-defined]
    recorder = LLMGateway(vendor, config("read_write"), cache=keyless.cache)
    recorded = recorder.complete(ASK, metadata=META)
    replayed = keyless.complete(ASK, metadata=META)
    assert replayed.content == recorded.content == "buy"
    assert replayed.cache_hit and replayed.cost_usd == 0.0 and len(vendor.calls) == 1
    with pytest.raises(CacheMiss, match="replay_only"):
        keyless.complete([Message.user("Rate MSFT.")], metadata=META)


def test_the_offline_provider_refuses_to_make_a_call() -> None:
    provider = OfflineProvider("openai_compatible", "https://api.deepseek.com/")
    assert provider.endpoint == "https://api.deepseek.com"
    gateway = LLMGateway(provider, AtlasConfig())  # no cache: every call goes to the provider
    with pytest.raises(LLMError, match=r"offline.*only recorded answers"):
        gateway.complete(ASK, metadata=META)
    assert not isinstance(provider, FakeLLM)
    with pytest.raises(ValueError, match="name"):
        OfflineProvider("")
