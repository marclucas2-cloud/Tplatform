"""Shared types and helpers for US stock cross-sectional monthly strategies.

The 3 strategies (tom, rs_spy, sector_rot_us) all follow the same pattern:
    1. Determine if today is an active day (month-end rebal, hold period, etc.)
    2. Compute target portfolio = {symbol: target_notional_usd}
    3. Orchestrator diffs vs current Alpaca positions and executes

Each strategy has:
    - NAME: unique identifier
    - compute_target_portfolio(prices, capital, as_of) -> Dict[str, float]
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
UNIVERSE_FILE = ROOT / "data" / "us_stocks" / "_universe.json"
METADATA_FILE = ROOT / "data" / "us_stocks" / "_metadata.csv"


@dataclass
class USPosition:
    """Target position from a US stock strategy."""
    strategy: str
    symbol: str
    side: str            # "BUY" or "SELL"
    notional: float      # USD
    reason: str = ""

    def __post_init__(self):
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {self.side}")
        if self.notional <= 0:
            raise ValueError(f"notional must be > 0, got {self.notional}")


def load_universe() -> list[str]:
    """Load the S&P 500 universe filtered for quality (~496 tickers)."""
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            f"{UNIVERSE_FILE} missing — run scripts/download_us_data.py first"
        )
    data = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
    return data.get("tickers", [])


def load_sector_map() -> dict[str, str]:
    """Return {ticker: GICS sector} from the metadata CSV."""
    if not METADATA_FILE.exists():
        raise FileNotFoundError(f"{METADATA_FILE} missing")
    df = pd.read_csv(METADATA_FILE)
    return dict(zip(df["ticker"], df["sector"]))


def trading_month_ends(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Return the CALENDAR last business day of each completed month in idx.

    A month is only included if its calendar-last-business-day is actually
    present in `idx`. This prevents treating "most recent day in data" as a
    month-end when the month isn't over yet (critical for live paper trading
    where we don't have future data).

    Example: if idx goes from 2023-01-03 to 2026-04-06, the last month in the
    result is 2026-03-31 (March complete). April is excluded because
    2026-04-30 is not yet in idx.
    """
    import pandas.tseries.offsets as offsets
    idx_set = set(idx)
    ym_pairs = sorted({(int(d.year), int(d.month)) for d in idx})
    result = []
    for y, m in ym_pairs:
        try:
            last_bday = (pd.Timestamp(year=y, month=m, day=1) + offsets.BMonthEnd(0))
        except Exception:
            continue
        if last_bday in idx_set:
            result.append(last_bday)
    return pd.DatetimeIndex(sorted(result))


def trading_month_starts(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Return first trading day of each (year, month) present in idx."""
    s = pd.Series(idx, index=idx)
    first = s.groupby([idx.year, idx.month]).first()
    return pd.DatetimeIndex(first.values)


def nth_trading_day_of_month(idx: pd.DatetimeIndex, year: int, month: int, n: int) -> pd.Timestamp | None:
    """Return the nth (1-indexed) trading day of the given (year, month)."""
    mask = (idx.year == year) & (idx.month == month)
    sub = idx[mask]
    if len(sub) < n:
        return None
    return sub[n - 1]


def build_price_matrix(
    prices: Dict[str, pd.DataFrame],
    close_col: str = "close",
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Build a single DataFrame (index=date, columns=tickers) of close prices."""
    if tickers is None:
        tickers = list(prices.keys())
    valid = [t for t in tickers if t in prices and close_col in prices[t].columns]
    if not valid:
        return pd.DataFrame()
    all_idx = sorted(set().union(*[set(prices[t].index) for t in valid]))
    idx = pd.DatetimeIndex(all_idx)
    out = pd.DataFrame({t: prices[t][close_col] for t in valid}).reindex(idx).ffill()
    return out
