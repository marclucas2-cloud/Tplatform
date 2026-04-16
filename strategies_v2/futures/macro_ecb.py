"""Macro ECB Event Momentum — fade-or-follow ECB rate decisions.

Edge: ECB Governing Council monetary policy decisions (8x/year, 14:15 CET)
create asymmetric volatility on EU equity indices. The 30-min move post
announcement (which includes the 14:45 press conference) is a strong
momentum signal that continues over the next 1-3 hours.

Backtest 2021-2026 (5 years), 3 instruments :
  ESTX50: 23 trades, +$1,041, avg +$45, Sharpe 3.41, PF 1.73
  DAX:    23 trades, +$3,960, avg +$172, Sharpe 3.53, PF 1.81
  CAC40:  23 trades, +$2,003, avg +$87, Sharpe 4.08, PF 2.01
  COMBINED: 69 trades, +$7,004, avg +$101, Sharpe 3.18, MaxDD -$1,846

Walk-forward yearly: 4/6 PASS (2022, 2023, 2024, 2026 profitable;
2021 + 2025 = pause cycles, lossy).

Hypothesis: works during ECB transition cycles (hike or cut) where
markets are uncertain about the trajectory; fails during pause periods.

Signal:
  1. Today must be in BCE calendar (data/calendar_bce.csv)
  2. Now must be 14:45-17:00 CET (30min post 14:15 announcement)
  3. Compute move = (close at T+30min) / (open at T0) - 1
  4. If |move| > 0.15% -> trade in direction (momentum follow-through)
  5. SL = 50% of move, TP = 2x move, max hold 3h
  6. One trade per event per instrument

This is a special case: the strategy is event-driven and must be invoked
ONLY at the right time. The worker should call it from a dedicated
intraday cycle (not the daily futures cycle).
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


PARIS_TZ = ZoneInfo("Europe/Paris")
UTC_TZ = ZoneInfo("UTC")
_CALENDAR_PATH = Path(__file__).parent.parent.parent / "data" / "calendar_bce.csv"


def _load_bce_dates() -> set[date]:
    """Load ECB meeting dates from CSV. Returns set of date objects."""
    if not _CALENDAR_PATH.exists():
        return set()
    dates = set()
    with open(_CALENDAR_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = datetime.strptime(row["date"], "%Y-%m-%d").date()
            dates.add(d)
    return dates


_BCE_DATES_CACHE: set[date] | None = None


def get_bce_dates() -> set[date]:
    global _BCE_DATES_CACHE
    if _BCE_DATES_CACHE is None:
        _BCE_DATES_CACHE = _load_bce_dates()
    return _BCE_DATES_CACHE


class MacroECB(StrategyBase):
    """ECB rate decision momentum follow-through.

    One instance per instrument (DAX, CAC40, ESTX50). Each instance
    trades only its symbol on ECB days.

    Note: This is an INTRADAY strategy. The worker must call on_bar with
    5-minute bars during 14:30-17:00 CET on ECB days. on_bar will detect
    if the right time has arrived and emit at most one signal per ECB
    event (per instrument).
    """

    SUPPORTED_SYMBOLS = ("DAX", "CAC40", "ESTX50")

    def __init__(self, symbol: str = "ESTX50") -> None:
        if symbol not in self.SUPPORTED_SYMBOLS:
            raise ValueError(f"symbol must be in {self.SUPPORTED_SYMBOLS}, got {symbol}")
        self._symbol = symbol
        self.momentum_threshold: float = 0.0015  # 0.15%
        self.obs_minutes: int = 30
        self.sl_pct_of_move: float = 0.5
        self.tp_mult_of_move: float = 2.0
        self.max_hold_minutes: int = 180  # 3 hours
        # Event tracking : key = ECB date (local), value = "TRIGGERED"
        # to ensure one signal per event per instance
        self._fired_dates: set[date] = set()
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return f"macro_ecb_{self._symbol.lower()}"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    @property
    def symbol(self) -> str:
        return self._symbol

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self.data_feed is None:
            return None
        if bar.symbol != self._symbol:
            return None

        # Convert bar timestamp to Paris local
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC_TZ)
        local = ts.astimezone(PARIS_TZ)
        local_date = local.date()
        local_time = local.time()

        # Filter 1 : must be ECB day
        if local_date not in get_bce_dates():
            return None

        # Filter 2 : must be after announcement window (14:15 + obs_minutes).
        # Bug fix 2026-04-16: en prod, les bars 5min IBKR EU ont ~15-20min de
        # lag. Le scheduler shift de 14:50 -> 15:10 (cf worker.py) garantit
        # que les bars 14:45 sont dispo lors du run. On utilise local_time du
        # bar (compatibility tests) mais avec un fallback now() si bar trop
        # ancien (live data lag protection).
        decision_time = time(14, 15)
        earliest_signal_time = time(14, 45)
        latest_signal_time = time(17, 0)
        # Fallback live : si bar > 20min old, utiliser now() pour le filter
        now_paris = datetime.now(PARIS_TZ)
        if (now_paris.date() == local_date
                and (now_paris - local).total_seconds() > 1200):
            local_time = now_paris.time()
        if local_time < earliest_signal_time or local_time > latest_signal_time:
            return None

        # Filter 3 : already fired today
        if local_date in self._fired_dates:
            return None

        # Compute move : need bar at T0 (14:15) and bar at T0 + obs_minutes (14:45)
        # We use get_bars to retrieve recent 5-min bars
        bars_needed = 12  # ~1h of 5min bars
        recent = self.data_feed.get_bars(self._symbol, bars_needed)
        if recent is None or len(recent) < 6:
            return None

        # Find bar at decision time today
        recent_local_idx = recent.index.tz_convert(PARIS_TZ) if hasattr(recent.index, "tz_convert") else recent.index
        # Build mask of today + decision_time
        today_mask = [(d.date() == local_date) for d in recent_local_idx]
        recent_today = recent[today_mask]
        if recent_today.empty:
            return None

        # Find bar nearest to 14:15
        recent_today_local = recent_today.index.tz_convert(PARIS_TZ) if hasattr(recent_today.index, "tz_convert") else recent_today.index
        target_t0 = time(14, 15)
        # Find first bar >= 14:15
        t0_idx = None
        for i, dt in enumerate(recent_today_local):
            if dt.time() >= target_t0:
                t0_idx = i
                break
        if t0_idx is None:
            return None

        t0_open = float(recent_today["open"].iloc[t0_idx])
        # Need observation bars : at least obs_minutes / 5 bars after T0
        obs_bars_needed = self.obs_minutes // 5
        if t0_idx + obs_bars_needed >= len(recent_today):
            return None
        t_obs_close = float(recent_today["close"].iloc[t0_idx + obs_bars_needed - 1])

        move = (t_obs_close - t0_open) / t0_open
        if abs(move) < self.momentum_threshold:
            # Even if no signal, mark fired so we don't keep checking
            self._fired_dates.add(local_date)
            return None

        # Build signal in direction of move
        entry_price = bar.close
        if move > 0:
            side = "BUY"
            sl = entry_price * (1 - abs(move) * self.sl_pct_of_move)
            tp = entry_price * (1 + abs(move) * self.tp_mult_of_move)
        else:
            side = "SELL"
            sl = entry_price * (1 + abs(move) * self.sl_pct_of_move)
            tp = entry_price * (1 - abs(move) * self.tp_mult_of_move)

        self._fired_dates.add(local_date)
        return Signal(
            symbol=self._symbol,
            side=side,
            strategy_name=self.name,
            stop_loss=sl,
            take_profit=tp,
            strength=min(abs(move) * 50, 1.0),
        )

    def reset_fired_dates(self) -> None:
        """Reset internal state. Useful for tests or worker restart."""
        self._fired_dates.clear()

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self._symbol,
            "momentum_threshold": self.momentum_threshold,
            "obs_minutes": self.obs_minutes,
            "sl_pct_of_move": self.sl_pct_of_move,
            "tp_mult_of_move": self.tp_mult_of_move,
            "max_hold_minutes": self.max_hold_minutes,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for k, v in params.items():
            if k == "symbol":
                continue
            if hasattr(self, k):
                setattr(self, k, v)

    def get_parameter_grid(self) -> Dict[str, list]:
        return {
            "momentum_threshold": [0.001, 0.0015, 0.002, 0.0025],
            "obs_minutes": [15, 30, 45],
            "sl_pct_of_move": [0.4, 0.5, 0.6],
            "tp_mult_of_move": [1.5, 2.0, 2.5],
        }
