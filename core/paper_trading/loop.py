"""
Paper Trading Loop — simulateur de trading temps réel.

Simule l'exécution bar-by-bar comme si les données arrivaient en streaming.
Peut fonctionner sur données synthétiques (dev) ou données IG réelles (prod).

Architecture :
  PaperTradingLoop.run(data, strategies) :
    Pour chaque nouvelle bougie (bar) :
      1. Mettre à jour le FeatureStore avec les données disponibles jusqu'à cette bougie
      2. Calculer les signaux (no-lookahead : signal sur bar[t], ordre à bar[t+1])
      3. Évaluer les stops/targets des positions ouvertes
      4. Logguer l'état du portfolio à chaque bar

Différence avec le Backtest Engine :
  - Backtest : vectorisé, toutes les bougies d'un coup, vitesse maximale
  - Paper Loop : barre par barre, simule le temps réel, teste la logique d'exécution

Usage :
  loop = PaperTradingLoop(initial_capital=10_000)
  summary = loop.run(ohlcv_data, [strategy1, strategy2])
  loop.print_report()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from core.data.loader import OHLCVData
from core.features.store import FeatureStore

logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    strategy_id: str
    direction: str          # "long" | "short"
    entry_time: pd.Timestamp
    entry_price: float
    size: float
    stop_price: float
    target_price: float
    deal_id: str


@dataclass
class PaperTrade:
    strategy_id: str
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    size: float
    net_pnl: float
    exit_reason: str


@dataclass
class PaperTradingReport:
    """Rapport final du paper trading."""
    n_bars_processed: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: float = 0.0
    final_capital: float = 0.0
    initial_capital: float = 0.0
    max_drawdown_pct: float = 0.0
    trades: list[PaperTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    strategy_stats: dict[str, dict] = field(default_factory=dict)

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital:
            return (self.final_capital - self.initial_capital) / self.initial_capital * 100
        return 0.0

    @property
    def win_rate_pct(self) -> float:
        return (self.winning_trades / self.total_trades * 100) if self.total_trades else 0.0

    def print(self):
        print(f"\n{'='*55}")
        print(f"  PAPER TRADING REPORT")
        print(f"{'='*55}")
        print(f"  Bars traites   : {self.n_bars_processed}")
        print(f"  Trades total   : {self.total_trades}")
        print(f"  Win rate       : {self.win_rate_pct:.1f}%")
        print(f"  PnL total      : {self.total_pnl:+.4f}")
        print(f"  Return         : {self.total_return_pct:+.2f}%")
        print(f"  Capital final  : {self.final_capital:.2f}")
        print(f"  Max drawdown   : {self.max_drawdown_pct:.2f}%")
        if self.strategy_stats:
            print(f"\n  Par strategie :")
            for sid, stats in self.strategy_stats.items():
                print(f"    {sid:<35} trades={stats['trades']:3d}  pnl={stats['pnl']:+.4f}")
        print(f"{'='*55}")


class PaperTradingLoop:
    """
    Simulateur paper trading bar-by-bar.

    Peut gérer plusieurs stratégies simultanément avec allocation de capital séparée.
    """

    def __init__(self, initial_capital: float = 10_000.0, pip_value: float = 0.0001):
        self.initial_capital = initial_capital
        self.pip_value = pip_value  # DEPRECATED — conserve pour backward-compat
        self._feature_store = FeatureStore()

    def run(self, data: OHLCVData, strategies: list[dict],
            speed: str = "instant", log_every: int = 100) -> PaperTradingReport:
        """
        Execute le paper trading bar-by-bar.

        data       : données OHLCV complètes (simulées ou réelles)
        strategies : liste de dicts stratégie validés
        speed      : "instant" (max vitesse) | "verbose" (log chaque bar)
        log_every  : afficher le portfolio toutes les N barres
        """
        df = data.df
        n = len(df)
        capital = self.initial_capital
        equity_curve = [capital]
        open_positions: dict[str, PaperPosition] = {}  # strategy_id → position
        all_trades: list[PaperTrade] = []
        strategy_stats = {s["strategy_id"]: {"trades": 0, "pnl": 0.0} for s in strategies}

        # Pré-calculer les features pour toutes les stratégies (optimisation)
        strategy_features = {}
        for strat in strategies:
            sid = strat["strategy_id"]
            features = self._required_features(strat)
            if features:
                enriched = self._feature_store.compute(df, features)
            else:
                enriched = df
            strategy_features[sid] = enriched

        logger.info(
            f"Paper trading: {n} barres, {len(strategies)} stratégie(s), "
            f"capital={capital:.0f}"
        )

        for i in range(1, n - 1):  # Démarre à 1 (besoin de la barre précédente)
            bar = df.iloc[i]
            next_bar = df.iloc[i + 1]

            for strat in strategies:
                sid = strat["strategy_id"]
                params = strat["parameters"]
                cost_model = strat["cost_model"]
                enriched = strategy_features[sid]

                # Couts en % du prix (nouveau format) ou pips (ancien format)
                if "spread_pct" in cost_model:
                    def _cps(price):
                        return price * cost_model["spread_pct"] / 200 + price * cost_model["slippage_pct"] / 100
                else:
                    _spread_abs = cost_model["spread_pips"] * self.pip_value
                    _slip_abs = cost_model["slippage_pips"] * self.pip_value
                    def _cps(price):
                        return _spread_abs / 2 + _slip_abs

                stop_pct = params.get("stop_loss_pct", 0.5) / 100
                target_pct = params.get("take_profit_pct", 1.0) / 100
                position_pct = params.get("max_position_pct", 0.02)

                row = enriched.iloc[i]
                signal_long  = bool(row.get("signal_long", False))
                signal_short = bool(row.get("signal_short", False))

                pos = open_positions.get(sid)

                if pos is None:
                    # Entrée en position
                    if signal_long or signal_short:
                        direction = "long" if signal_long else "short"
                        entry_p = next_bar["open"]
                        entry_p += _cps(entry_p) if direction == "long" else -_cps(entry_p)
                        size = (capital * position_pct) / entry_p if entry_p else 0

                        if size > 0:
                            open_positions[sid] = PaperPosition(
                                strategy_id=sid,
                                direction=direction,
                                entry_time=next_bar.name,
                                entry_price=entry_p,
                                size=size,
                                stop_price=entry_p * (1 - stop_pct) if direction == "long" else entry_p * (1 + stop_pct),
                                target_price=entry_p * (1 + target_pct) if direction == "long" else entry_p * (1 - target_pct),
                                deal_id=f"PAPER-{sid[:6].upper()}-{i}",
                            )
                else:
                    # Vérifier sortie
                    exit_reason = None
                    exit_price = next_bar["open"]

                    if pos.direction == "long":
                        if next_bar["low"] <= pos.stop_price:
                            exit_price, exit_reason = pos.stop_price, "stop_loss"
                        elif next_bar["high"] >= pos.target_price:
                            exit_price, exit_reason = pos.target_price, "take_profit"
                        elif signal_short:
                            exit_reason = "signal_reverse"
                    else:
                        if next_bar["high"] >= pos.stop_price:
                            exit_price, exit_reason = pos.stop_price, "stop_loss"
                        elif next_bar["low"] <= pos.target_price:
                            exit_price, exit_reason = pos.target_price, "take_profit"
                        elif signal_long:
                            exit_reason = "signal_reverse"

                    if exit_reason:
                        exit_price -= _cps(exit_price) if pos.direction == "long" else -_cps(exit_price)
                        if pos.direction == "long":
                            gross = (exit_price - pos.entry_price) * pos.size
                        else:
                            gross = (pos.entry_price - exit_price) * pos.size
                        costs = _cps(exit_price) * 2 * pos.size
                        net_pnl = gross - costs
                        capital += net_pnl

                        trade = PaperTrade(
                            strategy_id=sid,
                            direction=pos.direction,
                            entry_time=pos.entry_time,
                            exit_time=next_bar.name,
                            entry_price=pos.entry_price,
                            exit_price=exit_price,
                            size=pos.size,
                            net_pnl=net_pnl,
                            exit_reason=exit_reason,
                        )
                        all_trades.append(trade)
                        strategy_stats[sid]["trades"] += 1
                        strategy_stats[sid]["pnl"] += net_pnl
                        del open_positions[sid]

                        if speed == "verbose":
                            logger.info(
                                f"[{next_bar.name}] {sid} CLOSE {pos.direction.upper()} "
                                f"@ {exit_price:.5f} | PnL={net_pnl:+.4f} | {exit_reason}"
                            )

            equity_curve.append(capital)

            if log_every and i % log_every == 0:
                logger.debug(f"Bar {i}/{n} | capital={capital:.2f} | trades={len(all_trades)}")

        # Fermer les positions ouvertes
        last_bar = df.iloc[-1]
        for sid, pos in open_positions.items():
            exit_price = last_bar["close"]
            if pos.direction == "long":
                net_pnl = (exit_price - pos.entry_price) * pos.size
            else:
                net_pnl = (pos.entry_price - exit_price) * pos.size
            capital += net_pnl
            all_trades.append(PaperTrade(
                strategy_id=sid, direction=pos.direction,
                entry_time=pos.entry_time, exit_time=last_bar.name,
                entry_price=pos.entry_price, exit_price=exit_price,
                size=pos.size, net_pnl=net_pnl, exit_reason="end_of_data",
            ))
            equity_curve.append(capital)

        # Calcul drawdown
        eq = np.array(equity_curve)
        roll_max = np.maximum.accumulate(eq)
        dd = (eq - roll_max) / roll_max * 100
        max_dd = abs(dd.min())

        wins = sum(1 for t in all_trades if t.net_pnl > 0)

        report = PaperTradingReport(
            n_bars_processed=n,
            total_trades=len(all_trades),
            winning_trades=wins,
            total_pnl=sum(t.net_pnl for t in all_trades),
            final_capital=capital,
            initial_capital=self.initial_capital,
            max_drawdown_pct=round(max_dd, 4),
            trades=all_trades,
            equity_curve=equity_curve,
            strategy_stats=strategy_stats,
        )

        logger.info(
            f"Paper trading terminé : {len(all_trades)} trades, "
            f"PnL={report.total_pnl:+.4f}, return={report.total_return_pct:+.2f}%"
        )
        return report

    @staticmethod
    def _required_features(strategy: dict) -> list[str]:
        """Infère les features requises depuis le strategy_id."""
        sid = strategy["strategy_id"]
        params = strategy["parameters"]
        features = []
        if sid.startswith("rsi_"):
            period = int(params.get("rsi_period", 14))
            features.append(f"rsi_{period}")
        if sid.startswith("rsi_filtered_"):
            adx_p = int(params.get("adx_period", 14))
            rsi_p = int(params.get("rsi_period", 14))
            features += [f"rsi_{rsi_p}", f"adx_{adx_p}"]
        if sid.startswith("vwap_"):
            atr_p = int(params.get("atr_period", 14))
            features += ["vwap", f"atr_{atr_p}"]
        if sid.startswith("orb_"):
            features += ["or_high", "or_low", "or_established"]
        return features
