"""Fixture loaders and frame builders for the Stage 08 universe tests (no network)."""

from datetime import date
from pathlib import Path

import pandas as pd

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "universe"


def constituents_html() -> str:
    """The recorded constituents table (revision 1382178819)."""
    return (FIXTURES / "wikipedia_sp500_constituents.html").read_text(encoding="utf-8")


def changes_html() -> str:
    """The recorded change log (revision 1379179365)."""
    return (FIXTURES / "wikipedia_sp500_changes.html").read_text(encoding="utf-8")


def constituents(*symbols: str, date_added: date | None = None) -> pd.DataFrame:
    """A `parse_constituents`-shaped frame for `symbols` (name = symbol, sector "Tech")."""
    return pd.DataFrame(
        {
            "symbol": list(symbols),
            "name": list(symbols),
            "sector": ["Tech"] * len(symbols),
            "industry": ["Software"] * len(symbols),
            "exchange": ["XNYS"] * len(symbols),
            "date_added": [date_added] * len(symbols),
        }
    )


def changes(*rows: tuple[date, str | None, str | None]) -> pd.DataFrame:
    """A `parse_changes`-shaped frame from `(date, added, removed)` rows, given newest first."""
    return pd.DataFrame(
        {
            "date": [r[0] for r in rows],
            "added": [r[1] for r in rows],
            "added_name": [f"{r[1]} Inc" if r[1] else None for r in rows],
            "removed": [r[2] for r in rows],
            "removed_name": [f"{r[2]} Corp" if r[2] else None for r in rows],
            "reason": ["test"] * len(rows),
        }
    )
