"""
STRAT-002 — Altcoin Relative Strength (Margin Long/Short).

Edge: Altcoins exhibit strong cross-sectional momentum relative to BTC.
Coins that outperform BTC on a risk-adjusted basis over 14 days tend to
continue outperforming, and vice versa. This is the crypto equivalent of
Jegadeesh & Titman (1993) momentum, adapted for the BTC-denominated
altcoin market.

Signal:
  - Weekly (Sunday 00:00 UTC): rank top 15 altcoins by 14d BTC-adjusted alpha
  - alpha_14d = return_14d - beta * btc_return_14d
  - beta from 90d rolling correlation
  - LONG top 3 (spot), SHORT bottom 3 (margin borrow)

Filters:
  - Volume > $50M 24h
  - Borrow available, rate < 0.1%/day
  - Market cap > $2B
  - No meme coins (blacklist)
  - SL per position -8%, portfolio SL -5%
  - If 2 out of 6 positions hit stop → close all

Allocation: 15% of crypto capital
Leverage: 1.5x max (margin)
Cost: ~$13.5/week in commissions + borrow interest
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "Altcoin Relative Strength",
    "id": "STRAT-002",
    "symbols": [
        "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT", "ATOMUSDT",
        "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
    ],
    "benchmark": "BTCUSDT",
    "allocation_pct": 0.15,
    "max_leverage": 1.5,
    "market_type": "margin",  # margin for shorts, spot for longs
    "timeframe": "1d",
    "frequency": "weekly",  # rebalance Sunday 00:00 UTC
    "max_positions": 6,  # 3 long + 3 short
}

# ── Ranking parameters ──────────────────────────────────────────────────
ALPHA_LOOKBACK_DAYS = 14
BETA_LOOKBACK_DAYS = 90
TOP_N = 3  # long top 3
BOTTOM_N = 3  # short bottom 3

# ── Filters ─────────────────────────────────────────────────────────────
MIN_VOLUME_24H = 50_000_000  # $50M
MIN_MARKET_CAP = 2_000_000_000  # $2B
MAX_BORROW_RATE_DAILY = 0.001  # 0.1%/day

# ── Risk management ─────────────────────────────────────────────────────
SL_PER_POSITION_PCT = -0.08  # -8% per position
PORTFOLIO_SL_PCT = -0.05  # -5% portfolio level
MAX_STOPS_BEFORE_CLOSE_ALL = 2  # if 2/6 hit stop → liquidate all

# ── Meme coin blacklist ─────────────────────────────────────────────────
MEME_BLACKLIST = {
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT", "BONKUSDT",
    "WIFUSDT", "MEMEUSDT", "BOMEUSDT", "TURBOUSDT", "BABYDOGEUSDT",
}


def compute_btc_adjusted_alpha(
    returns: pd.DataFrame,
    btc_returns: pd.Series,
    alpha_window: int = ALPHA_LOOKBACK_DAYS,
    beta_window: int = BETA_LOOKBACK_DAYS,
) -> pd.Series:
    """Compute BTC-adjusted alpha for each altcoin.

    alpha_14d = return_14d - beta * btc_return_14d
    beta = cov(alt, btc) / var(btc) over 90 days

    Args:
        returns: DataFrame of daily returns, columns = altcoin symbols
        btc_returns: Series of BTC daily returns
        alpha_window: lookback for alpha calculation (14d)
        beta_window: lookback for beta estimation (90d)

    Returns:
        Series indexed by symbol with alpha values
    """
    if len(returns) < beta_window:
        return pd.Series(dtype=float)

    btc_ret_window = btc_returns.tail(beta_window)
    btc_var = btc_ret_window.var()
    if btc_var == 0 or pd.isna(btc_var):
        return pd.Series(dtype=float)

    btc_cumret_14d = (1 + btc_returns.tail(alpha_window)).prod() - 1

    alphas = {}
    for symbol in returns.columns:
        alt_ret_window = returns[symbol].tail(beta_window)

        # Beta from 90d covariance
        cov = alt_ret_window.cov(btc_ret_window)
        beta = cov / btc_var if btc_var > 0 else 1.0

        # 14d cumulative return
        alt_cumret_14d = (1 + returns[symbol].tail(alpha_window)).prod() - 1

        # Alpha = excess return over beta-adjusted BTC return
        alpha = alt_cumret_14d - beta * btc_cumret_14d
        alphas[symbol] = alpha

    return pd.Series(alphas).dropna().sort_values(ascending=False)


def filter_universe(
    symbols: list[str],
    volumes_24h: dict[str, float],
    market_caps: dict[str, float],
    borrow_rates: dict[str, float],
    borrow_available: dict[str, bool],
) -> list[str]:
    """Filter universe by volume, mcap, borrow availability, meme blacklist.

    Returns:
        List of eligible symbols
    """
    eligible = []
    for sym in symbols:
        if sym in MEME_BLACKLIST:
            continue
        if volumes_24h.get(sym, 0) < MIN_VOLUME_24H:
            continue
        if market_caps.get(sym, 0) < MIN_MARKET_CAP:
            continue
        if not borrow_available.get(sym, False):
            continue
        if borrow_rates.get(sym, 1.0) > MAX_BORROW_RATE_DAILY:
            continue
        eligible.append(sym)
    return eligible


def generate_rotation_signals(
    alphas: pd.Series,
    current_positions: dict[str, str],  # {symbol: "LONG" or "SHORT"}
    capital: float,
    eligible_symbols: list[str],
) -> list[dict]:
    """Generate weekly rotation signals.

    Args:
        alphas: Series of BTC-adjusted alpha, sorted descending
        current_positions: current open positions
        capital: allocated capital for this strategy
        eligible_symbols: symbols that pass filters

    Returns:
        List of signal dicts
    """
    signals = []

    # Filter alphas to eligible symbols only
    alphas_eligible = alphas[alphas.index.isin(eligible_symbols)]
    if len(alphas_eligible) < TOP_N + BOTTOM_N:
        # Not enough eligible symbols — close everything
        for sym in current_positions:
            signals.append({
                "action": "CLOSE",
                "symbol": sym,
                "reason": "insufficient_eligible_universe",
                "strategy": "altcoin_relative_strength",
            })
        return signals

    # Target portfolio
    long_targets = set(alphas_eligible.head(TOP_N).index)
    short_targets = set(alphas_eligible.tail(BOTTOM_N).index)

    # Ensure no overlap
    overlap = long_targets & short_targets
    long_targets -= overlap
    short_targets -= overlap

    # Close positions not in new targets
    for sym, direction in current_positions.items():
        if direction == "LONG" and sym not in long_targets:
            signals.append({
                "action": "CLOSE",
                "symbol": sym,
                "reason": "rotation_out",
                "strategy": "altcoin_relative_strength",
            })
        elif direction == "SHORT" and sym not in short_targets:
            signals.append({
                "action": "CLOSE",
                "symbol": sym,
                "reason": "rotation_out",
                "strategy": "altcoin_relative_strength",
            })

    # Open new longs (spot buy)
    per_position_capital = capital / (TOP_N + BOTTOM_N)
    for sym in long_targets:
        if sym not in current_positions:
            signals.append({
                "action": "BUY",
                "symbol": sym,
                "notional": per_position_capital,
                "market_type": "spot",
                "stop_loss_pct": SL_PER_POSITION_PCT,
                "strategy": "altcoin_relative_strength",
                "alpha": round(float(alphas_eligible.get(sym, 0)), 4),
            })

    # Open new shorts (margin borrow + sell)
    for sym in short_targets:
        if sym not in current_positions:
            signals.append({
                "action": "SELL",
                "symbol": sym,
                "notional": per_position_capital,
                "market_type": "margin",
                "leverage": STRATEGY_CONFIG["max_leverage"],
                "stop_loss_pct": -SL_PER_POSITION_PCT,  # positive for shorts
                "strategy": "altcoin_relative_strength",
                "alpha": round(float(alphas_eligible.get(sym, 0)), 4),
            })

    return signals


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for altcoin relative strength rotation.

    Kwargs:
        is_rebalance_day: True on Sundays at 00:00 UTC
        returns_df: DataFrame of daily returns for all altcoins
        btc_returns: Series of BTC daily returns
        volumes_24h: {symbol: volume_usd}
        market_caps: {symbol: mcap_usd}
        borrow_rates: {symbol: daily_rate}
        borrow_available: {symbol: bool}
        current_asset: symbol being evaluated
        stops_hit_count: number of positions that hit stop this week
        portfolio_pnl_pct: portfolio-level unrealized PnL
    """
    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # ── Portfolio-level risk checks ─────────────────────────────────────
    stops_hit = kwargs.get("stops_hit_count", 0)
    portfolio_pnl = kwargs.get("portfolio_pnl_pct", 0.0)

    # If 2+ stops hit → close all
    if stops_hit >= MAX_STOPS_BEFORE_CLOSE_ALL and has_position:
        return {
            "action": "CLOSE",
            "reason": "cascade_stop_2_of_6",
            "stops_hit": stops_hit,
            "strategy": "altcoin_relative_strength",
        }

    # Portfolio-level stop
    if portfolio_pnl < PORTFOLIO_SL_PCT and has_position:
        return {
            "action": "CLOSE",
            "reason": "portfolio_stop_loss",
            "portfolio_pnl_pct": portfolio_pnl,
            "strategy": "altcoin_relative_strength",
        }

    # ── Per-position stop check ─────────────────────────────────────────
    if has_position:
        pos = positions[0]
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            current_price = candle.get("close", pos.entry_price)
            direction = getattr(pos, "direction", 1)
            unrealized = (current_price / pos.entry_price - 1) * direction
            if unrealized < SL_PER_POSITION_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "position_stop_loss_8pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "altcoin_relative_strength",
                }
        return None  # Hold until next rebalance

    # ── Only trade on rebalance day ─────────────────────────────────────
    if not kwargs.get("is_rebalance_day", False):
        return None

    returns_df = kwargs.get("returns_df")
    btc_returns = kwargs.get("btc_returns")
    if returns_df is None or btc_returns is None:
        return None

    # Compute alphas
    alphas = compute_btc_adjusted_alpha(returns_df, btc_returns)
    if alphas.empty:
        return None

    current_asset = kwargs.get("current_asset", "")

    # Check if current asset is in top/bottom
    if current_asset in alphas.head(TOP_N).index:
        capital = state.get("capital", 10_000) * STRATEGY_CONFIG["allocation_pct"]
        per_pos = capital / (TOP_N + BOTTOM_N)
        return {
            "action": "BUY",
            "pct": per_pos / state.get("capital", 10_000),
            "market_type": "spot",
            "strategy": "altcoin_relative_strength",
            "alpha": round(float(alphas.get(current_asset, 0)), 4),
        }

    if current_asset in alphas.tail(BOTTOM_N).index:
        borrow_rate = kwargs.get("borrow_rates", {}).get(current_asset, 0)
        if borrow_rate < MAX_BORROW_RATE_DAILY:
            capital = state.get("capital", 10_000) * STRATEGY_CONFIG["allocation_pct"]
            per_pos = capital / (TOP_N + BOTTOM_N)
            return {
                "action": "SELL",
                "pct": per_pos / state.get("capital", 10_000),
                "leverage": STRATEGY_CONFIG["max_leverage"],
                "market_type": "margin",
                "strategy": "altcoin_relative_strength",
                "alpha": round(float(alphas.get(current_asset, 0)), 4),
                "borrow_rate_daily": borrow_rate,
            }

    return None
