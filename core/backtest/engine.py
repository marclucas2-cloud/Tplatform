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
    expectancy: float = 0.0          # Gain attendu moyen par trade (€)
    rolling_sharpe_std: float = 0.0  # Stabilité : std du Sharpe sur fenêtres glissantes

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
            "expectancy": round(self.expectancy, 6),
            "rolling_sharpe_std": round(self.rolling_sharpe_std, 4),
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
            f"  Expectancy : {self.expectancy:+.4f}",
            f"  Sharpe std : {self.rolling_sharpe_std:.3f}",
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
        self.pip_value = pip_value  # DEPRECATED — conserve pour backward-compat

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
        """Trouve la fonction de stratégie dans le registre par préfixe (plus long match gagne)."""
        best_prefix, best_fn = None, None
        for prefix, fn in _STRATEGY_REGISTRY.items():
            if strategy_id.startswith(prefix):
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix, best_fn = prefix, fn
        return best_fn

    def _simulate_trades(self, df: pd.DataFrame, params: dict,
                         cost_model: dict) -> tuple[list[Trade], pd.Series]:
        """
        Simulation position par position.

        NO LOOKAHEAD : signal[t] → ordre exécuté à open[t+1]
        Coûts : spread + slippage appliqués à l'entrée ET à la sortie.
        """
        # Coûts de transaction — nouveau format spread_pct/slippage_pct (% du prix)
        # Backward-compat : si spread_pips présent, conversion via pip_value
        use_pct = "spread_pct" in cost_model
        if use_pct:
            _spread_pct = cost_model["spread_pct"] / 100
            _slippage_pct = cost_model["slippage_pct"] / 100
        else:
            _spread_abs = cost_model["spread_pips"] * self.pip_value
            _slippage_abs = cost_model["slippage_pips"] * self.pip_value

        def _cost_per_side(price: float) -> float:
            """Cout de transaction par cote (entree ou sortie)."""
            if use_pct:
                return price * _spread_pct / 2 + price * _slippage_pct
            return _spread_abs / 2 + _slippage_abs

        stop_loss_pct = params.get("stop_loss_pct", 0.5) / 100
        take_profit_pct = params.get("take_profit_pct", 1.0) / 100
        trailing_stop_pct = params.get("trailing_stop_pct", 0.0) / 100  # 0 = désactivé
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
                    entry_price = next_row["open"] + _cost_per_side(next_row["open"])
                    size = (capital * position_size) / entry_price
                    position = {
                        "direction": "long",
                        "entry_time": next_row.name,
                        "entry_price": entry_price,
                        "size": size,
                        "entry_bar": i + 1,
                        "stop": entry_price * (1 - stop_loss_pct),
                        "target": entry_price * (1 + take_profit_pct),
                        "high_watermark": entry_price,  # trailing stop
                    }
                elif signal_short:
                    entry_price = next_row["open"] - _cost_per_side(next_row["open"])
                    size = (capital * position_size) / entry_price
                    position = {
                        "direction": "short",
                        "entry_time": next_row.name,
                        "entry_price": entry_price,
                        "size": size,
                        "entry_bar": i + 1,
                        "stop": entry_price * (1 + stop_loss_pct),
                        "target": entry_price * (1 - take_profit_pct),
                        "low_watermark": entry_price,   # trailing stop
                    }
            else:
                # Trailing stop : mise à jour du watermark et du stop dynamique
                if trailing_stop_pct > 0:
                    if position["direction"] == "long":
                        hw = position.get("high_watermark", position["entry_price"])
                        if next_row["high"] > hw:
                            hw = next_row["high"]
                            position["high_watermark"] = hw
                            new_stop = hw * (1 - trailing_stop_pct)
                            if new_stop > position["stop"]:  # stop ne recule jamais
                                position["stop"] = new_stop
                    else:
                        lw = position.get("low_watermark", position["entry_price"])
                        if next_row["low"] < lw:
                            lw = next_row["low"]
                            position["low_watermark"] = lw
                            new_stop = lw * (1 + trailing_stop_pct)
                            if new_stop < position["stop"]:  # stop ne remonte jamais
                                position["stop"] = new_stop

                # Vérifier conditions de sortie
                exit_reason = None
                exit_price = next_row["open"]

                if position["direction"] == "long":
                    if next_row["open"] <= position["stop"]:
                        # Gap down overnight : sortie au prix d'ouverture réel (pire que le stop)
                        exit_price = next_row["open"]
                        exit_reason = "stop_loss"
                    elif next_row["low"] <= position["stop"]:
                        exit_price = position["stop"]
                        exit_reason = "stop_loss"
                    elif next_row["open"] >= position["target"]:
                        # Gap up overnight : plafonné au target (conservateur)
                        exit_price = position["target"]
                        exit_reason = "take_profit"
                    elif next_row["high"] >= position["target"]:
                        exit_price = position["target"]
                        exit_reason = "take_profit"
                    elif signal_short:  # Signal inverse = sortie
                        exit_reason = "signal"
                else:  # short
                    if next_row["open"] >= position["stop"]:
                        # Gap up overnight : sortie au prix d'ouverture réel (pire que le stop)
                        exit_price = next_row["open"]
                        exit_reason = "stop_loss"
                    elif next_row["high"] >= position["stop"]:
                        exit_price = position["stop"]
                        exit_reason = "stop_loss"
                    elif next_row["open"] <= position["target"]:
                        # Gap down overnight : plafonné au target (conservateur)
                        exit_price = position["target"]
                        exit_reason = "take_profit"
                    elif next_row["low"] <= position["target"]:
                        exit_price = position["target"]
                        exit_reason = "take_profit"
                    elif signal_long:
                        exit_reason = "signal"

                if exit_reason:
                    exit_price_with_cost = (
                        exit_price - _cost_per_side(exit_price)
                        if position["direction"] == "long"
                        else exit_price + _cost_per_side(exit_price)
                    )
                    if position["direction"] == "long":
                        gross_pnl = (exit_price - position["entry_price"]) * position["size"]
                    else:
                        gross_pnl = (position["entry_price"] - exit_price) * position["size"]

                    costs = _cost_per_side(exit_price) * 2 * position["size"]  # entrée + sortie
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

            # MTM : inclure le P&L latent si une position est ouverte
            if position is not None:
                current_price = next_row["close"]
                if position["direction"] == "long":
                    unrealized = (current_price - position["entry_price"]) * position["size"]
                else:
                    unrealized = (position["entry_price"] - current_price) * position["size"]
                equity_values.append(capital + unrealized)
            else:
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
            costs = _cost_per_side(exit_price) * 2 * position["size"]
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

        # Expectancy = gain espéré moyen par trade
        avg_win  = float(wins.mean())  if len(wins)   > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        wr = win_rate / 100
        expectancy = avg_win * wr - avg_loss * (1 - wr)

        # Stabilité : std du Sharpe sur fenêtres glissantes (20% des données)
        roll_window = max(50, len(returns) // 5)
        def _rolling_sharpe(x: np.ndarray) -> float:
            s = x.std()
            return (x.mean() / s * np.sqrt(periods_per_year)) if s > 0 else 0.0
        rs = returns.rolling(roll_window).apply(_rolling_sharpe, raw=True).dropna()
        rolling_sharpe_std = float(rs.std()) if len(rs) > 1 else 0.0

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
            expectancy=round(expectancy, 6),
            rolling_sharpe_std=round(rolling_sharpe_std, 4),
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


@register_strategy("rsi_filtered_")
def rsi_filtered_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    RSI Mean Reversion filtré par ADX.

    Logique :
      - Signal RSI classique (oversold/overbought)
      - MAIS uniquement si ADX < adx_threshold (marché ranging, pas trending)

    Le filtre ADX est crucial : évite les faux signaux en tendance forte.
    ADX < 20 = ranging → mean reversion viable
    ADX > 25 = trending → ignorer les signaux RSI
    """
    from core.features.store import FeatureStore

    period      = int(params.get("rsi_period", 14))
    oversold    = params.get("oversold", 30)
    overbought  = params.get("overbought", 70)
    adx_period  = int(params.get("adx_period", 14))
    adx_thresh  = params.get("adx_threshold", 25)

    fs = FeatureStore()
    enriched = fs.compute(df, [f"rsi_{period}", f"adx_{adx_period}"])

    rsi = enriched[f"rsi_{period}"]       # déjà shifté de 1 par le FeatureStore
    rsi_prev = rsi.shift(1)               # shift supplémentaire pour croisement
    adx = enriched[f"adx_{adx_period}"]   # déjà shifté de 1

    ranging = adx < adx_thresh

    df = df.copy()
    df["rsi"] = rsi
    df["adx"] = adx
    df["signal_long"]  = ranging & (rsi < oversold)  & (rsi_prev >= oversold)
    df["signal_short"] = ranging & (rsi > overbought) & (rsi_prev <= overbought)

    return df


@register_strategy("vwap_")
def vwap_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    VWAP Mean Reversion.

    Logique :
      - Prix s'éloigne du VWAP de plus de N écarts-types → retour probable
      - Signal LONG  : close < VWAP - n_std * ATR  (prix trop bas vs VWAP)
      - Signal SHORT : close > VWAP + n_std * ATR  (prix trop haut vs VWAP)
      - Sortie : retour vers VWAP (écart < exit_std * ATR)

    Pourquoi ça marche :
      Les market makers utilisent le VWAP comme référence de prix équitable.
      Les déviations importantes génèrent un flow de retour vers la moyenne.
    """
    from core.features.store import FeatureStore

    atr_period       = int(params.get("atr_period", 14))
    n_std            = params.get("entry_std", 1.5)      # écart pour entrée
    exit_std         = params.get("exit_std", 0.3)       # écart pour sortie
    use_sigma_bands  = int(params.get("use_sigma_bands", 0))  # 0=ATR, 1=σ rolling std
    sigma_period     = int(params.get("sigma_period", 20))

    fs = FeatureStore()
    features = ["vwap", f"atr_{atr_period}"]
    enriched = fs.compute(df, features)

    vwap = enriched["vwap"]
    atr  = enriched[f"atr_{atr_period}"]
    close_prev = df["close"].shift(1)  # close de la bougie fermée

    # Bandes : ATR (classique) ou écart-type glissant (σ bands)
    if use_sigma_bands:
        sigma = df["close"].rolling(sigma_period).std().shift(1)
        band_unit = sigma.fillna(atr)  # fallback ATR si sigma NaN
    else:
        band_unit = atr

    deviation = close_prev - vwap
    band = n_std * band_unit

    df = df.copy()
    df["vwap"]      = vwap
    df["atr"]       = atr
    df["deviation"] = deviation
    df["signal_long"]  = deviation < -band          # Prix trop bas → long
    df["signal_short"] = deviation > band            # Prix trop haut → short
    # Signal de sortie : retour vers VWAP
    df["signal_exit_long"]  = deviation > -exit_std * band_unit
    df["signal_exit_short"] = deviation < exit_std * band_unit

    return df


@register_strategy("orb_")
def orb_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Opening Range Breakout (ORB).

    Logique :
      - Établir le range des premières N minutes de la session
      - Signal LONG  : close dépasse or_high avec momentum (volume élevé)
      - Signal SHORT : close passe sous or_low avec momentum
      - Sortie : trailing stop ou fin de session

    Excellent sur indices (DAX, SP500) et instruments liquides en ouverture.
    """
    from core.features.store import FeatureStore

    volume_mult = params.get("volume_multiplier", 1.5)  # volume > N * volume moyen
    vol_lookback = int(params.get("volume_lookback", 20))

    fs = FeatureStore()
    enriched = fs.compute(df, ["or_high", "or_low", "or_established"])

    or_high       = enriched["or_high"]
    or_low        = enriched["or_low"]
    established   = enriched["or_established"]
    close_prev    = df["close"].shift(1)

    # Filtre volume : breakout valide uniquement si volume élevé
    vol_avg = df["volume"].rolling(vol_lookback).mean().shift(1)
    vol_prev = df["volume"].shift(1)
    high_volume = vol_prev > volume_mult * vol_avg

    df = df.copy()
    df["or_high"]        = or_high
    df["or_low"]         = or_low
    df["or_established"] = established
    df["signal_long"]    = (established > 0) & (close_prev > or_high) & high_volume
    df["signal_short"]   = (established > 0) & (close_prev < or_low)  & high_volume

    return df


@register_strategy("bb_squeeze_")
def bb_squeeze_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Bollinger Band Squeeze — Breakout après compression de volatilité.

    Logique :
      - Squeeze  : BB width < sa moyenne mobile N périodes (marché qui coile)
      - Breakout : BB width repasse au-dessus de la moyenne (expansion)
      - Direction : déterminée par le momentum (EMA rapide vs EMA lente)

      Signal LONG  : sortie de squeeze + momentum haussier (ema_fast > ema_slow)
      Signal SHORT : sortie de squeeze + momentum baissier (ema_fast < ema_slow)

    Pourquoi ça marche :
      La compression de volatilité précède les grands mouvements.
      On joue la direction du breakout en filtrant par momentum.
    """
    from core.features.store import FeatureStore

    bb_period    = int(params.get("bb_period", 20))
    bb_std       = params.get("bb_std", 2.0)
    squeeze_ma   = int(params.get("squeeze_ma_period", 20))  # période MA de la BB width
    ema_fast     = int(params.get("ema_fast", 9))
    ema_slow     = int(params.get("ema_slow", 21))
    squeeze_mult = params.get("squeeze_threshold", 0.95)  # width < mult * ma_width = squeeze

    std_int = int(bb_std)  # FeatureStore utilise int(n_std) dans les clés : bb_width_20_2
    fs = FeatureStore()
    enriched = fs.compute(df, [
        f"bb_width_{bb_period}_{std_int}",
        f"ema_{ema_fast}",
        f"ema_{ema_slow}",
    ])

    bb_width  = enriched[f"bb_width_{bb_period}_{std_int}"]
    ema_f     = enriched[f"ema_{ema_fast}"]
    ema_s     = enriched[f"ema_{ema_slow}"]

    # Squeeze détecté quand bb_width est sous sa propre MA (bandes qui se serrent)
    width_ma    = bb_width.rolling(squeeze_ma).mean()
    in_squeeze  = bb_width < squeeze_mult * width_ma  # déjà shiftée par FeatureStore

    # Sortie du squeeze : était en squeeze, ne l'est plus
    in_squeeze_prev = in_squeeze.shift(1)
    squeeze_exit    = (~in_squeeze) & in_squeeze_prev

    momentum_bull = ema_f > ema_s
    momentum_bear = ema_f < ema_s

    df = df.copy()
    df["bb_width"]     = bb_width
    df["ema_fast"]     = ema_f
    df["ema_slow"]     = ema_s
    df["in_squeeze"]   = in_squeeze
    df["signal_long"]  = squeeze_exit & momentum_bull
    df["signal_short"] = squeeze_exit & momentum_bear

    return df


@register_strategy("momentum_burst_")
def momentum_burst_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Momentum Burst — EMA crossover + spike de volume.

    Logique :
      - Signal LONG  : EMA rapide croise au-dessus de l'EMA lente
                       ET volume > volume_mult × volume moyen
      - Signal SHORT : EMA rapide croise en dessous de l'EMA lente
                       ET volume élevé

    Le filtre volume est critique : seuls les croisements accompagnés
    d'un vrai engagement du marché (smart money) sont retenus.
    """
    from core.features.store import FeatureStore

    ema_fast    = int(params.get("ema_fast", 9))
    ema_slow    = int(params.get("ema_slow", 21))
    vol_mult    = params.get("volume_multiplier", 2.0)
    vol_lookback = int(params.get("volume_lookback", 20))

    fs = FeatureStore()
    enriched = fs.compute(df, [f"ema_{ema_fast}", f"ema_{ema_slow}"])

    ema_f = enriched[f"ema_{ema_fast}"]   # déjà shift(1) par FeatureStore
    ema_s = enriched[f"ema_{ema_slow}"]

    ema_f_prev = ema_f.shift(1)
    ema_s_prev = ema_s.shift(1)

    # Croisement haussier : était sous, maintenant au-dessus
    cross_bull = (ema_f > ema_s) & (ema_f_prev <= ema_s_prev)
    # Croisement baissier
    cross_bear = (ema_f < ema_s) & (ema_f_prev >= ema_s_prev)

    # Filtre volume (shift(1) pour no-lookahead)
    vol_avg  = df["volume"].rolling(vol_lookback).mean().shift(1)
    vol_prev = df["volume"].shift(1)
    high_vol = vol_prev > vol_mult * vol_avg

    df = df.copy()
    df["ema_fast"]     = ema_f
    df["ema_slow"]     = ema_s
    df["signal_long"]  = cross_bull & high_vol
    df["signal_short"] = cross_bear & high_vol

    return df


@register_strategy("gap_go_")
def gap_go_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Gap and Go — continuation momentum apres un gap significatif.

    Logique (daily) :
      - Detecte un gap > threshold% a l'ouverture
      - Confirmation : la bougie du gap cloture dans la direction du gap (continuation)
      - Volume superieur a la moyenne
      - Signal : continuation probable le(s) jour(s) suivant(s)

    NO LOOKAHEAD : signal utilise shift(1) sur toutes les colonnes.
    """
    gap_threshold = params.get("gap_threshold_pct", 2.0) / 100
    vol_mult = params.get("volume_multiplier", 1.5)
    vol_lookback = int(params.get("volume_lookback", 20))
    max_gap = params.get("max_gap_pct", 8.0) / 100  # gaps trop gros = impredictibles

    # Gap : open[t] vs close[t-1]
    prev_close = df["close"].shift(1)
    gap_pct = (df["open"] - prev_close) / prev_close

    # Continuation : close dans la direction du gap
    continuation_bull = df["close"] > df["open"]  # bougie verte apres gap up
    continuation_bear = df["close"] < df["open"]  # bougie rouge apres gap down

    # Volume
    vol_avg = df["volume"].rolling(vol_lookback).mean()
    high_vol = df["volume"] > vol_mult * vol_avg

    # Shift de 1 bar : signal base sur la bougie precedente (fermee)
    gap_up   = (gap_pct.shift(1) > gap_threshold) & (gap_pct.shift(1) < max_gap)
    gap_down = (gap_pct.shift(1) < -gap_threshold) & (gap_pct.shift(1) > -max_gap)
    cont_bull_prev = continuation_bull.shift(1)
    cont_bear_prev = continuation_bear.shift(1)
    hv_prev = high_vol.shift(1)

    df = df.copy()
    df["gap_pct"] = gap_pct
    df["signal_long"]  = gap_up & cont_bull_prev & hv_prev
    df["signal_short"] = gap_down & cont_bear_prev & hv_prev

    return df


@register_strategy("gap_fill_")
def gap_fill_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Gap Fill Mean Reversion — fader les gaps sans catalyst.

    Logique :
      - Detecte un gap > min_gap% et < max_gap%
      - Volume FAIBLE (pas de catalyst institutionnel) → gap "technique"
      - Parie sur le retour vers le close de la veille (gap fill)
      - Signal inverse du gap : short apres gap up, long apres gap down

    NO LOOKAHEAD : shift(1) systematique.
    """
    min_gap = params.get("min_gap_pct", 1.0) / 100
    max_gap = params.get("max_gap_pct", 3.0) / 100
    vol_max_mult = params.get("volume_max_multiplier", 1.5)  # volume SOUS ce seuil
    vol_lookback = int(params.get("volume_lookback", 20))

    prev_close = df["close"].shift(1)
    gap_pct = (df["open"] - prev_close) / prev_close

    # Volume faible = pas de catalyst → gap technique susceptible de se remplir
    vol_avg = df["volume"].rolling(vol_lookback).mean()
    low_vol = df["volume"] < vol_max_mult * vol_avg

    # Shift pour no-lookahead
    gap_up   = (gap_pct.shift(1) > min_gap) & (gap_pct.shift(1) < max_gap)
    gap_down = (gap_pct.shift(1) < -min_gap) & (gap_pct.shift(1) > -max_gap)
    lv_prev = low_vol.shift(1)

    # Si le gap n'a PAS ete rempli dans la journee, la reversion est plus probable le lendemain
    # Gap up non rempli : close > open de la veille (reste dans le gap)
    not_filled_up   = (df["close"].shift(1) > prev_close.shift(1))
    not_filled_down = (df["close"].shift(1) < prev_close.shift(1))

    df = df.copy()
    df["gap_pct"] = gap_pct
    # Signal INVERSE : short les gap up, long les gap down
    df["signal_short"] = gap_up & lv_prev
    df["signal_long"]  = gap_down & lv_prev

    return df


@register_strategy("rsi_extreme_")
def rsi_extreme_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    RSI(2) Extreme Reversal — entree sur extremes RSI avec filtre tendance.

    Logique :
      - RSI(2) ultra-court : detecte les overshoots intraday/intraweek
      - Seuils extremes (< 5 / > 95) pour ne capter que les vrais extremes
      - Filtre SMA(200) : uniquement long en tendance haussiere,
        short en tendance baissiere

    Edge : les extremes RSI(2) revertent dans 60-65% des cas sur large caps.
    NO LOOKAHEAD : shift(1) sur RSI et SMA.
    """
    rsi_period = int(params.get("rsi_period", 2))
    oversold = params.get("oversold", 5)
    overbought = params.get("overbought", 95)
    sma_period = int(params.get("sma_period", 200))

    rsi = compute_rsi(df["close"], rsi_period)
    sma = compute_sma(df["close"], sma_period)

    # Shift pour no-lookahead
    rsi_prev = rsi.shift(1)
    sma_prev = sma.shift(1)
    close_prev = df["close"].shift(1)

    # Filtre tendance : long uniquement en uptrend, short en downtrend
    uptrend = close_prev > sma_prev
    downtrend = close_prev < sma_prev

    # RSI extreme + reversal : RSI etait extreme, maintenant revient
    rsi_prev2 = rsi.shift(2)
    recovering_from_oversold = (rsi_prev2 < oversold) & (rsi_prev > rsi_prev2)
    recovering_from_overbought = (rsi_prev2 > overbought) & (rsi_prev < rsi_prev2)

    df = df.copy()
    df["rsi"] = rsi
    df["sma_200"] = sma
    df["signal_long"]  = recovering_from_oversold & uptrend
    df["signal_short"] = recovering_from_overbought & downtrend

    return df


@register_strategy("rel_strength_")
def rel_strength_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Relative Strength Momentum — surfer les actifs en acceleration.

    Logique :
      - ROC court terme (5 bars) detecte l'acceleration recente
      - ROC moyen terme (20 bars) confirme la tendance
      - Signal LONG  : ROC_5 > seuil ET ROC_20 > 0 (momentum accelere en uptrend)
      - Signal SHORT : ROC_5 < -seuil ET ROC_20 < 0 (momentum accelere en downtrend)
      - Volume : confirmation par volume superieur a la moyenne

    Edge : les actifs en acceleration continuent dans 60-65% des cas (momentum factor).
    NO LOOKAHEAD : shift(1) systematique.
    """
    roc_fast = int(params.get("roc_fast_period", 5))
    roc_slow = int(params.get("roc_slow_period", 20))
    roc_threshold = params.get("roc_threshold_pct", 2.0) / 100
    vol_mult = params.get("volume_multiplier", 1.5)
    vol_lookback = int(params.get("volume_lookback", 20))

    # Rate of Change
    close = df["close"]
    roc_f = (close / close.shift(roc_fast) - 1)
    roc_s = (close / close.shift(roc_slow) - 1)

    # Volume
    vol_avg = df["volume"].rolling(vol_lookback).mean()
    high_vol = df["volume"] > vol_mult * vol_avg

    # Shift pour no-lookahead
    roc_f_prev = roc_f.shift(1)
    roc_s_prev = roc_s.shift(1)
    hv_prev = high_vol.shift(1)

    # Acceleration : ROC court terme fort + tendance moyen terme confirmee
    accel_bull = (roc_f_prev > roc_threshold) & (roc_s_prev > 0)
    accel_bear = (roc_f_prev < -roc_threshold) & (roc_s_prev < 0)

    df = df.copy()
    df["roc_fast"] = roc_f
    df["roc_slow"] = roc_s
    df["signal_long"]  = accel_bull & hv_prev
    df["signal_short"] = accel_bear & hv_prev

    return df


@register_strategy("seasonality_")
def seasonality_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Intraday Seasonality — Filtres horaires sur tendances statistiques.

    Logique :
      - Actif uniquement pendant les fenêtres horaires à haute probabilité
      - Signal de base : EMA crossover (momentum)
      - Filtre heure : trade uniquement dans les plages définies par session
        Ex : ouverture London (8h-10h) ou overlap London/NY (13h-15h)

    Pourquoi ça marche :
      Les flux institutionnels sont concentrés à certaines heures.
      Les patterns intraday se répètent statistiquement.
    """
    from core.features.store import FeatureStore

    ema_fast      = int(params.get("ema_fast", 9))
    ema_slow      = int(params.get("ema_slow", 21))
    session_start = int(params.get("session_start_hour", 8))   # heure UTC
    session_end   = int(params.get("session_end_hour", 10))

    fs = FeatureStore()
    enriched = fs.compute(df, [f"ema_{ema_fast}", f"ema_{ema_slow}"])

    ema_f = enriched[f"ema_{ema_fast}"]
    ema_s = enriched[f"ema_{ema_slow}"]
    ema_f_prev = ema_f.shift(1)
    ema_s_prev = ema_s.shift(1)

    cross_bull = (ema_f > ema_s) & (ema_f_prev <= ema_s_prev)
    cross_bear = (ema_f < ema_s) & (ema_f_prev >= ema_s_prev)

    # Filtre temporel : on utilise l'heure de l'index (supposé UTC)
    if hasattr(df.index, "hour"):
        in_session = (df.index.hour >= session_start) & (df.index.hour < session_end)
        in_session = pd.Series(in_session, index=df.index)
    else:
        in_session = pd.Series(True, index=df.index)  # pas de filtrage si index sans heure

    df = df.copy()
    df["ema_fast"]     = ema_f
    df["ema_slow"]     = ema_s
    df["in_session"]   = in_session
    df["signal_long"]  = cross_bull & in_session
    df["signal_short"] = cross_bear & in_session

    return df
