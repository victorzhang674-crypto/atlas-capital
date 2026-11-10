"""`build_gateway`: the provider is chosen by config, the key comes from `Secrets`."""

import pytest
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, LLMConfig, Secrets
from atlas.core.errors import ConfigError
from atlas.llm import build_gateway
from atlas.llm.providers.anthropic import AnthropicProvider
from atlas.llm.providers.openai_compat import OpenAICompatibleProvider


def test_default_is_the_openai_compatible_provider_at_the_configured_url() -> None:
    config = AtlasConfig(secrets=Secrets(deepseek_api_key=SecretStr("sk-test-not-real")))
    provider = build_gateway(config).provider
    assert isinstance(provider, OpenAICompatibleProvider)
    assert repr(provider) == (
        "OpenAICompatibleProvider(url='https://api.deepseek.com/chat/completions')"
    )


def test_missing_deepseek_key_names_the_variable() -> None:
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        build_gateway(AtlasConfig())


def test_anthropic_provider_when_configured() -> None:
    config = AtlasConfig(
        llm=LLMConfig(provider="anthropic"),
        secrets=Secrets(anthropic_api_key=SecretStr("sk-ant-test-not-real")),
    )
    assert isinstance(build_gateway(config).provider, AnthropicProvider)


def test_missing_anthropic_key_names_the_variable() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_gateway(AtlasConfig(llm=LLMConfig(provider="anthropic")))


def test_anthropic_key_reaches_the_sdk_client() -> None:  # audit (mutation survivors)
    config = AtlasConfig(
        llm=LLMConfig(provider="anthropic"),
        secrets=Secrets(anthropic_api_key=SecretStr("sk-ant-test-not-real")),
    )
    provider = build_gateway(config).provider
    assert isinstance(provider, AnthropicProvider)
    assert provider._client.api_key == "sk-ant-test-not-real"
    assert repr(provider) == "AnthropicProvider()"
