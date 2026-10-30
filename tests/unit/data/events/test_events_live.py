"""Stage 12: live smoke tests against EDGAR, FRED and Yahoo (`-m live`, deselected by default)."""

from datetime import date

import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.data.events.earnings import YahooEarningsCalendar, earnings_events, fetch_filings
from atlas.data.fundamentals.edgar_client import build_edgar_client
from atlas.data.macro.fred_client import build_fred_client

pytestmark = pytest.mark.live


def test_live_aapl_earnings_events() -> None:
    cfg = load_config("dev")
    secret = cfg.secrets.sec_user_agent
    if secret is None or not secret.get_secret_value().strip():
        pytest.skip("SEC_USER_AGENT not set")
    with build_edgar_client(cfg) as client:
        filings = fetch_filings(client, 320193, since=date(2018, 1, 1))
    events, _ = earnings_events(filings, "AAPL", TradingCalendar(), periodic_match_days=60)
    days = set(events["event_date"])
    assert {date(2023, 8, 3), date(2024, 2, 1), date(2019, 1, 2)} <= days
    assert set(events["time_of_day"]) == {"AMC"}


def test_live_fred_release_dates() -> None:
    cfg = load_config("dev")
    secret = cfg.secrets.fred_api_key
    if secret is None or not secret.get_secret_value().strip():
        pytest.skip("FRED_API_KEY not set")
    with build_fred_client(cfg) as client:
        cpi = client.release_dates(10, realtime_start=date(2024, 1, 1))
        first = client.initial_release_dates("CPIAUCSL", observation_start=date(2023, 1, 1))
    assert date(2024, 1, 11) in cpi and date(2024, 1, 11) in first
    assert date(2024, 2, 9) in cpi and date(2024, 2, 9) not in first  # seasonal factors


def test_live_yahoo_calendar() -> None:
    out = YahooEarningsCalendar().fetch_upcoming(["AAPL", "MSFT"])
    assert set(out["symbol"]) <= {"AAPL", "MSFT"} and len(out) >= 1
    assert set(out["event_type"]) <= {"earnings", "ex_dividend"}
