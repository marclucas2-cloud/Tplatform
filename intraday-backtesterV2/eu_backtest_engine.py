"""
Moteur de backtest adapte pour les marches EU.

Differences vs US engine :
- Horaires : 9:00-17:30 CET, entree 9:05+, sortie forcee 17:25
- Couts : commission 0.10% + slippage 0.03% = 0.13% aller simple (vs $0.005/share US)
- Capital : EUR 200K (paper IBKR)
- Timezone : Europe/Paris (CET)
- Position sizing : max 10% du capital (moins de diversification EU)
"""
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import time as dt_time
from typing import Optional


# ── EU Config ──
EU_INITIAL_CAPITAL = 200_000       # EUR (IBKR paper)
EU_MAX_POSITION_PCT = 0.10         # 10% max par position
EU_MAX_SIMULTANEOUS = 3            # Max 3 positions simultanees (faible frequence)
EU_COMMISSION_PCT = 0.0010         # 0.10% commission
EU_SLIPPAGE_PCT = 0.0003           # 0.03% slippage

# EU Market hours (CET)
EU_MARKET_OPEN = dt_time(9, 0)
EU_MARKET_CLOSE = dt_time(17, 30)
EU_EARLIEST_ENTRY = dt_time(9, 5)
EU_FORCE_EXIT_TIME = dt_time(17, 25)


class EUSignal:
    """Signal de trading pour les marches EU."""
    def __init__(
        self,
        action: str,          # "LONG" or "SHORT"
        ticker: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        timestamp: pd.Timestamp,
        metadata: dict = None,
    ):
        self.action = action
        self.ticker = ticker
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.timestamp = timestamp
        self.metadata = metadata or {}


class EUBaseStrategy(ABC):
    """Classe abstraite pour les strategies EU."""

    name: str = "EUBaseStrategy"

    @abstractmethod
    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        """
        Pour un jour donne, retourne une liste de signaux EU.
        data: {ticker: DataFrame du jour avec OHLCV}
        """
        pass

    def get_required_tickers(self) -> list[str]:
        """Retourne les tickers necessaires pour cette strategie."""
        return []


class EUBacktestEngine:
    """
    Moteur de backtest evenementiel adapte pour les marches EU.
    Commissions en % (pas par share comme US).
    """

    def __init__(
        self,
        strategy: EUBaseStrategy,
        initial_capital: float = EU_INITIAL_CAPITAL,
        commission_pct: float = EU_COMMISSION_PCT,
        slippage_pct: float = EU_SLIPPAGE_PCT,
        max_position_pct: float = EU_MAX_POSITION_PCT,
        max_simultaneous: int = EU_MAX_SIMULTANEOUS,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.max_position_pct = max_position_pct
        self.max_simultaneous = max_simultaneous
        self.trades: list[dict] = []
        self.open_positions: list[dict] = []

    def run(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Execute le backtest sur toutes les donnees.
        data: {ticker: DataFrame avec toutes les barres}
        """
        print(f"\n[EU BACKTEST] Running: {self.strategy.name}")
        print(f"  Capital: EUR {self.initial_capital:,.0f}")
        print(f"  Costs: {self.commission_pct*100:.2f}% commission + {self.slippage_pct*100:.2f}% slippage")
        print(f"  Tickers: {list(data.keys())}")

        # Get all trading dates
        all_dates = set()
        for ticker_df in data.values():
            if hasattr(ticker_df.index, 'date'):
                all_dates.update(ticker_df.index.date)
            else:
                # Daily data — index IS the date
                for idx in ticker_df.index:
                    if hasattr(idx, 'date'):
                        all_dates.add(idx.date())
                    else:
                        all_dates.add(pd.Timestamp(idx).date())
        all_dates = sorted(all_dates)

        print(f"  Trading days: {len(all_dates)}")

        for date in all_dates:
            # Check that at least one ticker has data for this date
            has_data = False
            for ticker, df in data.items():
                if hasattr(df.index, 'date'):
                    if date in set(df.index.date):
                        has_data = True
                        break
                else:
                    if pd.Timestamp(date) in df.index:
                        has_data = True
                        break

            if not has_data:
                continue

            # Pass FULL data to strategy (strategy handles date filtering)
            # This allows strategies to access historical bars for prev_close, averages, etc.
            try:
                signals = self.strategy.generate_signals(data, date)
            except Exception as e:
                print(f"  [ERROR] {date}: {e}")
                continue

            # Build day_data for intraday simulation (stops/targets)
            day_data = {}
            for ticker, df in data.items():
                if hasattr(df.index, 'date'):
                    day_df = df[df.index.date == date]
                else:
                    day_df = df[df.index == pd.Timestamp(date)]
                if not day_df.empty:
                    day_data[ticker] = day_df

            # Execute signals (with EU time guards for intraday data)
            for signal in signals:
                if len(self.open_positions) >= self.max_simultaneous:
                    break
                # Guard: for intraday data, reject signals outside 9:05-17:25 CET
                sig_time = signal.timestamp.time() if hasattr(signal.timestamp, 'time') else None
                if sig_time and sig_time != pd.Timestamp("00:00").time():
                    # Only apply time guard for intraday timestamps (not midnight = daily data)
                    if sig_time < EU_EARLIEST_ENTRY or sig_time >= EU_FORCE_EXIT_TIME:
                        continue
                self._open_position(signal, day_data)

            # Simulate intraday — check stops/targets
            self._simulate_day(day_data, date)

            # Force close at end of day
            self._force_close_all(day_data, date)

        # Build trades DataFrame
        trades_df = pd.DataFrame(self.trades) if self.trades else pd.DataFrame()
        print(f"  Total trades: {len(trades_df)}")

        return trades_df

    def _open_position(self, signal: EUSignal, day_data: dict):
        """Open a position based on a signal."""
        # Position sizing: max_position_pct of capital
        max_euros = self.capital * self.max_position_pct
        shares = int(max_euros / signal.entry_price)

        if shares < 1:
            return

        # Slippage at entry
        if signal.action == "LONG":
            actual_entry = signal.entry_price * (1 + self.slippage_pct)
        else:
            actual_entry = signal.entry_price * (1 - self.slippage_pct)

        # Commission (percentage-based)
        notional = shares * actual_entry
        commission = notional * self.commission_pct

        self.open_positions.append({
            "ticker": signal.ticker,
            "action": signal.action,
            "entry_price": actual_entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "shares": shares,
            "entry_time": signal.timestamp,
            "commission_entry": commission,
            "metadata": signal.metadata,
        })

    def _simulate_day(self, day_data: dict, date):
        """Simulate bar by bar to check stops and targets."""
        closed = []

        for pos in self.open_positions:
            ticker = pos["ticker"]
            if ticker not in day_data:
                continue

            df = day_data[ticker]

            # For daily data with single row, skip intraday simulation
            if len(df) <= 1:
                continue

            # Only look at bars after entry and before force exit
            bars_after = df[df.index > pos["entry_time"]]
            if hasattr(df.index[0], 'time'):
                bars_after = bars_after[bars_after.index.time <= EU_FORCE_EXIT_TIME]

            for ts, bar in bars_after.iterrows():
                if pos["action"] == "LONG":
                    if bar["low"] <= pos["stop_loss"]:
                        self._close_position(pos, pos["stop_loss"], ts, "stop_loss")
                        closed.append(pos)
                        break
                    if bar["high"] >= pos["take_profit"]:
                        self._close_position(pos, pos["take_profit"], ts, "take_profit")
                        closed.append(pos)
                        break
                else:  # SHORT
                    if bar["high"] >= pos["stop_loss"]:
                        self._close_position(pos, pos["stop_loss"], ts, "stop_loss")
                        closed.append(pos)
                        break
                    if bar["low"] <= pos["take_profit"]:
                        self._close_position(pos, pos["take_profit"], ts, "take_profit")
                        closed.append(pos)
                        break

        for pos in closed:
            if pos in self.open_positions:
                self.open_positions.remove(pos)

    def _force_close_all(self, day_data: dict, date):
        """Force close all positions at 17:25 CET (or last bar).
        For daily data (1 bar/day), check SL/TP against bar's high/low.
        When both SL and TP are within bar's range, use close direction to determine
        which was hit (if close > open for LONG = TP likely hit first, else SL)."""
        for pos in self.open_positions[:]:
            ticker = pos["ticker"]
            if ticker in day_data:
                df = day_data[ticker]
                if not df.empty:
                    bar = df.iloc[-1]
                    ts = df.index[-1]

                    if pos["action"] == "LONG":
                        sl_hit = bar["low"] <= pos["stop_loss"]
                        tp_hit = bar["high"] >= pos["take_profit"]

                        if sl_hit and tp_hit:
                            # Both levels breached — use close position to decide
                            # If close > entry, TP was likely hit; else SL
                            if bar["close"] > pos["entry_price"]:
                                self._close_position(pos, pos["take_profit"], ts, "take_profit")
                            else:
                                self._close_position(pos, pos["stop_loss"], ts, "stop_loss")
                        elif sl_hit:
                            self._close_position(pos, pos["stop_loss"], ts, "stop_loss")
                        elif tp_hit:
                            self._close_position(pos, pos["take_profit"], ts, "take_profit")
                        else:
                            self._close_position(pos, bar["close"], ts, "eod_close")
                    else:  # SHORT
                        sl_hit = bar["high"] >= pos["stop_loss"]
                        tp_hit = bar["low"] <= pos["take_profit"]

                        if sl_hit and tp_hit:
                            if bar["close"] < pos["entry_price"]:
                                self._close_position(pos, pos["take_profit"], ts, "take_profit")
                            else:
                                self._close_position(pos, pos["stop_loss"], ts, "stop_loss")
                        elif sl_hit:
                            self._close_position(pos, pos["stop_loss"], ts, "stop_loss")
                        elif tp_hit:
                            self._close_position(pos, pos["take_profit"], ts, "take_profit")
                        else:
                            self._close_position(pos, bar["close"], ts, "eod_close")
            self.open_positions.remove(pos)

    def _close_position(self, pos: dict, exit_price: float, exit_time, reason: str):
        """Close a position and record the trade."""
        # Slippage at exit
        if pos["action"] == "LONG":
            actual_exit = exit_price * (1 - self.slippage_pct)
            pnl = (actual_exit - pos["entry_price"]) * pos["shares"]
        else:
            actual_exit = exit_price * (1 + self.slippage_pct)
            pnl = (pos["entry_price"] - actual_exit) * pos["shares"]

        # Commission at exit (percentage-based)
        notional_exit = pos["shares"] * actual_exit
        commission_exit = notional_exit * self.commission_pct
        total_commission = pos["commission_entry"] + commission_exit

        entry_date = pos["entry_time"].date() if hasattr(pos["entry_time"], "date") else pos["entry_time"]

        self.trades.append({
            "ticker": pos["ticker"],
            "date": entry_date,
            "direction": pos["action"],
            "entry_price": round(pos["entry_price"], 4),
            "exit_price": round(actual_exit, 4),
            "shares": pos["shares"],
            "pnl": round(pnl, 2),
            "commission": round(total_commission, 2),
            "net_pnl": round(pnl - total_commission, 2),
            "entry_time": pos["entry_time"],
            "exit_time": exit_time,
            "exit_reason": reason,
            "cost_pct": round(total_commission / (pos["shares"] * pos["entry_price"]) * 100, 3),
            **pos.get("metadata", {}),
        })

        self.capital += (pnl - total_commission)
