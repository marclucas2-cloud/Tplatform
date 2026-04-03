"""
Moteur de backtest générique.
Chaque stratégie hérite de BaseStrategy et implémente generate_signals().
"""
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import time as dt_time
from typing import Optional
import config


class Signal:
    """Représente un signal de trading."""
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


class BaseStrategy(ABC):
    """Classe abstraite — chaque stratégie implémente generate_signals()."""

    name: str = "BaseStrategy"

    @abstractmethod
    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Pour un jour donné, retourne une liste de signaux.
        data: {ticker: DataFrame intraday du jour}
        """
        pass

    def get_required_tickers(self) -> list[str]:
        """Retourne les tickers nécessaires pour cette stratégie.
        Override dans les sous-classes. Par défaut, retourne les tickers principaux."""
        from universe import PERMANENT_TICKERS, SECTOR_MAP
        tickers = list(PERMANENT_TICKERS)
        for components in SECTOR_MAP.values():
            tickers.extend(components[:3])
        return list(set(tickers))


class BacktestEngine:
    """
    Moteur de backtest événementiel.
    Simule l'exécution barre par barre avec gestion des ordres.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = config.INITIAL_CAPITAL,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades: list[dict] = []
        self.open_positions: list[dict] = []

    def run(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Exécute le backtest sur toutes les données.
        data: {ticker: DataFrame avec toutes les barres intraday}
        """
        print(f"\n[BACKTEST] Running: {self.strategy.name}")
        print(f"  Capital: ${self.initial_capital:,.0f}")
        print(f"  Tickers: {list(data.keys())}")

        # Récupérer toutes les dates de trading
        all_dates = set()
        for ticker_df in data.values():
            all_dates.update(ticker_df.index.date)
        all_dates = sorted(all_dates)

        print(f"  Trading days: {len(all_dates)}")

        for date in all_dates:
            # Extraire les données du jour pour chaque ticker
            day_data = {}
            for ticker, df in data.items():
                day_df = df[df.index.date == date]
                if not day_df.empty:
                    day_data[ticker] = day_df

            if not day_data:
                continue

            # Générer les signaux
            try:
                signals = self.strategy.generate_signals(day_data, date)
            except Exception as e:
                print(f"  [ERROR] {date}: {e}")
                continue

            # Exécuter les signaux (avec guard horaires)
            earliest = dt_time(9, 35)
            latest = dt_time(15, 55)
            for signal in signals:
                if len(self.open_positions) >= config.MAX_SIMULTANEOUS:
                    break
                # Guard : rejeter tout signal hors 9:35-15:55 ET
                sig_time = signal.timestamp.time() if hasattr(signal.timestamp, 'time') else None
                if sig_time and (sig_time < earliest or sig_time >= latest):
                    continue
                self._open_position(signal, day_data)

            # Simuler le reste de la journée — vérifier stops/targets
            self._simulate_day(day_data, date)

            # Fermeture forcée fin de journée
            self._force_close_all(day_data, date)

        # Construire le DataFrame des trades
        trades_df = pd.DataFrame(self.trades) if self.trades else pd.DataFrame()
        print(f"  Total trades: {len(trades_df)}")

        return trades_df

    def _open_position(self, signal: Signal, day_data: dict):
        """Ouvre une position basée sur un signal."""
        # Position sizing : max 5% du capital
        max_dollars = self.capital * config.MAX_POSITION_PCT
        shares = int(max_dollars / signal.entry_price)

        if shares < 1:
            return

        # Slippage
        if signal.action == "LONG":
            actual_entry = signal.entry_price * (1 + config.SLIPPAGE_PCT)
        else:
            actual_entry = signal.entry_price * (1 - config.SLIPPAGE_PCT)

        commission = shares * config.COMMISSION_PER_SHARE

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
        """Simule barre par barre pour vérifier stops et targets (jusqu'à 15:55 ET)."""
        closed = []
        latest_time = dt_time(15, 55)

        for pos in self.open_positions:
            ticker = pos["ticker"]
            if ticker not in day_data:
                continue

            df = day_data[ticker]
            # Ne regarder que les barres après l'entrée et avant 15:55
            bars_after = df[(df.index > pos["entry_time"]) & (df.index.time <= latest_time)]

            for ts, bar in bars_after.iterrows():
                # Vérifier stop-loss
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
        """Ferme toutes les positions ouvertes à 15:55 ET (ou dernière barre si pas de 15:55)."""
        close_time = dt_time(15, 55)
        for pos in self.open_positions[:]:
            ticker = pos["ticker"]
            if ticker in day_data:
                df = day_data[ticker]
                if not df.empty:
                    # Chercher la barre à 15:55 ou la dernière barre avant 15:55
                    eod_bars = df[df.index.time <= close_time]
                    if not eod_bars.empty:
                        last_bar = eod_bars.iloc[-1]
                    else:
                        last_bar = df.iloc[-1]
                    self._close_position(pos, last_bar["close"], eod_bars.index[-1] if not eod_bars.empty else df.index[-1], "eod_close")
            self.open_positions.remove(pos)

    def _close_position(self, pos: dict, exit_price: float, exit_time, reason: str):
        """Ferme une position et enregistre le trade."""
        # Slippage à la sortie
        if pos["action"] == "LONG":
            actual_exit = exit_price * (1 - config.SLIPPAGE_PCT)
            pnl = (actual_exit - pos["entry_price"]) * pos["shares"]
        else:
            actual_exit = exit_price * (1 + config.SLIPPAGE_PCT)
            pnl = (pos["entry_price"] - actual_exit) * pos["shares"]

        commission = pos["commission_entry"] + pos["shares"] * config.COMMISSION_PER_SHARE

        self.trades.append({
            "ticker": pos["ticker"],
            "date": pos["entry_time"].date() if hasattr(pos["entry_time"], "date") else pos["entry_time"],
            "direction": pos["action"],
            "entry_price": round(pos["entry_price"], 4),
            "exit_price": round(actual_exit, 4),
            "shares": pos["shares"],
            "pnl": round(pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(pnl - commission, 2),
            "entry_time": pos["entry_time"],
            "exit_time": exit_time,
            "exit_reason": reason,
            **pos.get("metadata", {}),
        })

        self.capital += (pnl - commission)
