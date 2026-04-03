"""Cross-Asset Time-Series Momentum (Moskowitz, Ooi & Pedersen 2012).

Edge structurel: momentum time-series fonctionne sur TOUTES les classes d'actifs
simultanement depuis 100+ ans. L'edge persiste car il est lie au comportement
humain (under-reaction) et aux contraintes institutionnelles.

Mecanisme:
  - Pour chaque asset: si rendement 12 mois > 0 → LONG, sinon CASH (ou SHORT)
  - Ponderation: inverse-vol (risk parity)
  - Rebalance: weekly
  - 5 instruments: SPY, TLT, GLD, EURUSD, BTC

Avantages vs stat arb:
  - Fonctionne EN trend (le contraire du stat arb)
  - Ultra-simple, pas de formation/cointeg/pairs
  - Backteste sur 100+ ans (Moskowitz 2012, AQR)
  - Decorrelé des strats mean-revert existantes

Broker: Alpaca (SPY, TLT, GLD) + IBKR (EURUSD) + Binance (BTC)
Capital: reparti sur les 3 brokers existants
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("cross_asset_momentum")


# ============================================================
# Configuration
# ============================================================

class MomentumSignal(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CASH = "CASH"


@dataclass
class AssetConfig:
    """Config per asset in the cross-asset universe."""
    symbol: str
    asset_class: str    # equity, bond, commodity, fx, crypto
    broker: str         # alpaca, ibkr, binance
    ticker: str         # Actual trading ticker
    min_position_usd: float = 100.0
    allow_short: bool = False  # Only short if allowed


# Universe: 1 representative per asset class
UNIVERSE = [
    AssetConfig("SPY", "equity", "alpaca", "SPY", 100, False),
    AssetConfig("TLT", "bond", "alpaca", "TLT", 100, False),
    AssetConfig("GLD", "commodity", "alpaca", "GLD", 100, False),
    AssetConfig("EURUSD", "fx", "ibkr", "EUR.USD", 200, True),
    AssetConfig("BTC", "crypto", "binance", "BTCUSDC", 50, False),
]


@dataclass
class CrossAssetMomentumConfig:
    """Strategy configuration."""
    # Lookback periods for momentum signal
    lookback_days: int = 252        # 12 months (standard Moskowitz)
    short_lookback_days: int = 21   # 1 month (for dual momentum filter)

    # Rebalance
    rebalance_frequency: str = "weekly"  # weekly or monthly
    rebalance_day: int = 0              # 0=Monday

    # Signal
    use_dual_momentum: bool = True  # Combine 12M + 1M signals
    allow_short: bool = False       # Long-only or long/short
    vol_target_annual: float = 0.10 # 10% annual vol target

    # Sizing
    method: str = "INVERSE_VOL"     # INVERSE_VOL or EQUAL_WEIGHT
    vol_lookback_days: int = 60     # For inverse-vol weighting
    max_weight: float = 0.40        # Max 40% in one asset
    min_weight: float = 0.05        # Min 5% if signal is LONG
    cash_yield_annual: float = 0.04 # Risk-free rate for Sharpe

    # Risk
    max_drawdown_pct: float = 0.15  # -15% → reduce to 50%
    rebalance_buffer_pct: float = 0.03  # Don't rebalance if weight change < 3%

    # Regime
    regime_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "TREND_STRONG": 1.2,    # Momentum loves trends
        "MEAN_REVERT": 0.7,     # Whipsaw risk
        "HIGH_VOL": 0.8,        # Larger moves, keep exposure
        "PANIC": 0.5,           # Reduce but don't kill
        "LOW_LIQUIDITY": 0.5,
        "UNKNOWN": 0.8,
    })


# ============================================================
# Core Strategy
# ============================================================

@dataclass
class AssetSignal:
    """Momentum signal for a single asset."""
    symbol: str
    asset_class: str
    signal: MomentumSignal
    return_12m: float
    return_1m: float
    volatility_annual: float
    raw_weight: float        # Before normalization
    final_weight: float      # After normalization
    target_notional: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "signal": self.signal.value,
            "return_12m": round(self.return_12m, 4),
            "return_1m": round(self.return_1m, 4),
            "vol_annual": round(self.volatility_annual, 4),
            "weight": round(self.final_weight, 4),
            "notional": round(self.target_notional, 0),
        }


class CrossAssetMomentumStrategy:
    """Cross-Asset Time-Series Momentum.

    Usage:
        strategy = CrossAssetMomentumStrategy()
        signals = strategy.generate_signals(prices, capital=30000)
        for sig in signals:
            if sig.signal == MomentumSignal.LONG:
                buy(sig.symbol, sig.target_notional)
    """

    def __init__(
        self,
        config: CrossAssetMomentumConfig = None,
        universe: List[AssetConfig] = None,
    ):
        self.config = config or CrossAssetMomentumConfig()
        self.universe = universe or UNIVERSE
        self.last_weights: Dict[str, float] = {}
        self.last_rebalance: Optional[datetime] = None

    def generate_signals(
        self,
        prices: Dict[str, pd.DataFrame],
        capital: float = 30_000.0,
        current_regime: str = "UNKNOWN",
        as_of: datetime = None,
    ) -> List[AssetSignal]:
        """Generate momentum signals for all assets.

        Args:
            prices: {symbol: DataFrame with 'close' column}
            capital: Total capital to allocate
            current_regime: Market regime for sizing adjustment
        """
        now = as_of or datetime.now()
        signals = []

        # Regime multiplier
        regime_mult = self.config.regime_multipliers.get(current_regime, 0.8)

        for asset in self.universe:
            if asset.symbol not in prices:
                logger.warning(f"No data for {asset.symbol}, skipping")
                continue

            close = prices[asset.symbol]["close"]
            if len(close) < self.config.lookback_days:
                logger.warning(f"{asset.symbol}: only {len(close)} bars, need {self.config.lookback_days}")
                continue

            sig = self._compute_signal(asset, close)
            signals.append(sig)

        # Compute weights
        self._compute_weights(signals, capital, regime_mult)

        # Apply rebalance buffer
        if self.last_weights:
            for sig in signals:
                old_w = self.last_weights.get(sig.symbol, 0)
                if abs(sig.final_weight - old_w) < self.config.rebalance_buffer_pct:
                    sig.final_weight = old_w  # Don't rebalance small changes
                    sig.target_notional = capital * sig.final_weight

        # Store weights
        self.last_weights = {s.symbol: s.final_weight for s in signals}
        self.last_rebalance = now

        return signals

    def _compute_signal(
        self,
        asset: AssetConfig,
        close: pd.Series,
    ) -> AssetSignal:
        """Compute momentum signal for a single asset."""
        # 12-month return
        ret_12m = close.iloc[-1] / close.iloc[-self.config.lookback_days] - 1

        # 1-month return
        short_lb = min(self.config.short_lookback_days, len(close) - 1)
        ret_1m = close.iloc[-1] / close.iloc[-short_lb] - 1

        # Volatility (annualized)
        vol_lb = min(self.config.vol_lookback_days, len(close) - 1)
        daily_returns = close.pct_change().iloc[-vol_lb:]
        vol_annual = float(daily_returns.std() * np.sqrt(252))
        if vol_annual == 0:
            vol_annual = 0.01  # Floor

        # Signal determination
        if self.config.use_dual_momentum:
            # Dual momentum: both 12M AND 1M must agree
            if ret_12m > 0 and ret_1m > 0:
                signal = MomentumSignal.LONG
            elif ret_12m < 0 and ret_1m < 0 and self.config.allow_short and asset.allow_short:
                signal = MomentumSignal.SHORT
            else:
                signal = MomentumSignal.CASH
        else:
            # Simple: 12M return only
            if ret_12m > 0:
                signal = MomentumSignal.LONG
            elif ret_12m < 0 and self.config.allow_short and asset.allow_short:
                signal = MomentumSignal.SHORT
            else:
                signal = MomentumSignal.CASH

        return AssetSignal(
            symbol=asset.symbol,
            asset_class=asset.asset_class,
            signal=signal,
            return_12m=ret_12m,
            return_1m=ret_1m,
            volatility_annual=vol_annual,
            raw_weight=0.0,
            final_weight=0.0,
        )

    def _compute_weights(
        self,
        signals: List[AssetSignal],
        capital: float,
        regime_mult: float,
    ):
        """Compute portfolio weights using inverse-vol risk parity."""
        active = [s for s in signals if s.signal != MomentumSignal.CASH]

        if not active:
            # All cash
            for s in signals:
                s.final_weight = 0.0
                s.target_notional = 0.0
            return

        if self.config.method == "INVERSE_VOL":
            # Inverse-volatility weighting
            inv_vols = [1.0 / s.volatility_annual for s in active]
            total_inv_vol = sum(inv_vols)

            for s, iv in zip(active, inv_vols):
                s.raw_weight = iv / total_inv_vol
        else:
            # Equal weight
            n = len(active)
            for s in active:
                s.raw_weight = 1.0 / n

        # Apply vol target scaling
        # Portfolio vol ≈ avg(individual vols * weights)
        port_vol = sum(s.raw_weight * s.volatility_annual for s in active)
        if port_vol > 0:
            vol_scale = self.config.vol_target_annual / port_vol
        else:
            vol_scale = 1.0
        vol_scale = min(vol_scale, 2.0)  # Cap leverage at 2x

        # Apply regime multiplier
        scale = vol_scale * regime_mult

        # Final weights with bounds
        for s in active:
            w = s.raw_weight * scale
            # Apply direction
            if s.signal == MomentumSignal.SHORT:
                w = -w
            # Bounds
            w = max(-self.config.max_weight, min(self.config.max_weight, w))
            if abs(w) < self.config.min_weight:
                w = 0.0  # Below minimum → go to cash
            s.final_weight = w
            s.target_notional = capital * abs(w)

        # Cash assets get 0
        for s in signals:
            if s.signal == MomentumSignal.CASH:
                s.final_weight = 0.0
                s.target_notional = 0.0

    def get_portfolio_summary(self, signals: List[AssetSignal]) -> Dict[str, Any]:
        """Summary of current portfolio allocation."""
        long_pct = sum(s.final_weight for s in signals if s.final_weight > 0)
        short_pct = sum(abs(s.final_weight) for s in signals if s.final_weight < 0)
        cash_pct = 1.0 - long_pct - short_pct

        return {
            "n_long": sum(1 for s in signals if s.signal == MomentumSignal.LONG),
            "n_short": sum(1 for s in signals if s.signal == MomentumSignal.SHORT),
            "n_cash": sum(1 for s in signals if s.signal == MomentumSignal.CASH),
            "long_pct": round(long_pct * 100, 1),
            "short_pct": round(short_pct * 100, 1),
            "cash_pct": round(cash_pct * 100, 1),
            "assets": [s.to_dict() for s in signals],
        }


# ============================================================
# Backtester
# ============================================================

@dataclass
class MomentumBacktestResult:
    """Backtest results."""
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    volatility_annual: float = 0.0
    n_rebalances: int = 0
    avg_long_assets: float = 0.0
    total_turnover: float = 0.0
    equity_curve: pd.Series = None
    weights_history: List[Dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"CROSS-ASSET MOMENTUM BACKTEST\n"
            f"{'='*60}\n"
            f"Total Return:     {self.total_return_pct:>8.2f}%\n"
            f"Annualized:       {self.annualized_return_pct:>8.2f}%\n"
            f"Sharpe Ratio:     {self.sharpe_ratio:>8.2f}\n"
            f"Max Drawdown:     {self.max_drawdown_pct:>8.2f}%\n"
            f"Calmar Ratio:     {self.calmar_ratio:>8.2f}\n"
            f"Volatility:       {self.volatility_annual:>8.2f}%\n"
            f"Rebalances:       {self.n_rebalances:>8d}\n"
            f"Avg Long Assets:  {self.avg_long_assets:>8.1f}\n"
            f"Total Turnover:   {self.total_turnover:>8.2f}x\n"
            f"{'='*60}\n"
        )


def backtest_cross_asset_momentum(
    prices: Dict[str, pd.DataFrame],
    start_date: str = "2024-01-01",
    end_date: str = "2025-12-31",
    initial_capital: float = 30_000.0,
    config: CrossAssetMomentumConfig = None,
    cost_bps: float = 3.0,
    rebalance_every_n_days: int = 5,
) -> MomentumBacktestResult:
    """Backtest the cross-asset momentum strategy.

    Args:
        prices: {symbol: DataFrame with 'close' and DatetimeIndex}
        start_date/end_date: Backtest window
        initial_capital: Starting capital
        config: Strategy config
        cost_bps: Round-trip cost per rebalance in bps
        rebalance_every_n_days: Rebalance frequency
    """
    config = config or CrossAssetMomentumConfig()
    strategy = CrossAssetMomentumStrategy(config)

    # Align all assets to common dates
    sample = list(prices.values())[0]
    all_dates = sample.index
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    bt_dates = all_dates[(all_dates >= start_dt) & (all_dates <= end_dt)]

    if len(bt_dates) < 20:
        return MomentumBacktestResult()

    equity = initial_capital
    equity_series = {}
    weights = {asset.symbol: 0.0 for asset in strategy.universe}
    prev_weights = dict(weights)
    total_turnover = 0.0
    n_rebalances = 0
    long_counts = []
    weights_history = []

    for i, date in enumerate(bt_dates):
        # Daily return from held positions
        day_return = 0.0
        for asset in strategy.universe:
            if asset.symbol not in prices:
                continue
            close = prices[asset.symbol]["close"]
            idx = close.index.get_indexer([date], method="ffill")[0]
            if idx < 1:
                continue
            daily_ret = close.iloc[idx] / close.iloc[idx - 1] - 1
            day_return += weights.get(asset.symbol, 0) * daily_ret

        equity *= (1 + day_return)

        # Weekly rebalance
        if i % rebalance_every_n_days == 0:
            prices_slice = {}
            for asset in strategy.universe:
                if asset.symbol in prices:
                    mask = prices[asset.symbol].index <= date
                    if mask.sum() >= config.lookback_days:
                        prices_slice[asset.symbol] = prices[asset.symbol][mask]

            if len(prices_slice) >= 3:
                signals = strategy.generate_signals(
                    prices_slice, capital=equity, as_of=date.to_pydatetime(),
                )

                new_weights = {s.symbol: s.final_weight for s in signals}

                # Turnover cost
                turnover = sum(
                    abs(new_weights.get(s, 0) - prev_weights.get(s, 0))
                    for s in set(list(new_weights.keys()) + list(prev_weights.keys()))
                ) / 2
                cost = equity * turnover * cost_bps / 10_000
                equity -= cost
                total_turnover += turnover

                weights = new_weights
                prev_weights = dict(weights)
                n_rebalances += 1

                n_long = sum(1 for s in signals if s.signal == MomentumSignal.LONG)
                long_counts.append(n_long)

                weights_history.append({
                    "date": str(date.date()),
                    "weights": {k: round(v, 3) for k, v in weights.items() if v != 0},
                    "n_long": n_long,
                })

        equity_series[date] = equity

    # Compute metrics
    eq = pd.Series(equity_series)
    daily_returns = eq.pct_change().dropna()

    total_ret = (equity - initial_capital) / initial_capital * 100
    n_years = len(bt_dates) / 252
    ann_ret = ((equity / initial_capital) ** (1 / max(n_years, 0.1)) - 1) * 100

    vol = float(daily_returns.std() * np.sqrt(252) * 100) if len(daily_returns) > 0 else 0
    sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

    peak = eq.expanding().max()
    dd = (eq - peak) / peak
    max_dd = float(dd.min() * 100)
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    return MomentumBacktestResult(
        total_return_pct=round(total_ret, 2),
        annualized_return_pct=round(ann_ret, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
        calmar_ratio=round(calmar, 2),
        volatility_annual=round(vol, 2),
        n_rebalances=n_rebalances,
        avg_long_assets=round(np.mean(long_counts), 1) if long_counts else 0,
        total_turnover=round(total_turnover, 2),
        equity_curve=eq,
        weights_history=weights_history,
    )


def walk_forward_cross_asset(
    prices: Dict[str, pd.DataFrame],
    n_windows: int = 5,
    initial_capital: float = 30_000.0,
) -> Dict:
    """Walk-forward analysis."""
    sample = list(prices.values())[0]
    all_dates = sample.index
    total_days = len(all_dates)
    window_size = total_days // n_windows

    results = []

    for w in range(n_windows):
        start_idx = w * window_size
        end_idx = min((w + 1) * window_size, total_days)
        window_dates = all_dates[start_idx:end_idx]

        # Use 70% for lookback warmup, 30% for test
        train_end_idx = int(len(window_dates) * 0.7)
        test_start = window_dates[train_end_idx]
        test_end = window_dates[-1]

        result = backtest_cross_asset_momentum(
            prices=prices,
            start_date=str(test_start.date()),
            end_date=str(test_end.date()),
            initial_capital=initial_capital,
        )

        results.append({
            "window": w + 1,
            "test_start": str(test_start.date()),
            "test_end": str(test_end.date()),
            "sharpe": result.sharpe_ratio,
            "return_pct": result.total_return_pct,
            "max_dd_pct": result.max_drawdown_pct,
            "vol_pct": result.volatility_annual,
        })

    sharpes = [r["sharpe"] for r in results]
    profitable = sum(1 for r in results if r["return_pct"] > 0)

    summary = {
        "windows": results,
        "n_windows": n_windows,
        "avg_sharpe_oos": round(np.mean(sharpes), 2),
        "min_sharpe_oos": round(min(sharpes), 2),
        "max_sharpe_oos": round(max(sharpes), 2),
        "profitable_windows_pct": round(profitable / n_windows * 100, 1),
    }

    if summary["avg_sharpe_oos"] >= 0.5 and summary["profitable_windows_pct"] >= 60:
        summary["verdict"] = "VALIDATED"
    elif summary["avg_sharpe_oos"] >= 0.3 and summary["profitable_windows_pct"] >= 40:
        summary["verdict"] = "BORDERLINE"
    else:
        summary["verdict"] = "REJECTED"

    return summary
