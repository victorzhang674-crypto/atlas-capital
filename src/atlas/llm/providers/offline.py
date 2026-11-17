"""`OfflineProvider`: the configured provider's identity with no way to call it.

A replay needs no model vendor: in cache mode `replay_only` the gateway answers every request
from recordings and a miss raises `CacheMiss` before any provider is asked. The cache key
still names the provider and its endpoint, so a replay without an API key (CI, a recorded
test fixture, `--replay` on a machine with no `.env`) stands this provider in for the real
one: same `name` and `endpoint`, and a `complete` that refuses.
"""

from atlas.core.errors import LLMError
from atlas.llm.schemas import ProviderRequest, ProviderResponse


class OfflineProvider:
    """A provider that can only be replayed: it has the `name` and `endpoint` of the provider
    whose recordings it reads (so cache keys match) and raises `LLMError` when asked to
    make a call."""

    def __init__(self, name: str, endpoint: str = "") -> None:
        if not name:
            raise ValueError("an offline provider needs the name of the provider it stands for")
        self._name = name
        self._endpoint = endpoint.rstrip("/")

    @property
    def name(self) -> str:
        """The name of the provider this one stands for (`openai_compatible`, ...)."""
        return self._name

    @property
    def endpoint(self) -> str:
        """The base URL of the provider this one stands for ("" when it has none)."""
        return self._endpoint

    def __repr__(self) -> str:
        return f"OfflineProvider(name={self._name!r}, endpoint={self._endpoint!r})"

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Always raises `LLMError`: an offline provider never calls a model."""
        raise LLMError(
            f"no call can be made to {request.model}: the gateway was built offline for "
            f"replay ({self._name}, no API key); only recorded answers are available"
        )
