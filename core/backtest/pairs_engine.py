"""
Pairs Trading Backtest Engine — long/short simultané sur deux actifs.

Approche :
  - Dollar-neutral : même montant $ dans chaque jambe
  - Signal : z-score du spread (shift(1) = no lookahead)
  - Exécution : open[t+1] après signal sur close[t]
  - Mark-to-market quotidien : equity curve reflète les positions ouvertes
  - Coûts : cost_bps sur chaque jambe × 2 (entrée + sortie)

Notations :
  "long_a_short_b"  → long A, short B (spread trop bas, on attend mean-reversion upward)
  "short_a_long_b"  → short A, long B (spread trop haut, on attend mean-reversion downward)

Usage :
    engine = PairsBacktestEngine(initial_capital=100_000)
    result = engine.run(data_aapl, data_msft, pair_stats)
    print(result.summary())
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.data.pairs import PairStats, compute_spread

# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class PairTrade:
    """Un cycle complet d'ouverture + fermeture de la paire."""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str           # "long_a_short_b" | "short_a_long_b"
    entry_price_a: float
    entry_price_b: float
    exit_price_a: float
    exit_price_b: float
    size_a: float            # nb shares A
    size_b: float            # nb shares B
    notional: float          # $ par jambe
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str         # "zscore_cross" | "stop_loss" | "timeout" | "end_of_data"
    bars_held: int
    entry_zscore: float
    exit_zscore: float


@dataclass
class PairsBacktestResult:
    """Résultat étendu — to_dict() compatible avec StrategyRanker."""
    pair_id: str
    sector: str
    start_date: str
    end_date: str
    n_obs: int
    hedge_ratio: float
    half_life_days: float

    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    avg_holding_days: float = 0.0
    total_costs: float = 0.0
    expectancy: float = 0.0

    trades: list[PairTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    passes_validation: bool = False

    def to_dict(self) -> dict:
        return {
            "strategy_id":       self.pair_id,
            "pair_id":           self.pair_id,
            "sector":            self.sector,
            "asset":             self.pair_id,
            "sharpe_ratio":      self.sharpe_ratio,
            "max_drawdown_pct":  self.max_drawdown_pct,
            "win_rate_pct":      self.win_rate_pct,
            "profit_factor":     self.profit_factor,
            "total_trades":      self.total_trades,
            "total_return_pct":  self.total_return_pct,
            "avg_holding_days":  self.avg_holding_days,
            "total_costs":       self.total_costs,
            "expectancy":        self.expectancy,
            "passes_validation": self.passes_validation,
            "equity_curve":      self.equity_curve,
            "hedge_ratio":       self.hedge_ratio,
            "half_life_days":    self.half_life_days,
            "start_date":        self.start_date,
            "end_date":          self.end_date,
        }

    def summary(self) -> str:
        lines = [
            f"\n{'='*65}",
            f"  PAIRS BACKTEST : {self.pair_id}",
            f"{'='*65}",
            f"  Période     : {self.start_date} → {self.end_date} ({self.n_obs} barres)",
            f"  Hedge ratio : {self.hedge_ratio:.4f}  |  Half-life : {self.half_life_days:.1f}j",
            f"  Return      : {self.total_return_pct:+.2f}%  ({self.annualized_return_pct:+.1f}% ann.)",
            f"  Sharpe      : {self.sharpe_ratio:+.3f}",
            f"  Max DD      : {self.max_drawdown_pct:.1f}%",
            f"  Win Rate    : {self.win_rate_pct:.1f}%  |  PF : {self.profit_factor:.2f}",
            f"  Trades      : {self.total_trades}  |  Avg hold : {self.avg_holding_days:.1f}j",
            f"  Coûts total : {self.total_costs:.2f}$",
            f"  Expectancy  : {self.expectancy:+.4f}",
            f"{'='*65}",
        ]
        return "\n".join(lines)


# ─── Moteur ───────────────────────────────────────────────────────────────────

class PairsBacktestEngine:
    """
    Backteste une stratégie de pairs trading long/short sur données journalières.
    """

    PERIODS_PER_YEAR = 252

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        stop_zscore: float = 4.0,
        zscore_window: int = 60,
        position_pct: float = 0.40,
        max_holding_days: int = 60,
        cost_bps: float = 5.0,
    ):
        """
        initial_capital   : capital total ($)
        entry_zscore      : seuil d'entrée (|z| > entry_zscore)
        exit_zscore       : seuil de sortie (|z| < exit_zscore)
        stop_zscore       : stop-loss si |z| > stop_zscore (spread s'aggrave)
        zscore_window     : fenêtre rolling pour le z-score live (barres)
        position_pct      : fraction du capital par jambe (0.40 = 40% long + 40% short)
        max_holding_days  : durée max de détention (barres)
        cost_bps          : frais par passage en basis points (ex: 5 = 0.05%)
        """
        self.initial_capital = initial_capital
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_zscore = stop_zscore
        self.zscore_window = zscore_window
        self.position_pct = position_pct
        self.max_holding_days = max_holding_days
        self.cost_bps = cost_bps / 10_000

    def run(self, data_a, data_b, pair_stats: PairStats) -> PairsBacktestResult:
        """
        data_a, data_b : OHLCVData (même timezone UTC)
        pair_stats     : statistiques pré-calculées (hedge_ratio, ols_alpha)
        """
        # Aligner les deux séries sur les dates communes
        df_a, df_b = self._align(data_a, data_b)

        if len(df_a) < self.zscore_window + 20:
            raise ValueError(
                f"Pas assez de données ({len(df_a)} barres) pour zscore_window={self.zscore_window}"
            )

        # Spread et z-score (shift(1) intégré pour no-lookahead)
        log_a = np.log(df_a["close"].values)
        log_b = np.log(df_b["close"].values)
        spread_vals = compute_spread(log_a, log_b, pair_stats.hedge_ratio, pair_stats.ols_alpha)

        spread = pd.Series(spread_vals, index=df_a.index)
        roll_mean = spread.rolling(self.zscore_window).mean()
        roll_std = spread.rolling(self.zscore_window).std().replace(0, np.nan)
        zscore_raw = (spread - roll_mean) / roll_std
        zscore = zscore_raw.shift(1)  # No-lookahead : signal basé sur close[t-1]

        # Simulation
        trades, equity = self._simulate(df_a, df_b, zscore)

        return self._build_result(trades, equity, pair_stats, df_a)

    # ─── Internals ────────────────────────────────────────────────────────────

    def _align(self, data_a, data_b) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Inner join sur l'index DatetimeIndex (gère les jours fériés différents)."""
        close_a = data_a.df[["open", "close"]]
        close_b = data_b.df[["open", "close"]]
        common_idx = close_a.index.intersection(close_b.index)
        return close_a.loc[common_idx], close_b.loc[common_idx]

    def _simulate(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        zscore: pd.Series,
    ) -> tuple[list[PairTrade], pd.Series]:
        """Simulation bar-by-bar avec mark-to-market quotidien."""
        n = len(df_a)
        capital = self.initial_capital
        position = None
        trades = []
        equity_list = []

        for i in range(n - 1):  # -1 : on a besoin de open[i+1]
            z = zscore.iloc[i]
            z_prev = zscore.iloc[i - 1] if i > 0 else float("nan")

            open_a_next = df_a["open"].iloc[i + 1]
            open_b_next = df_b["open"].iloc[i + 1]
            close_a_cur = df_a["close"].iloc[i]
            close_b_cur = df_b["close"].iloc[i]

            if np.isnan(z):
                equity_list.append(capital)
                continue

            if position is None:
                # ── Entrée : croisement du seuil ──────────────────────────
                z_ok = not np.isnan(z_prev)
                if z_ok and z < -self.entry_zscore and z_prev >= -self.entry_zscore:
                    # Spread trop bas → LONG A, SHORT B
                    position = self._open_position(
                        "long_a_short_b", capital, i + 1,
                        open_a_next, open_b_next, z,
                    )
                elif z_ok and z > self.entry_zscore and z_prev <= self.entry_zscore:
                    # Spread trop haut → SHORT A, LONG B
                    position = self._open_position(
                        "short_a_long_b", capital, i + 1,
                        open_a_next, open_b_next, z,
                    )

            else:
                # ── Sortie ────────────────────────────────────────────────
                direction = position["direction"]
                holding = i - position["entry_bar"] + 1
                exit_reason = None

                if direction == "long_a_short_b":
                    if z >= -self.exit_zscore:
                        exit_reason = "zscore_cross"
                    elif z < -self.stop_zscore:
                        exit_reason = "stop_loss"
                else:  # short_a_long_b
                    if z <= self.exit_zscore:
                        exit_reason = "zscore_cross"
                    elif z > self.stop_zscore:
                        exit_reason = "stop_loss"

                if holding >= self.max_holding_days:
                    exit_reason = "timeout"

                if exit_reason:
                    # Fermer à open[i+1]
                    trade = self._close_position(
                        position, i + 1, df_a.index[i + 1],
                        open_a_next, open_b_next, z, exit_reason,
                    )
                    capital += trade.net_pnl
                    trades.append(trade)
                    position = None

            # ── Equity mark-to-market ──────────────────────────────────
            if position is not None:
                unrealized = self._compute_pnl(position, close_a_cur, close_b_cur)
                equity_list.append(capital + unrealized)
            else:
                equity_list.append(capital)

        # Fermeture forcée en fin de données
        if position is not None:
            last_close_a = df_a["close"].iloc[-1]
            last_close_b = df_b["close"].iloc[-1]
            trade = self._close_position(
                position, n - 1, df_a.index[-1],
                last_close_a, last_close_b,
                zscore.iloc[-1] if not np.isnan(zscore.iloc[-1]) else 0.0,
                "end_of_data",
            )
            capital += trade.net_pnl
            trades.append(trade)
            equity_list.append(capital)

        idx = df_a.index[:len(equity_list)]
        equity = pd.Series(equity_list, index=idx, dtype=float)
        return trades, equity

    def _open_position(
        self,
        direction: str,
        capital: float,
        entry_bar: int,
        open_a: float,
        open_b: float,
        z: float,
    ) -> dict:
        notional = capital * self.position_pct
        return {
            "direction":     direction,
            "entry_bar":     entry_bar,
            "entry_price_a": open_a,
            "entry_price_b": open_b,
            "size_a":        notional / open_a,
            "size_b":        notional / open_b,
            "notional":      notional,
            "entry_z":       z,
            "cost_entry":    2 * notional * self.cost_bps,
        }

    def _close_position(
        self,
        position: dict,
        exit_bar: int,
        exit_time: pd.Timestamp,
        exit_a: float,
        exit_b: float,
        z: float,
        reason: str,
    ) -> PairTrade:
        gross = self._compute_pnl(position, exit_a, exit_b)
        cost_exit = 2 * position["notional"] * self.cost_bps
        net = gross - position["cost_entry"] - cost_exit
        return PairTrade(
            entry_time=exit_time - pd.Timedelta(
                days=exit_bar - position["entry_bar"]
            ),
            exit_time=exit_time,
            direction=position["direction"],
            entry_price_a=position["entry_price_a"],
            entry_price_b=position["entry_price_b"],
            exit_price_a=exit_a,
            exit_price_b=exit_b,
            size_a=position["size_a"],
            size_b=position["size_b"],
            notional=position["notional"],
            gross_pnl=round(gross, 4),
            costs=round(position["cost_entry"] + cost_exit, 4),
            net_pnl=round(net, 4),
            exit_reason=reason,
            bars_held=exit_bar - position["entry_bar"],
            entry_zscore=round(position["entry_z"], 4),
            exit_zscore=round(z, 4),
        )

    def _compute_pnl(self, position: dict, price_a: float, price_b: float) -> float:
        """P&L brut depuis l'entrée jusqu'au prix actuel."""
        size_a = position["size_a"]
        size_b = position["size_b"]
        ea = position["entry_price_a"]
        eb = position["entry_price_b"]
        if position["direction"] == "long_a_short_b":
            return (price_a - ea) * size_a + (eb - price_b) * size_b
        else:
            return (ea - price_a) * size_a + (price_b - eb) * size_b

    def _build_result(
        self,
        trades: list[PairTrade],
        equity: pd.Series,
        pair_stats: PairStats,
        df_a: pd.DataFrame,
    ) -> PairsBacktestResult:
        pair_id = f"{pair_stats.symbol_a}_{pair_stats.symbol_b}"
        n = len(equity)

        # Rendements daily
        returns = equity.pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            sharpe = float(returns.mean() / returns.std() * np.sqrt(self.PERIODS_PER_YEAR))
        else:
            sharpe = 0.0

        # Max drawdown
        rolling_max = equity.cummax()
        dd = (equity - rolling_max) / rolling_max * 100
        max_dd = float(abs(dd.min())) if len(dd) > 0 else 0.0

        # Trades stats
        pnls = [t.net_pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = gross_win / gross_loss if gross_loss > 0 else (
            float("inf") if gross_win > 0 else 0.0
        )
        avg_hold = float(np.mean([t.bars_held for t in trades])) if trades else 0.0
        total_costs = sum(t.costs for t in trades)

        # Return
        initial = self.initial_capital
        final = float(equity.iloc[-1]) if len(equity) > 0 else initial
        total_ret = (final / initial - 1) * 100
        n_years = n / self.PERIODS_PER_YEAR
        ann_ret = ((final / initial) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0.0

        # Expectancy
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(abs(np.mean(losses))) if losses else 0.0
        wr = win_rate / 100
        expectancy = avg_win * wr - avg_loss * (1 - wr) if pnls else 0.0

        # Validation basique
        passes = (sharpe > 0.5 and max_dd < 20.0 and len(trades) >= 10 and pf > 1.1)

        return PairsBacktestResult(
            pair_id=pair_id,
            sector=pair_stats.sector,
            start_date=str(df_a.index[0].date()),
            end_date=str(df_a.index[-1].date()),
            n_obs=n,
            hedge_ratio=pair_stats.hedge_ratio,
            half_life_days=pair_stats.half_life_days,
            sharpe_ratio=round(sharpe, 3),
            max_drawdown_pct=round(max_dd, 2),
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(pf, 3),
            total_trades=len(trades),
            total_return_pct=round(total_ret, 2),
            annualized_return_pct=round(ann_ret, 2),
            avg_holding_days=round(avg_hold, 1),
            total_costs=round(total_costs, 2),
            expectancy=round(expectancy, 4),
            trades=trades,
            equity_curve=equity,
            passes_validation=passes,
        )
