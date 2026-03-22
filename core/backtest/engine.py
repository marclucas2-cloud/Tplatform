"""
Moteur de backtest déterministe et vectorisé.

RÈGLES ABSOLUES (ne jamais violer) :
  1. No lookahead bias : signal calculé sur close[t], ordre exécuté à open[t+1]
  2. Coûts réels : spread + slippage appliqués sur chaque entrée ET sortie
  3. Zéro LLM : ce module est du pur calcul numérique — aucun appel externe
  4. Reproductibilité : seed fixé, fingerprint des données loggé

Architecture :
  BacktestEngine.run(data, strategy) → BacktestResult
    ↓
  _compute_indicators()   # pandas vectorisé, shift(1) obligatoire
    ↓
  _generate_signals()     # 1=long, -1=short, 0=neutre
    ↓
  _simulate_trades()      # boucle position par position avec coûts
    ↓
  _compute_metrics()      # Sharpe, drawdown, profit factor, etc.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from core.data.loader import OHLCVData

logger = logging.getLogger(__name__)


# ─── Structures de données ──────────────────────────────────────────────────

@dataclass
class Trade:
    """Représente un trade individuel avec tous ses attributs."""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str              # "long" ou "short"
    entry_price: float
    exit_price: float
    size: float                 # En unités (lots, contrats, etc.)
    gross_pnl: float
    costs: float                # Spread + slippage total
    net_pnl: float
    exit_reason: str            # "signal", "stop_loss", "take_profit", "end_of_data"
    bars_held: int

    @property
    def return_pct(self) -> float:
        return self.net_pnl / (self.entry_price * self.size) if self.entry_price else 0.0


@dataclass
class BacktestResult:
    """Résultats complets d'un backtest — loggés et versionnés."""
    strategy_id: str
    strategy_fingerprint: str
    data_fingerprint: str
    asset: str
    timeframe: str
    start_date: str
    end_date: str

    # Métriques clés
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate_pct: float = 0.0
    total_trades: int = 0
    avg_trade_pnl: float = 0.0
    avg_bars_held: float = 0.0
    total_costs: float = 0.0

    # Données détaillées
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)

    # Validation
    passes_validation: bool = False
    validation_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Sérialisation pour logging et stockage."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_fingerprint": self.strategy_fingerprint,
            "data_fingerprint": self.data_fingerprint,
            "asset": self.asset,
            "timeframe": self.timeframe,
            "period": f"{self.start_date} → {self.end_date}",
            "total_return_pct": round(self.total_return_pct, 4),
            "annualized_return_pct": round(self.annualized_return_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "profit_factor": round(self.profit_factor, 4),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "total_trades": self.total_trades,
            "total_costs": round(self.total_costs, 6),
            "passes_validation": self.passes_validation,
            "validation_failures": self.validation_failures,
        }

    def summary(self) -> str:
        """Résumé texte pour logging."""
        status = "[VALIDEE]" if self.passes_validation else "[REJETEE]"
        lines = [
            f"\n{'='*60}",
            f"  BACKTEST : {self.strategy_id}  {status}",
            f"{'='*60}",
            f"  Actif      : {self.asset} {self.timeframe}",
            f"  Periode    : {self.start_date} -> {self.end_date}",
            f"  Trades     : {self.total_trades}",
            f"  Return     : {self.total_return_pct:+.2f}%",
            f"  Sharpe     : {self.sharpe_ratio:.3f}",
            f"  Max DD     : {self.max_drawdown_pct:.2f}%",
            f"  Win rate   : {self.win_rate_pct:.1f}%",
            f"  Profit f.  : {self.profit_factor:.3f}",
            f"  Couts tot. : {self.total_costs:.6f}",
        ]
        if self.validation_failures:
            lines.append(f"  Echecs     : {', '.join(self.validation_failures)}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


# ─── Registre des stratégies ────────────────────────────────────────────────

# Clé : strategy_id prefix (ex "rsi_"). Valeur : fonction qui prend (df, params)
# et retourne un DataFrame avec colonnes signal_long, signal_short
_STRATEGY_REGISTRY: dict[str, Callable] = {}


def register_strategy(prefix: str):
    """Décorateur pour enregistrer une fonction de stratégie."""
    def decorator(fn: Callable):
        _STRATEGY_REGISTRY[prefix] = fn
        return fn
    return decorator


# ─── Moteur principal ───────────────────────────────────────────────────────

class BacktestEngine:
    """
    Moteur de backtest vectorisé — stateless, thread-safe.

    Exemple d'utilisation :
        engine = BacktestEngine(initial_capital=10_000)
        result = engine.run(ohlcv_data, strategy_dict)
        print(result.summary())
    """

    def __init__(self, initial_capital: float = 10_000.0, pip_value: float = 0.0001):
        self.initial_capital = initial_capital
        self.pip_value = pip_value  # Valeur d'un pip (0.0001 pour EUR/USD)

    def run(self, data: OHLCVData, strategy: dict) -> BacktestResult:
        """
        Point d'entrée principal du backtest.
        data     : données OHLCV validées
        strategy : dict validé par StrategyValidator
        """
        logger.info(f"Backtest démarré : {strategy['strategy_id']} sur {data.n_bars} bougies")

        df = data.df.copy()
        params = strategy["parameters"]
        cost_model = strategy["cost_model"]
        strategy_id = strategy["strategy_id"]

        # Trouver la fonction de stratégie
        strategy_fn = self._resolve_strategy(strategy_id)
        if strategy_fn is None:
            raise ValueError(
                f"Aucune stratégie enregistrée pour '{strategy_id}'. "
                f"Enregistrées : {list(_STRATEGY_REGISTRY.keys())}"
            )

        # 1. Calculer les indicateurs (shift(1) appliqué DANS la fonction)
        df = strategy_fn(df, params)

        # 2. Simuler les trades avec coûts réels
        trades, equity = self._simulate_trades(df, params, cost_model)

        # 3. Calculer les métriques
        result = self._compute_metrics(
            trades=trades,
            equity=equity,
            strategy=strategy,
            data=data,
        )

        logger.info(result.summary())
        return result

    def _resolve_strategy(self, strategy_id: str) -> Callable | None:
        """Trouve la fonction de stratégie dans le registre par préfixe."""
        for prefix, fn in _STRATEGY_REGISTRY.items():
            if strategy_id.startswith(prefix):
                return fn
        return None

    def _simulate_trades(self, df: pd.DataFrame, params: dict,
                         cost_model: dict) -> tuple[list[Trade], pd.Series]:
        """
        Simulation position par position.

        NO LOOKAHEAD : signal[t] → ordre exécuté à open[t+1]
        Coûts : spread + slippage appliqués à l'entrée ET à la sortie.
        """
        spread = cost_model["spread_pips"] * self.pip_value
        slippage = cost_model["slippage_pips"] * self.pip_value
        total_cost_per_side = spread / 2 + slippage  # Coût par côté (entrée ou sortie)

        stop_loss_pct = params.get("stop_loss_pct", 0.5) / 100
        take_profit_pct = params.get("take_profit_pct", 1.0) / 100
        position_size = params.get("max_position_pct", 0.02)  # fraction du capital

        trades: list[Trade] = []
        capital = self.initial_capital
        equity_values = [capital]
        equity_times = [df.index[0]]

        position = None  # None = pas de position ouverte

        for i in range(len(df) - 1):
            row = df.iloc[i]
            next_row = df.iloc[i + 1]

            # Signal calculé sur bougie[i], exécution à open[i+1]
            signal_long = row.get("signal_long", False)
            signal_short = row.get("signal_short", False)

            if position is None:
                # Entrée en position
                if signal_long:
                    entry_price = next_row["open"] + total_cost_per_side
                    size = (capital * position_size) / entry_price
                    position = {
                        "direction": "long",
                        "entry_time": next_row.name,
                        "entry_price": entry_price,
                        "size": size,
                        "entry_bar": i + 1,
                        "stop": entry_price * (1 - stop_loss_pct),
                        "target": entry_price * (1 + take_profit_pct),
                    }
                elif signal_short:
                    entry_price = next_row["open"] - total_cost_per_side
                    size = (capital * position_size) / entry_price
                    position = {
                        "direction": "short",
                        "entry_time": next_row.name,
                        "entry_price": entry_price,
                        "size": size,
                        "entry_bar": i + 1,
                        "stop": entry_price * (1 + stop_loss_pct),
                        "target": entry_price * (1 - take_profit_pct),
                    }
            else:
                # Vérifier conditions de sortie
                exit_reason = None
                exit_price = next_row["open"]

                if position["direction"] == "long":
                    if next_row["low"] <= position["stop"]:
                        exit_price = position["stop"]
                        exit_reason = "stop_loss"
                    elif next_row["high"] >= position["target"]:
                        exit_price = position["target"]
                        exit_reason = "take_profit"
                    elif signal_short:  # Signal inverse = sortie
                        exit_reason = "signal"
                else:  # short
                    if next_row["high"] >= position["stop"]:
                        exit_price = position["stop"]
                        exit_reason = "stop_loss"
                    elif next_row["low"] <= position["target"]:
                        exit_price = position["target"]
                        exit_reason = "take_profit"
                    elif signal_long:
                        exit_reason = "signal"

                if exit_reason:
                    exit_price_with_cost = (
                        exit_price - total_cost_per_side
                        if position["direction"] == "long"
                        else exit_price + total_cost_per_side
                    )
                    if position["direction"] == "long":
                        gross_pnl = (exit_price - position["entry_price"]) * position["size"]
                    else:
                        gross_pnl = (position["entry_price"] - exit_price) * position["size"]

                    costs = total_cost_per_side * 2 * position["size"]  # entrée + sortie
                    net_pnl = gross_pnl - costs
                    capital += net_pnl

                    trade = Trade(
                        entry_time=position["entry_time"],
                        exit_time=next_row.name,
                        direction=position["direction"],
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        size=position["size"],
                        gross_pnl=gross_pnl,
                        costs=costs,
                        net_pnl=net_pnl,
                        exit_reason=exit_reason,
                        bars_held=i + 1 - position["entry_bar"],
                    )
                    trades.append(trade)
                    position = None

            equity_values.append(capital)
            equity_times.append(next_row.name)

        # Fermeture forcée fin de données
        if position is not None:
            last_row = df.iloc[-1]
            exit_price = last_row["close"]
            if position["direction"] == "long":
                gross_pnl = (exit_price - position["entry_price"]) * position["size"]
            else:
                gross_pnl = (position["entry_price"] - exit_price) * position["size"]
            costs = total_cost_per_side * 2 * position["size"]
            net_pnl = gross_pnl - costs
            capital += net_pnl
            trade = Trade(
                entry_time=position["entry_time"],
                exit_time=last_row.name,
                direction=position["direction"],
                entry_price=position["entry_price"],
                exit_price=exit_price,
                size=position["size"],
                gross_pnl=gross_pnl,
                costs=costs,
                net_pnl=net_pnl,
                exit_reason="end_of_data",
                bars_held=len(df) - 1 - position["entry_bar"],
            )
            trades.append(trade)

        equity = pd.Series(equity_values, index=equity_times, name="equity")
        return trades, equity

    def _compute_metrics(self, trades: list[Trade], equity: pd.Series,
                         strategy: dict, data: OHLCVData) -> BacktestResult:
        """Calcule toutes les métriques de performance."""
        req = strategy.get("validation_requirements", {})
        n = len(trades)

        if n == 0:
            result = BacktestResult(
                strategy_id=strategy["strategy_id"],
                strategy_fingerprint=strategy.get("_fingerprint", ""),
                data_fingerprint=data.fingerprint,
                asset=data.asset,
                timeframe=data.timeframe,
                start_date=str(data.df.index[0].date()),
                end_date=str(data.df.index[-1].date()),
                total_trades=0,
                passes_validation=False,
                validation_failures=["Aucun trade généré"],
            )
            return result

        # Calculs de base
        pnls = np.array([t.net_pnl for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]

        total_return = (equity.iloc[-1] - self.initial_capital) / self.initial_capital * 100
        win_rate = len(wins) / n * 100 if n > 0 else 0

        profit_factor = (wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else float("inf")

        # Max drawdown
        rolling_max = equity.cummax()
        drawdown = (equity - rolling_max) / rolling_max * 100
        max_dd = abs(drawdown.min())

        # Sharpe ratio (annualisé, basé sur returns quotidiens de l'equity)
        returns = equity.pct_change().dropna()
        periods_per_year = self._infer_periods_per_year(data.timeframe)
        if returns.std() > 0:
            sharpe = (returns.mean() / returns.std()) * np.sqrt(periods_per_year)
        else:
            sharpe = 0.0

        # Sortino (downside deviation uniquement)
        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = (returns.mean() / downside.std()) * np.sqrt(periods_per_year)
        else:
            sortino = 0.0

        # Return annualisé
        n_years = len(data.df) / periods_per_year
        annualized = ((1 + total_return / 100) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

        total_costs = sum(t.costs for t in trades)

        # Validation contre les critères du schéma
        failures = []
        if n < req.get("min_trades", 0):
            failures.append(f"trades({n}) < min({req['min_trades']})")
        if sharpe < req.get("min_sharpe", 0):
            failures.append(f"sharpe({sharpe:.2f}) < min({req['min_sharpe']})")
        if max_dd > req.get("max_drawdown_pct", 100):
            failures.append(f"drawdown({max_dd:.1f}%) > max({req['max_drawdown_pct']}%)")
        if profit_factor < req.get("min_profit_factor", 1.2):
            failures.append(f"profit_factor({profit_factor:.2f}) < min({req.get('min_profit_factor', 1.2)})")

        return BacktestResult(
            strategy_id=strategy["strategy_id"],
            strategy_fingerprint=strategy.get("_fingerprint", ""),
            data_fingerprint=data.fingerprint,
            asset=data.asset,
            timeframe=data.timeframe,
            start_date=str(data.df.index[0].date()),
            end_date=str(data.df.index[-1].date()),
            total_return_pct=round(total_return, 4),
            annualized_return_pct=round(annualized, 4),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            max_drawdown_pct=round(max_dd, 4),
            profit_factor=round(profit_factor, 4),
            win_rate_pct=round(win_rate, 2),
            total_trades=n,
            avg_trade_pnl=round(float(pnls.mean()), 6),
            avg_bars_held=round(np.mean([t.bars_held for t in trades]), 1),
            total_costs=round(total_costs, 6),
            trades=trades,
            equity_curve=equity,
            passes_validation=len(failures) == 0,
            validation_failures=failures,
        )

    @staticmethod
    def _infer_periods_per_year(timeframe: str) -> float:
        """Nombre de bougies par an selon le timeframe."""
        mapping = {
            "1M": 252 * 24 * 60,
            "5M": 252 * 24 * 12,
            "15M": 252 * 24 * 4,
            "30M": 252 * 24 * 2,
            "1H": 252 * 24,
            "4H": 252 * 6,
            "1D": 252,
            "1W": 52,
        }
        return mapping.get(timeframe, 252)


# ─── Indicateurs techniques (réutilisables) ─────────────────────────────────

def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """
    RSI vectorisé avec méthode Wilder (EMA).
    IMPORTANT : retourne des valeurs alignées sur l'index original.
    Le shift(1) est appliqué APRÈS dans la fonction de signal.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ─── Stratégies enregistrées ────────────────────────────────────────────────

@register_strategy("rsi_")
def rsi_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Stratégie RSI mean-reversion.

    Logique :
      - Signal LONG  : RSI croise à la hausse le seuil oversold (ex 30)
      - Signal SHORT : RSI croise à la baisse le seuil overbought (ex 70)

    NO LOOKAHEAD : les signaux sont basés sur rsi.shift(1) et rsi.shift(2)
    pour s'assurer que la bougie est FERMÉE avant de décider.
    """
    period = int(params.get("rsi_period", 14))
    oversold = params.get("oversold", 30)
    overbought = params.get("overbought", 70)

    # RSI calculé sur close — valeur disponible APRÈS fermeture de la bougie
    rsi = compute_rsi(df["close"], period)

    # Shift(1) : on utilise le RSI de la bougie précédente (bougie fermée)
    # Cela évite tout lookahead : le signal à t utilise close[t-1]
    rsi_prev = rsi.shift(1)
    rsi_prev2 = rsi.shift(2)

    # Croisement haussier : RSI était sous le seuil, maintenant au-dessus
    df = df.copy()
    df["rsi"] = rsi
    df["signal_long"] = (rsi_prev < oversold) & (rsi_prev2 >= oversold)
    df["signal_short"] = (rsi_prev > overbought) & (rsi_prev2 <= overbought)

    return df


@register_strategy("breakout_")
def breakout_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Stratégie breakout sur channel N périodes.

    Logique :
      - Signal LONG  : close dépasse le high des N dernières bougies
      - Signal SHORT : close passe sous le low des N dernières bougies

    NO LOOKAHEAD : channel calculé sur les N bougies PRÉCÉDENTES (.shift(1))
    """
    n = int(params.get("channel_period", 20))

    # Rolling max/min sur les N bougies précédentes (shift pour exclure la bougie courante)
    upper = df["high"].shift(1).rolling(n).max()
    lower = df["low"].shift(1).rolling(n).min()

    df = df.copy()
    df["upper_channel"] = upper
    df["lower_channel"] = lower
    df["signal_long"] = df["close"].shift(1) > upper
    df["signal_short"] = df["close"].shift(1) < lower

    return df
