"""A synthetic memory corpus for the Stage 27 tests: episodes on a few clear topics, written
over March 2024 by two agents, some settled later."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from atlas.agents.memory.embedder import Embedder, HashEmbedder
from atlas.agents.memory.retrieval import AgentMemory
from atlas.core.calendar import MARKET_TZ
from atlas.core.config import AgentsConfig, AtlasConfig, MemoryConfig
from atlas.core.pit import AsOf

AGENT = "fundamental_analyst"
OTHER = "news_analyst"


def at(day: date, hour: int = 12) -> datetime:
    """`hour`:00 New York time on `day`, as an aware datetime (UTC)."""
    return datetime.combine(day, time(hour), tzinfo=MARKET_TZ).astimezone(UTC)


def as_of(day: date) -> AsOf:
    """The default decision moment of `day` (08:00 New York)."""
    return AsOf.for_session(day)


@dataclass(frozen=True)
class Seed:
    """One synthetic episode: topic, text, symbol, creation day and settle day (if any)."""

    topic: str
    text: str
    symbol: str
    day: date
    settle: date | None = None
    agent: str = AGENT


CORPUS: tuple[Seed, ...] = (
    Seed(
        "earnings",
        "AAA beat quarterly earnings estimates and raised full year guidance",
        "AAA",
        date(2024, 3, 4),
        settle=date(2024, 3, 11),
    ),
    Seed(
        "earnings",
        "BBB earnings beat on revenue growth, guidance raised again",
        "BBB",
        date(2024, 3, 5),
        settle=date(2024, 3, 25),
    ),
    Seed(
        "earnings",
        "CCC missed earnings estimates and cut guidance on weak demand",
        "CCC",
        date(2024, 3, 6),
    ),
    Seed(
        "rates",
        "The central bank raised interest rates and signalled more hikes",
        "SPY",
        date(2024, 3, 7),
        settle=date(2024, 3, 14),
    ),
    Seed(
        "rates",
        "Treasury yields jumped after hot inflation data; rate hike odds rose",
        "SPY",
        date(2024, 3, 8),
    ),
    Seed(
        "supply",
        "Supply chain disruption: port strike delays shipments of chips",
        "DDD",
        date(2024, 3, 11),
        settle=date(2024, 3, 18),
    ),
    Seed(
        "supply",
        "Factory shutdown in Asia hits supply of components; inventory falls",
        "EEE",
        date(2024, 3, 12),
    ),
    Seed(
        "legal",
        "Regulator opens antitrust lawsuit against the company over app store fees",
        "FFF",
        date(2024, 3, 13),
    ),
    Seed(
        "deal",
        "Acquisition announced: buyer pays a 30 percent premium in cash",
        "GGG",
        date(2024, 3, 14),
        settle=date(2024, 3, 21),
    ),
    Seed(
        "earnings",
        "AAA earnings beat estimates; management raised margin guidance",
        "AAA",
        date(2024, 3, 20),
        settle=date(2024, 3, 27),
    ),
    Seed(
        "earnings",
        "News desk: HHH beat earnings and raised guidance",
        "HHH",
        date(2024, 3, 6),
        agent=OTHER,
    ),
)


HASH_CONFIG = AtlasConfig(
    agents=AgentsConfig(memory=MemoryConfig(embedder="hash", min_similarity=0.1))
)
"""The config for `HashEmbedder` corpora: word-overlap similarities run lower than MiniLM's,
for which the default `min_similarity` (0.3) is set."""


def build_memory(
    embedder: Embedder | None = None, config: AtlasConfig | None = None
) -> tuple[AgentMemory, dict[str, Seed]]:
    """An in-memory `AgentMemory` (default: `HashEmbedder`, `HASH_CONFIG`) holding `CORPUS`
    (episodes created at 12:00 New York on their day, settled at 17:00 on their settle day);
    returns it and the seed of each id."""
    memory = AgentMemory(embedder or HashEmbedder(), config=config or HASH_CONFIG)
    seeds: dict[str, Seed] = {}
    for seed in CORPUS:
        ep = memory.record_episode(
            seed.agent,
            seed.text,
            {"action": "buy" if "beat" in seed.text else "hold", "topic": seed.topic},
            created_at=at(seed.day),
            symbol=seed.symbol,
        )
        seeds[ep.id] = seed
        if seed.settle is not None:
            memory.settle_episode(
                ep.id, {"return_5d": 0.01, "topic": seed.topic}, settled_at=at(seed.settle, 17)
            )
    return memory, seeds


def days(n: int) -> timedelta:
    return timedelta(days=n)
