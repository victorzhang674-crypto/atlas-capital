"""The news analyst's tools (Stage 31): `get_company_news`, `get_upcoming_events`,
`get_past_earnings_reactions` and `get_sector_news` (`NEWS_TOOLS`), and `prefetch`. The analyst
interprets what these return; it never looks anything up elsewhere: every article, event and
reaction comes through the context's `DataView` (point in time).

Evidence, one item per article, event or reaction, with a stable id from the source:

- `news:<article_id>`: an article about the stock (`get_company_news`) or about its sector
  peers (`get_sector_news`), as `DataView.news` returns it: copies and roundups dropped, the
  vendor's id (so the same article has the same id in both lists), `as_of` its publish time;
- `ncov:<SYM>:<day>:<company | sector>`: the coverage of a news read: how many articles there
  were in the window, the newest one's date, which peers were read. It makes "no article" a
  fact the report can cite;
- `evt:<SYM>:<event_type>:<date>`: one upcoming event of the stock (earnings with before-open
  or after-close, ex-dividend), scheduled or only estimated; `evt:<SYM>:<day>:none` says there
  is none in the horizon. Market-wide events (macro releases) are not listed: they are the
  macro economist's;
- `rx:<SYM>:<event day>`: one past earnings reaction, in percent (gap, open-to-close, abnormal
  open-to-close and the abnormal return over the reaction window against the benchmark);
  `rxs:<SYM>:<day>:summary`: their count, mean absolute abnormal return, how many were
  positive and the extremes, computed here and not by the model;
- `gap:<SYM>:<day>:<tool>`: what `prefetch` could not gather, and why.

Coverage, `none`, summary and gap items are timed one microsecond before the decision cutoff
(computed at the decision moment from data known then, Stage 29's convention); articles carry
their publish time, events and reactions the time their row became known. Windows and counts:
`agents.news` (`config/fund.yaml`). The sector's peers are the members of the factor universe
in the stock's GICS sector, the `sector_peers` with the highest average dollar volume first;
articles already in the company list are left out of the sector list.
"""

import math
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Annotated

import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from atlas.agents.tools.fundamental_tools import fmt_pct, gap_item
from atlas.agents.tools.registry import Tool
from atlas.core.calendar import market_date
from atlas.core.config import NewsAnalystConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.view import DataView

_log = get_logger(__name__)

EPSILON = timedelta(microseconds=1)
"""Computed items are timed this long before the decision cutoff."""

WHEN = {
    "BMO": "before the market open (BMO)",
    "AMC": "after the market close (AMC)",
    "unknown": "time of day not known",
}
"""How an event's `time_of_day` reads."""


class NewsArgs(BaseModel):
    """Arguments of the news tools: one ticker (normalised; a malformed one fails validation,
    so the model gets an error text). The windows are settings, not arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Annotated[
        str,
        Field(min_length=1, max_length=20, description="Ticker symbol, for example AAPL"),
        AfterValidator(normalize_symbol),
    ]


# --- helpers ----------------------------------------------------------------------------------


def _settings(view: DataView) -> NewsAnalystConfig:
    return view.config.agents.news


def _computed_at(view: DataView) -> datetime:
    """The time of a computed evidence item: just before the decision cutoff."""
    return view.as_of.cutoff - EPSILON


def _require(view: DataView, symbol: str) -> None:
    if symbol in view.blocked:
        raise DataError(
            f"{symbol} is blocked on {view.day.isoformat()} by critical data-quality issues"
        )


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _computed(view: DataView, evidence_id: str, text: str) -> Evidence:
    return Evidence(id=evidence_id, source="atlas", as_of=_computed_at(view), text=text)


def _newest_first(items: Sequence[Evidence]) -> list[Evidence]:
    return sorted(items, key=lambda e: (-e.as_of.timestamp(), e.id))


def _instant(value: object) -> datetime:
    return pd.Timestamp(value).tz_convert("UTC").to_pydatetime()  # type: ignore[arg-type]


def _day(value: object) -> date:
    return pd.Timestamp(value).date()  # type: ignore[arg-type]


# --- get_company_news -------------------------------------------------------------------------


def _company_articles(view: DataView, symbol: str) -> tuple[list[Evidence], bool]:
    """The stock's articles in the window, most relevant first, at most `max_articles`, and
    whether more exist than are shown."""
    settings = _settings(view)
    found = view.news(symbol, settings.lookback_days, settings.max_articles + 1)
    return found[: settings.max_articles], len(found) > settings.max_articles


def company_article_count(view: DataView, symbol: str) -> int:
    """How many company articles `company_news` shows for `symbol`: 0 when there are none or
    the news table was never written (the empty-news guard treats both as no news). A blocked
    symbol still raises `DataError`."""
    _require(view, symbol)
    try:
        return len(_company_articles(view, symbol)[0])
    except DataError as exc:  # the `gap:` item of the prefetch says the same; logged here too
        _log.warning("news.company_count_unavailable", symbol=symbol, reason=str(exc))
        return 0


def company_news(view: DataView, symbol: str) -> list[Evidence]:
    """The coverage item and the stock's articles of the last `lookback_days` (newest first,
    at most `max_articles`). `DataError` for a blocked symbol or a lake without news."""
    _require(view, symbol)
    settings = _settings(view)
    shown, capped = _company_articles(view, symbol)
    day = view.day.isoformat()
    text = (
        f"{symbol} company news on {day}: {_count(len(shown), 'article')} in the "
        f"{settings.lookback_days} calendar days to {day} (duplicates and roundups removed)"
    )
    if shown:
        text += f"; newest published {max(e.as_of for e in shown).date().isoformat()}"
    if capped:
        text += (
            f"; more articles exist than the {settings.max_articles} shown "
            "(the least relevant are left out)"
        )
    return [_computed(view, f"ncov:{symbol}:{day}:company", text), *_newest_first(shown)]


# --- get_sector_news --------------------------------------------------------------------------


def _sector_peers(view: DataView, symbol: str, sector: str) -> list[str]:
    """The members of the factor universe in `sector` other than `symbol`, highest average
    dollar volume first (those without a volume last), ties by symbol."""
    universe = view.config.quant.factors.universe
    peers = []
    for other in view.universe(universe):
        if other == symbol:
            continue
        try:
            if view.instrument(other).sector == sector:
                peers.append(other)
        except DataError as exc:  # no instrument row: no known sector, so not a peer (logged)
            _log.warning("news.peer_skipped", symbol=other, reason=str(exc))
    if not peers:
        return []
    volume = view.adv(peers)["adv_usd"]
    return sorted(
        peers,
        key=lambda p: (
            -volume[p] if math.isfinite(float(volume.get(p, math.nan))) else math.inf,
            p,
        ),
    )


def sector_news(view: DataView, symbol: str) -> list[Evidence]:
    """The sector coverage item and the articles about the stock's sector peers (see the module
    docstring). `DataError` for a blocked or unknown symbol or a lake without news."""
    _require(view, symbol)
    settings = _settings(view)
    day = view.day.isoformat()
    sector = view.instrument(symbol).sector
    cover = f"ncov:{symbol}:{day}:sector"
    if not sector:
        return [
            _computed(
                view,
                cover,
                f"{symbol} sector news on {day}: {symbol} has no GICS sector in the instruments "
                "table, so no sector news was read (0 peers, 0 articles)",
            )
        ]
    peers = _sector_peers(view, symbol, sector)[: settings.sector_peers]
    universe = view.config.quant.factors.universe
    if not peers:
        return [
            _computed(
                view,
                cover,
                f"{symbol} sector news on {day}: no other {sector} member of {universe} on "
                f"{day}, so no sector news was read (0 peers, 0 articles)",
            )
        ]
    own = {e.id for e in _company_articles(view, symbol)[0]}
    taken: dict[str, Evidence] = {}
    per_peer = []
    for peer in peers:
        found = view.news(
            peer, settings.lookback_days, settings.sector_articles_per_peer + len(own)
        )
        mine = [e for e in found if e.id not in own][: settings.sector_articles_per_peer]
        per_peer.append(f"{peer} ({_count(len(mine), 'article')})")
        for item in mine:
            taken.setdefault(item.id, item)
    shown = _newest_first(list(taken.values()))[: settings.sector_articles]
    text = (
        f"{symbol} sector news on {day}: {sector} peers read by dollar volume: "
        f"{', '.join(per_peer)}; {_count(len(shown), 'article')} shown from the last "
        f"{settings.lookback_days} calendar days (articles already in the company list left out)"
    )
    return [_computed(view, cover, text), *shown]


# --- get_upcoming_events ----------------------------------------------------------------------


def _ahead(days: int) -> str:
    if days == 0:
        return "today"
    return f"{_count(days, 'calendar day')} ahead"


def _event_item(row: pd.Series, symbol: str, today: date) -> Evidence:
    kind, when_day = str(row["event_type"]), _day(row["event_date"])
    source = str(row["source"])
    ahead = _ahead((when_day - today).days)
    published = _day(row["available_at"]).isoformat()
    when = WHEN.get(str(row["time_of_day"]), WHEN["unknown"])
    if kind == "earnings" and source == "estimated":
        text = (
            f"{symbol} earnings estimated for {when_day} {when}, {ahead}: no date is scheduled "
            "yet, so this is the last report date plus 52 weeks (an estimate, not a confirmed "
            "date)"
        )
    elif kind == "earnings" and source.startswith("sec_"):
        text = f"{symbol} earnings reported on {when_day} (filing {source})"
    elif kind == "earnings":
        text = (
            f"{symbol} earnings expected on {when_day} {when}, {ahead}; a scheduled date "
            f"(latest published {published})"
        )
    elif kind == "ex_dividend" and source == "corporate_actions":
        text = f"{symbol} ex-dividend date {when_day} (already in the price data)"
    elif kind == "ex_dividend":
        text = (
            f"{symbol} ex-dividend date {when_day}, {ahead}; a scheduled date "
            f"(latest published {published})"
        )
    else:
        text = f"{symbol} {kind.replace('_', ' ')} on {when_day}, {ahead} (source {source})"
    return Evidence(
        id=f"evt:{symbol}:{kind}:{when_day}",
        source="events",
        as_of=_instant(row["available_at"]),
        text=text,
    )


def upcoming_events(view: DataView, symbol: str) -> list[Evidence]:
    """The stock's own events within `events_horizon_days` (earnings, ex-dividend), scheduled
    or estimated, soonest first; one `none` item when there are none. Market-wide events are
    left out. `DataError` for a blocked symbol."""
    _require(view, symbol)
    settings = _settings(view)
    frame = view.events([symbol], settings.events_horizon_days)
    own = frame.loc[frame["symbol"] == symbol]
    items: dict[str, Evidence] = {}
    for _, row in own.iterrows():
        item = _event_item(row, symbol, view.day)
        items.setdefault(item.id, item)
    if items:
        return list(items.values())
    day = view.day.isoformat()
    return [
        _computed(
            view,
            f"evt:{symbol}:{day}:none",
            f"No earnings or ex-dividend event of {symbol} is known within the "
            f"{settings.events_horizon_days} calendar days from {day}.",
        )
    ]


# --- get_past_earnings_reactions --------------------------------------------------------------


def _reaction_item(row: pd.Series, symbol: str, sessions: int) -> Evidence:
    event_day = market_date(_instant(row["event_time"]))
    text = (
        f"{symbol} earnings of {event_day} (reaction session {_day(row['reaction_date'])}): "
        f"gap {fmt_pct(float(row['gap_return']), signed=True)}, open-to-close "
        f"{fmt_pct(float(row['oc_return']), signed=True)}, abnormal open-to-close "
        f"{fmt_pct(float(row['oc_abnormal']), signed=True)} and {sessions}-session abnormal "
        f"return {fmt_pct(float(row['car']), signed=True)} against {row['benchmark']}"
    )
    return Evidence(
        id=f"rx:{symbol}:{event_day}",
        source="earnings_reactions",
        as_of=_instant(row["available_at"]),
        text=text,
    )


def _reaction_summary(
    view: DataView, symbol: str, rows: pd.DataFrame, days: list[date], sessions: int
) -> Evidence:
    """The `rxs` item of `rows` (newest first, `days` their event days). The lake's table
    has no null return, so every row counts."""
    day = view.day.isoformat()
    pairs = list(zip((float(c) for c in rows["car"]), days, strict=True))
    k = len(pairs)
    mean_abs = sum(abs(c) for c, _ in pairs) / k
    high, low = max(pairs), min(pairs)
    text = (
        f"{symbol} last {_count(k, 'earnings reaction')} ({min(days)} to {max(days)}): "
        f"mean absolute {sessions}-session abnormal return {fmt_pct(mean_abs)}, "
        f"{sum(c > 0 for c, _ in pairs)} of {k} positive; "
        f"largest {fmt_pct(high[0], signed=True)} ({high[1]}), "
        f"smallest {fmt_pct(low[0], signed=True)} ({low[1]})"
    )
    return _computed(view, f"rxs:{symbol}:{day}:summary", text)


def past_reactions(view: DataView, symbol: str) -> list[Evidence]:
    """The summary item and the stock's last `reactions` earnings reactions, newest first.
    `DataError` for a blocked symbol."""
    _require(view, symbol)
    settings = _settings(view)
    day = view.day.isoformat()
    rows = view.earnings_reactions(symbol, settings.reactions)
    if rows.empty:
        return [
            _computed(
                view,
                f"rxs:{symbol}:{day}:summary",
                f"{symbol}: no earnings reaction is known on {day} (0 reactions)",
            )
        ]
    sessions = view.config.data.events.reaction_days
    days = [market_date(_instant(t)) for t in rows["event_time"]]
    items = [_reaction_item(r, symbol, sessions) for _, r in rows.iterrows()]
    return [_reaction_summary(view, symbol, rows, days, sessions), *items]


# --- prefetch and the tools -------------------------------------------------------------------


def prefetch(view: DataView, symbol: str) -> list[Evidence]:
    """All four tools' evidence for `symbol`: company news, events, reactions and sector news.
    A blocked symbol raises `DataError` (no report, no new risk); another tool's `DataError`
    becomes a `gap:` item."""
    symbol = normalize_symbol(symbol)
    _require(view, symbol)
    items: list[Evidence] = []
    for tool, build in (
        ("get_company_news", company_news),
        ("get_upcoming_events", upcoming_events),
        ("get_past_earnings_reactions", past_reactions),
        ("get_sector_news", sector_news),
    ):
        try:
            items += build(view, symbol)
        except DataError as exc:
            items.append(gap_item(view, symbol, tool, exc))
    return items


GET_COMPANY_NEWS = Tool(
    name="get_company_news",
    description=(
        "One stock's news articles of the last weeks known today (duplicates and roundups "
        "removed, newest first) and how many there were."
    ),
    args=NewsArgs,
    fn=lambda ctx, args: company_news(ctx.view, args.symbol),
)
GET_UPCOMING_EVENTS = Tool(
    name="get_upcoming_events",
    description=(
        "One stock's upcoming events known today: the next earnings date with before-open or "
        "after-close timing (scheduled or estimated) and the ex-dividend date."
    ),
    args=NewsArgs,
    fn=lambda ctx, args: upcoming_events(ctx.view, args.symbol),
)
GET_PAST_EARNINGS_REACTIONS = Tool(
    name="get_past_earnings_reactions",
    description=(
        "One stock's price reaction to its last earnings reports (gap, open-to-close and "
        "abnormal returns against the market) with their summary."
    ),
    args=NewsArgs,
    fn=lambda ctx, args: past_reactions(ctx.view, args.symbol),
)
GET_SECTOR_NEWS = Tool(
    name="get_sector_news",
    description=(
        "Recent news articles about the largest peers in one stock's GICS sector known today, "
        "newest first, without the articles already in the stock's own news."
    ),
    args=NewsArgs,
    fn=lambda ctx, args: sector_news(ctx.view, args.symbol),
)

NEWS_TOOLS = (GET_COMPANY_NEWS, GET_UPCOMING_EVENTS, GET_PAST_EARNINGS_REACTIONS, GET_SECTOR_NEWS)
"""The news analyst's tools, in brief order."""


__all__ = [
    "EPSILON",
    "GET_COMPANY_NEWS",
    "GET_PAST_EARNINGS_REACTIONS",
    "GET_SECTOR_NEWS",
    "GET_UPCOMING_EVENTS",
    "NEWS_TOOLS",
    "WHEN",
    "NewsArgs",
    "company_article_count",
    "company_news",
    "past_reactions",
    "prefetch",
    "sector_news",
    "upcoming_events",
]
