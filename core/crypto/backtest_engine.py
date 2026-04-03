"""
CryptoBacktester V2 — Backtest engine for Binance France Margin + Spot + Earn.

NO futures/perp. Key differences from V1:
  1. Borrow interest: hourly accrual on margin shorts (not 8h funding)
  2. Commissions: spot/margin maker+taker 0.10% (not 0.02% futures)
  3. Slippage: spot book is thinner than perp — BTC 2bps, ETH 3bps, tier2 5bps, tier3 8bps
  4. Earn yield: simulate Binance Earn returns with historical APY
  5. Margin liquidation: margin_level = total_asset / total_debt, liquidated at 1.1
  6. Borrow availability: if rate > 0.5%/day, reduce short size 50%
  7. Walk-forward validation adapted for margin/earn mechanics
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Historical borrow rates (daily %, Binance cross-margin typical)
# ──────────────────────────────────────────────────────────────────────
BORROW_RATES_DAILY = {
    "BTCUSDT": 0.0002,     # ~0.02%/day ≈ 7.3%/year
    "ETHUSDT": 0.00024,    # ~0.024%/day ≈ 8.8%/year
    "SOLUSDT": 0.0005,     # ~0.05%/day ≈ 18%/year
    "BNBUSDT": 0.0005,     # ~0.05%/day
    "XRPUSDT": 0.0008,     # ~0.08%/day
    "DOGEUSDT": 0.0012,    # ~0.12%/day
    "AVAXUSDT": 0.0010,    # ~0.10%/day
    "MATICUSDT": 0.0015,   # ~0.15%/day
    "LINKUSDT": 0.0008,    # ~0.08%/day
    "ADAUSDT": 0.0010,     # ~0.10%/day
    "tier_2": 0.0008,      # mid-cap default
    "tier_3": 0.0024,      # small-cap default (~0.24%/day)
}

# High-rate threshold: reduce short size if above this
BORROW_RATE_HIGH_THRESHOLD = 0.005  # 0.5%/day

# Earn APY (annualized, flexible staking typical)
EARN_APY = {
    "BTCUSDT": 0.02,       # ~2% APY flexible
    "ETHUSDT": 0.025,      # ~2.5% APY flexible
    "USDTUSDT": 0.05,      # ~5% APY (stablecoin earn)
    "SOLUSDT": 0.04,       # ~4% APY
    "BNBUSDT": 0.015,      # ~1.5% APY
    "default": 0.03,       # 3% fallback
}


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────

@dataclass
class CryptoPosition:
    """Track a single crypto position (spot or margin)."""
    symbol: str
    direction: int               # +1 long, -1 short
    qty: float
    entry_price: float
    entry_time: datetime
    stop_loss: float = 0.0
    take_profit: float = 0.0
    leverage: float = 1.0        # 1x spot, up to 3x margin
    market_type: str = "spot"    # "spot" or "margin"
    strategy: str = ""
    realized_pnl: float = 0.0
    commissions_paid: float = 0.0
    max_favorable: float = 0.0
    max_adverse: float = 0.0

    # Margin borrow tracking
    is_margin_borrow: bool = False
    borrowed_asset: str = ""     # e.g. "BTC" for short, "USDT" for leveraged long
    borrowed_amount: float = 0.0
    total_borrow_cost: float = 0.0
    last_interest_ts: datetime | None = None

    # Earn tracking
    is_earn: bool = False
    earn_yield_accrued: float = 0.0

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price

    @property
    def is_long(self) -> bool:
        return self.direction > 0


@dataclass
class CryptoTrade:
    """Completed trade record."""
    symbol: str
    direction: str
    qty: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_pct: float
    borrow_cost: float           # replaces funding_cost
    commission: float
    slippage_cost: float
    strategy: str
    exit_reason: str
    holding_hours: float
    leverage: float
    market_type: str
    earn_yield: float = 0.0


@dataclass
class EarnPosition:
    """Track an Earn (staking/flexible savings) position."""
    symbol: str
    amount: float                # USDT equivalent deposited
    apy: float
    start_time: datetime
    yield_accrued: float = 0.0
    is_locked: bool = False      # flexible vs locked
    lock_days: int = 0


# ──────────────────────────────────────────────────────────────────────
# Slippage model — spot book is thinner than perp
# ──────────────────────────────────────────────────────────────────────

class SlippageModel:
    """Tier-based slippage model for Binance spot/margin."""

    MODELS = {
        "BTCUSDT": {"base_bps": 2, "impact_per_100k": 1.0},
        "ETHUSDT": {"base_bps": 3, "impact_per_100k": 1.5},
        "tier_2":  {"base_bps": 5, "impact_per_100k": 3.0},
        "tier_3":  {"base_bps": 8, "impact_per_100k": 6.0},
    }

    TIER_2 = {"SOLUSDT", "BNBUSDT", "XRPUSDT"}

    @classmethod
    def estimate(cls, symbol: str, notional_usd: float) -> float:
        """Estimate slippage in decimal (e.g. 0.0002 = 2 bps)."""
        if symbol in cls.MODELS:
            model = cls.MODELS[symbol]
        elif symbol in cls.TIER_2:
            model = cls.MODELS["tier_2"]
        else:
            model = cls.MODELS["tier_3"]

        base = model["base_bps"]
        impact = model["impact_per_100k"] * (notional_usd / 100_000)
        return (base + impact) / 10_000


# ──────────────────────────────────────────────────────────────────────
# Commission model — spot/margin only, no futures
# ──────────────────────────────────────────────────────────────────────

class CommissionModel:
    """Binance spot/margin commission schedule (France, no BNB discount)."""

    RATES = {
        "spot_maker":   0.001,   # 0.10%
        "spot_taker":   0.001,   # 0.10%
        "margin_maker": 0.001,   # 0.10%
        "margin_taker": 0.001,   # 0.10%
    }

    @classmethod
    def calculate(
        cls,
        notional: float,
        market_type: str = "spot",
        order_type: str = "taker",
    ) -> float:
        key = f"{market_type}_{order_type}"
        rate = cls.RATES.get(key, 0.001)  # Default 0.10%
        return notional * rate


# ──────────────────────────────────────────────────────────────────────
# Main backtester
# ──────────────────────────────────────────────────────────────────────

class CryptoBacktester:
    """Backtester for Binance France: margin + spot + earn, NO futures/perp.

    Costs modelled:
      - Borrow interest (hourly accrual on margin positions)
      - Spot/margin commissions (0.10% maker+taker)
      - Tier-based slippage (spot book)
      - Earn yield (flexible savings returns)
      - Margin liquidation at margin_level < 1.1
    """

    def __init__(
        self,
        initial_capital: float = 15_000,
        leverage_default: float = 1.0,
        max_positions: int = 8,
        borrow_rates: dict[str, float] | None = None,
        earn_apy: dict[str, float] | None = None,
    ):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.leverage_default = leverage_default
        self.max_positions = max_positions
        self._borrow_rates = borrow_rates or BORROW_RATES_DAILY
        self._earn_apy = earn_apy or EARN_APY

        self.positions: list[CryptoPosition] = []
        self.earn_positions: list[EarnPosition] = []
        self.trades: list[CryptoTrade] = []
        self.equity_curve: list[dict] = []
        self._peak_equity = initial_capital
        self._liquidation_events: list[dict] = []

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(
        self,
        df: pd.DataFrame,
        strategy_fn,
        symbol: str = "BTCUSDT",
        **kwargs,
    ) -> dict:
        """Run a backtest on candle data.

        Args:
            df: DataFrame with timestamp, open, high, low, close, volume
            strategy_fn: callable(row, state) -> Signal dict or None
            symbol: trading pair
            **kwargs: passed to strategy_fn

        Returns:
            dict with trades, metrics, equity curve
        """
        if df.empty:
            return self._empty_result()

        df = df.sort_values("timestamp").reset_index(drop=True)
        state = {"positions": [], "capital": self.capital, **kwargs}

        for i in range(1, len(df)):
            # Anti-lookahead: use PREVIOUS candle for signals
            prev = df.iloc[i - 1]
            current = df.iloc[i]
            timestamp = current["timestamp"]

            # Apply hourly borrow interest on margin positions
            self.apply_borrow_interest(timestamp)

            # Apply earn yield
            self.apply_earn_yield(timestamp)

            # Check margin liquidation
            self.check_margin_liquidation(current["close"], timestamp)

            # Update position P&L and check stops
            self._update_positions(current, timestamp)

            # Generate signal from CLOSED candle (prev)
            state["positions"] = self.positions.copy()
            state["capital"] = self.capital
            state["equity"] = self._current_equity(current["close"])
            state["i"] = i
            state["earn_positions"] = self.earn_positions.copy()

            signal = strategy_fn(prev, state, **kwargs)

            if signal:
                self._execute_signal(signal, current, symbol, timestamp)

            # Record equity
            equity = self._current_equity(current["close"])
            self._peak_equity = max(self._peak_equity, equity)
            dd = (
                (equity - self._peak_equity) / self._peak_equity
                if self._peak_equity > 0
                else 0
            )
            self.equity_curve.append({
                "timestamp": timestamp,
                "equity": equity,
                "drawdown": dd,
                "positions": len(self.positions),
                "earn_positions": len(self.earn_positions),
                "total_borrow_cost": sum(
                    p.total_borrow_cost for p in self.positions if p.is_margin_borrow
                ),
            })

        # Close remaining positions at last price
        if self.positions:
            last = df.iloc[-1]
            for pos in self.positions[:]:
                self._close_position(
                    pos, last["close"], last["timestamp"], "end_of_data"
                )

        return self._compute_results(df)

    # ------------------------------------------------------------------
    # Borrow interest — hourly accrual (NOT 8h funding)
    # ------------------------------------------------------------------

    def apply_borrow_interest(self, timestamp: datetime):
        """Apply hourly borrow interest on margin positions.

        Binance cross-margin charges interest hourly on borrowed assets.
        Rate varies per asset: BTC ~0.02%/day, ETH ~0.024%/day,
        altcoins 0.05-0.24%/day.
        """
        for pos in self.positions:
            if not pos.is_margin_borrow or pos.borrowed_amount <= 0:
                continue

            if pos.last_interest_ts is None:
                pos.last_interest_ts = pos.entry_time

            # Calculate hours elapsed since last interest charge
            elapsed = timestamp - pos.last_interest_ts
            hours_elapsed = elapsed.total_seconds() / 3600.0

            if hours_elapsed < 1.0:
                continue

            # Number of full hours to charge
            full_hours = int(hours_elapsed)

            # Get daily rate for this asset
            daily_rate = self._get_borrow_rate(pos.symbol)
            hourly_rate = daily_rate / 24.0

            # Interest = borrowed_amount * hourly_rate * hours
            interest = pos.borrowed_amount * hourly_rate * full_hours
            pos.total_borrow_cost += interest
            pos.realized_pnl -= interest
            self.capital -= interest

            # Advance timestamp by full hours charged
            pos.last_interest_ts = pos.last_interest_ts + timedelta(
                hours=full_hours
            )

    def _get_borrow_rate(self, symbol: str) -> float:
        """Get daily borrow rate for a symbol."""
        if symbol in self._borrow_rates:
            return self._borrow_rates[symbol]
        # Tier fallback
        if symbol in SlippageModel.TIER_2:
            return self._borrow_rates.get("tier_2", 0.0008)
        return self._borrow_rates.get("tier_3", 0.0024)

    # ------------------------------------------------------------------
    # Earn yield
    # ------------------------------------------------------------------

    def apply_earn_yield(self, timestamp: datetime):
        """Simulate Binance Earn returns with historical APY rates.

        Earn is flexible savings — yield accrues daily (we approximate hourly
        for finer resolution in backtests).
        """
        for earn in self.earn_positions:
            elapsed = (timestamp - earn.start_time).total_seconds() / 3600.0
            if elapsed < 1.0:
                continue

            # Daily yield = amount * APY / 365
            # Hourly yield = daily / 24
            hourly_yield = earn.amount * earn.apy / 365.0 / 24.0
            hours_since_start = elapsed
            expected_total = hourly_yield * hours_since_start
            new_yield = expected_total - earn.yield_accrued

            if new_yield > 0:
                earn.yield_accrued += new_yield
                self.capital += new_yield

    # ------------------------------------------------------------------
    # Margin liquidation check
    # ------------------------------------------------------------------

    def check_margin_liquidation(
        self, current_price: float, timestamp: datetime
    ):
        """Check margin liquidation: margin_level = total_asset / total_debt.

        Binance cross-margin liquidates when margin_level < 1.1.
        We force-close at 1.1 to match real behavior.
        """
        margin_positions = [
            p for p in self.positions if p.is_margin_borrow
        ]
        if not margin_positions:
            return

        for pos in margin_positions[:]:
            # Total asset value for this position
            current_value = pos.qty * current_price
            # Total debt = borrowed amount + accrued interest
            total_debt = pos.borrowed_amount + pos.total_borrow_cost

            if total_debt <= 0:
                continue

            margin_level = current_value / total_debt

            if margin_level < 1.1:
                logger.warning(
                    f"MARGIN LIQUIDATION: {pos.symbol} margin_level="
                    f"{margin_level:.3f} < 1.1"
                )
                self._liquidation_events.append({
                    "symbol": pos.symbol,
                    "timestamp": timestamp,
                    "margin_level": round(margin_level, 4),
                    "borrowed": round(pos.borrowed_amount, 2),
                    "debt": round(total_debt, 2),
                    "value": round(current_value, 2),
                })
                self._close_position(
                    pos, current_price, timestamp, "margin_liquidation"
                )

    # ------------------------------------------------------------------
    # Borrow availability check
    # ------------------------------------------------------------------

    def check_borrow_availability(self, symbol: str) -> float:
        """Check if borrow rate is sustainable. Returns size multiplier.

        If borrow rate > 0.5%/day, reduce short size by 50%.
        Returns 1.0 (full size) or 0.5 (reduced).
        """
        daily_rate = self._get_borrow_rate(symbol)
        if daily_rate > BORROW_RATE_HIGH_THRESHOLD:
            logger.info(
                f"High borrow rate for {symbol}: {daily_rate*100:.2f}%/day "
                f"(>{BORROW_RATE_HIGH_THRESHOLD*100}%), reducing size 50%"
            )
            return 0.5
        return 1.0

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _update_positions(self, candle: pd.Series, timestamp: datetime):
        """Update positions: check stops, track MFE/MAE."""
        to_close = []
        for pos in self.positions:
            if pos.is_earn:
                continue  # Earn positions don't have stops

            price = candle["close"]
            high = candle["high"]
            low = candle["low"]

            # Track max favorable/adverse excursion
            if pos.is_long:
                pos.max_favorable = max(
                    pos.max_favorable,
                    (high - pos.entry_price) / pos.entry_price,
                )
                pos.max_adverse = min(
                    pos.max_adverse,
                    (low - pos.entry_price) / pos.entry_price,
                )
                if pos.stop_loss > 0 and low <= pos.stop_loss:
                    to_close.append((pos, pos.stop_loss, "stop_loss"))
                    continue
                if pos.take_profit > 0 and high >= pos.take_profit:
                    to_close.append((pos, pos.take_profit, "take_profit"))
                    continue
            else:
                pos.max_favorable = max(
                    pos.max_favorable,
                    (pos.entry_price - low) / pos.entry_price,
                )
                pos.max_adverse = min(
                    pos.max_adverse,
                    (pos.entry_price - high) / pos.entry_price,
                )
                if pos.stop_loss > 0 and high >= pos.stop_loss:
                    to_close.append((pos, pos.stop_loss, "stop_loss"))
                    continue
                if pos.take_profit > 0 and low <= pos.take_profit:
                    to_close.append((pos, pos.take_profit, "take_profit"))
                    continue

        for pos, exit_price, reason in to_close:
            self._close_position(pos, exit_price, timestamp, reason)

    def _execute_signal(
        self,
        signal: dict,
        candle: pd.Series,
        symbol: str,
        timestamp: datetime,
    ):
        """Execute a trading signal.

        signal dict keys:
          - action: "BUY", "SELL", "CLOSE", "EARN_DEPOSIT", "EARN_REDEEM"
          - qty: position size (optional)
          - pct: % of capital (optional)
          - stop_loss, take_profit: price levels
          - leverage: override (optional, max 3x for margin)
          - market_type: "spot" or "margin"
          - strategy: strategy name
          - earn_amount: for EARN_DEPOSIT
        """
        action = signal.get("action", "").upper()
        price = candle["open"]  # Execute at OPEN of next candle (no lookahead)

        # Earn operations
        if action == "EARN_DEPOSIT":
            self._earn_deposit(signal, timestamp)
            return
        if action == "EARN_REDEEM":
            self._earn_redeem(signal, timestamp)
            return

        if action == "CLOSE":
            target = signal.get("symbol", symbol)
            for pos in self.positions[:]:
                if pos.symbol == target and not pos.is_earn:
                    self._close_position(
                        pos, price, timestamp, signal.get("reason", "signal")
                    )
            return

        if action not in ("BUY", "SELL"):
            return

        if len(self.positions) >= self.max_positions:
            return

        direction = 1 if action == "BUY" else -1
        market_type = signal.get("market_type", "spot")
        leverage = signal.get("leverage", self.leverage_default)

        # Enforce max 3x margin leverage, 1x spot
        if market_type == "spot":
            leverage = 1.0
        else:
            leverage = min(leverage, 3.0)

        # Size calculation
        qty = signal.get("qty")
        if not qty:
            pct = signal.get("pct", 0.1)  # Default 10% of capital
            notional = self.capital * pct * leverage
            qty = notional / price if price > 0 else 0

        if qty <= 0:
            return

        # Check borrow availability for shorts (margin)
        is_borrow = False
        borrowed_asset = ""
        borrowed_amount = 0.0

        if direction == -1 and market_type == "margin":
            # Short = borrow the asset and sell it
            is_borrow = True
            borrowed_asset = symbol.replace("USDT", "")
            borrowed_amount = qty * price  # USDT-denominated debt
            size_mult = self.check_borrow_availability(symbol)
            if size_mult < 1.0:
                qty *= size_mult
                borrowed_amount *= size_mult

        elif direction == 1 and leverage > 1.0 and market_type == "margin":
            # Leveraged long = borrow USDT
            is_borrow = True
            borrowed_asset = "USDT"
            # Amount borrowed is the leveraged portion
            own_capital = qty * price / leverage
            borrowed_amount = qty * price - own_capital

        # Apply slippage
        slippage = SlippageModel.estimate(symbol, qty * price)
        fill_price = (
            price * (1 + slippage) if direction > 0
            else price * (1 - slippage)
        )
        slippage_cost = abs(fill_price - price) * qty

        # Apply commission (spot/margin 0.10%)
        commission = CommissionModel.calculate(
            qty * fill_price,
            market_type=market_type,
            order_type="taker",
        )
        self.capital -= commission

        pos = CryptoPosition(
            symbol=symbol,
            direction=direction,
            qty=qty,
            entry_price=fill_price,
            entry_time=timestamp,
            stop_loss=signal.get("stop_loss", 0),
            take_profit=signal.get("take_profit", 0),
            leverage=leverage,
            market_type=market_type,
            strategy=signal.get("strategy", ""),
            commissions_paid=commission,
            is_margin_borrow=is_borrow,
            borrowed_asset=borrowed_asset,
            borrowed_amount=borrowed_amount,
            last_interest_ts=timestamp,
        )
        self.positions.append(pos)

    def _close_position(
        self,
        pos: CryptoPosition,
        price: float,
        timestamp: datetime,
        reason: str,
    ):
        """Close a position and record the trade."""
        # Apply exit slippage
        slippage = SlippageModel.estimate(pos.symbol, pos.qty * price)
        fill_price = (
            price * (1 - slippage) if pos.is_long
            else price * (1 + slippage)
        )
        slippage_cost = abs(fill_price - price) * pos.qty

        # Exit commission
        commission = CommissionModel.calculate(
            pos.qty * fill_price, market_type=pos.market_type
        )

        # P&L calculation
        gross_pnl = (fill_price - pos.entry_price) * pos.qty * pos.direction
        net_pnl = (
            gross_pnl
            - pos.total_borrow_cost
            - pos.commissions_paid
            - commission
        )
        pnl_pct = (
            net_pnl / (pos.entry_price * pos.qty) * 100
            if pos.entry_price > 0
            else 0
        )

        self.capital += net_pnl

        holding = (
            (timestamp - pos.entry_time).total_seconds() / 3600
            if hasattr(timestamp, "hour")
            else 0
        )

        trade = CryptoTrade(
            symbol=pos.symbol,
            direction="LONG" if pos.is_long else "SHORT",
            qty=pos.qty,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            borrow_cost=pos.total_borrow_cost,
            commission=pos.commissions_paid + commission,
            slippage_cost=slippage_cost,
            strategy=pos.strategy,
            exit_reason=reason,
            holding_hours=holding,
            leverage=pos.leverage,
            market_type=pos.market_type,
            earn_yield=pos.earn_yield_accrued,
        )
        self.trades.append(trade)

        if pos in self.positions:
            self.positions.remove(pos)

    # ------------------------------------------------------------------
    # Earn operations
    # ------------------------------------------------------------------

    def _earn_deposit(self, signal: dict, timestamp: datetime):
        """Deposit funds into Binance Earn (flexible savings)."""
        amount = signal.get("earn_amount", 0)
        symbol = signal.get("symbol", "USDTUSDT")

        if amount <= 0 or amount > self.capital:
            return

        apy = self._earn_apy.get(symbol, self._earn_apy.get("default", 0.03))

        earn_pos = EarnPosition(
            symbol=symbol,
            amount=amount,
            apy=apy,
            start_time=timestamp,
            is_locked=signal.get("locked", False),
            lock_days=signal.get("lock_days", 0),
        )
        self.earn_positions.append(earn_pos)
        self.capital -= amount
        logger.info(
            f"EARN DEPOSIT: {symbol} ${amount:.0f} at {apy*100:.1f}% APY"
        )

    def _earn_redeem(self, signal: dict, timestamp: datetime):
        """Redeem funds from Binance Earn."""
        symbol = signal.get("symbol", "USDTUSDT")
        to_redeem = []
        for earn in self.earn_positions:
            if earn.symbol == symbol:
                # Return principal + accrued yield
                total = earn.amount + earn.yield_accrued
                self.capital += total
                to_redeem.append(earn)
                logger.info(
                    f"EARN REDEEM: {symbol} ${earn.amount:.0f} + "
                    f"${earn.yield_accrued:.2f} yield"
                )

        for earn in to_redeem:
            self.earn_positions.remove(earn)

    # ------------------------------------------------------------------
    # Equity calculation
    # ------------------------------------------------------------------

    def _current_equity(self, current_price: float) -> float:
        """Calculate current equity including open positions and earn."""
        unrealized = sum(
            (current_price - p.entry_price) * p.qty * p.direction
            for p in self.positions
            if not p.is_earn
        )
        earn_value = sum(
            e.amount + e.yield_accrued for e in self.earn_positions
        )
        return self.capital + unrealized + earn_value

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _compute_results(self, df: pd.DataFrame) -> dict:
        """Compute backtest metrics from trades."""
        if not self.trades:
            return self._empty_result()

        pnls = [t.pnl for t in self.trades]
        pnl_pcts = [t.pnl_pct for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        # Equity curve metrics
        eq = pd.DataFrame(self.equity_curve)
        if not eq.empty:
            max_dd = eq["drawdown"].min() * 100  # Already negative
        else:
            max_dd = 0

        # Sharpe ratio (annualized)
        if len(pnl_pcts) > 1:
            avg_pnl = np.mean(pnl_pcts)
            std_pnl = np.std(pnl_pcts, ddof=1)
            trades_per_year = len(self.trades) * 365 / max(
                (self.trades[-1].exit_time - self.trades[0].entry_time).days, 1
            )
            sharpe = (
                avg_pnl / std_pnl * np.sqrt(trades_per_year)
                if std_pnl > 0
                else 0
            )
        else:
            sharpe = 0

        total_pnl = sum(pnls)
        total_borrow = sum(t.borrow_cost for t in self.trades)
        total_commission = sum(t.commission for t in self.trades)
        total_slippage = sum(t.slippage_cost for t in self.trades)
        total_earn_yield = sum(t.earn_yield for t in self.trades) + sum(
            e.yield_accrued for e in self.earn_positions
        )

        return {
            "trades": self.trades,
            "n_trades": len(self.trades),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(
                total_pnl / self.initial_capital * 100, 2
            ),
            "sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "win_rate": (
                round(len(wins) / len(self.trades) * 100, 1)
                if self.trades
                else 0
            ),
            "avg_win": round(np.mean(wins), 2) if wins else 0,
            "avg_loss": round(np.mean(losses), 2) if losses else 0,
            "profit_factor": (
                round(sum(wins) / abs(sum(losses)), 2)
                if losses and sum(losses) != 0
                else float("inf")
            ),
            "avg_holding_hours": round(
                np.mean([t.holding_hours for t in self.trades]), 1
            ),
            "total_borrow_cost": round(total_borrow, 2),
            "total_commissions": round(total_commission, 2),
            "total_slippage_cost": round(total_slippage, 2),
            "total_earn_yield": round(total_earn_yield, 2),
            "cost_drag_pct": round(
                (total_borrow + total_commission + total_slippage)
                / self.initial_capital
                * 100,
                2,
            ),
            "earn_yield_pct": round(
                total_earn_yield / self.initial_capital * 100, 2
            ),
            "liquidation_events": len(self._liquidation_events),
            "liquidation_details": self._liquidation_events,
            "equity_curve": self.equity_curve,
            "final_equity": round(self.capital, 2),
        }

    def _empty_result(self) -> dict:
        return {
            "trades": [],
            "n_trades": 0,
            "total_pnl": 0,
            "total_return_pct": 0,
            "sharpe": 0,
            "max_drawdown_pct": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "avg_holding_hours": 0,
            "total_borrow_cost": 0,
            "total_commissions": 0,
            "total_slippage_cost": 0,
            "total_earn_yield": 0,
            "cost_drag_pct": 0,
            "earn_yield_pct": 0,
            "liquidation_events": 0,
            "liquidation_details": [],
            "equity_curve": [],
            "final_equity": self.initial_capital,
        }


# ──────────────────────────────────────────────────────────────────────
# Walk-Forward Validation
# ──────────────────────────────────────────────────────────────────────

class CryptoWalkForward:
    """Walk-forward validation adapted for margin+spot+earn strategies.

    Parameters (adapted from V1 for margin/earn specifics):
      - Train: 6 months, Test: 2 months (rolling windows)
      - Minimum 4 windows (32 months history minimum)
      - Criteria: OOS/IS ratio > 0.4 AND >= 50% windows profitable
      - For short-history assets: Train 4m, Test 1m, ratio > 0.5
      - Additional: borrow cost drag < 3% per window, no liquidation events
    """

    def __init__(
        self,
        train_months: int = 6,
        test_months: int = 2,
        min_windows: int = 4,
        min_oos_ratio: float = 0.4,
        min_profitable_pct: float = 0.5,
        max_borrow_drag_pct: float = 3.0,
        max_liquidations: int = 0,
    ):
        self.train_months = train_months
        self.test_months = test_months
        self.min_windows = min_windows
        self.min_oos_ratio = min_oos_ratio
        self.min_profitable_pct = min_profitable_pct
        self.max_borrow_drag_pct = max_borrow_drag_pct
        self.max_liquidations = max_liquidations

    def validate(
        self,
        df: pd.DataFrame,
        strategy_fn,
        symbol: str = "BTCUSDT",
        initial_capital: float = 15_000,
        borrow_rates: dict[str, float] | None = None,
        earn_apy: dict[str, float] | None = None,
        **kwargs,
    ) -> dict:
        """Run walk-forward validation.

        Returns:
            dict with verdict, windows, and aggregate metrics
        """
        if df.empty or "timestamp" not in df.columns:
            return {"verdict": "REJECTED", "reason": "no_data", "windows": []}

        df = df.sort_values("timestamp").reset_index(drop=True)
        start = df["timestamp"].min()
        end = df["timestamp"].max()
        total_months = (end - start).days / 30

        required_months = (
            (self.train_months + self.test_months) * self.min_windows
        )
        if total_months < required_months:
            return {
                "verdict": "REJECTED",
                "reason": (
                    f"insufficient_data ({total_months:.0f} months "
                    f"< {required_months})"
                ),
                "windows": [],
            }

        windows = []
        window_start = start

        while True:
            train_end = window_start + timedelta(days=self.train_months * 30)
            test_end = train_end + timedelta(days=self.test_months * 30)

            if test_end > end:
                break

            # Split data
            train_df = df[
                (df["timestamp"] >= window_start)
                & (df["timestamp"] < train_end)
            ].copy()
            test_df = df[
                (df["timestamp"] >= train_end)
                & (df["timestamp"] < test_end)
            ].copy()

            if len(train_df) < 100 or len(test_df) < 20:
                window_start = window_start + timedelta(
                    days=self.test_months * 30
                )
                continue

            # Run IS (in-sample)
            bt_is = CryptoBacktester(
                initial_capital=initial_capital,
                borrow_rates=borrow_rates,
                earn_apy=earn_apy,
            )
            is_result = bt_is.run(train_df, strategy_fn, symbol, **kwargs)

            # Run OOS (out-of-sample)
            bt_oos = CryptoBacktester(
                initial_capital=initial_capital,
                borrow_rates=borrow_rates,
                earn_apy=earn_apy,
            )
            oos_result = bt_oos.run(test_df, strategy_fn, symbol, **kwargs)

            is_sharpe = is_result.get("sharpe", 0)
            oos_sharpe = oos_result.get("sharpe", 0)
            oos_profitable = oos_result.get("total_pnl", 0) > 0
            ratio = oos_sharpe / is_sharpe if is_sharpe > 0 else 0
            borrow_drag = oos_result.get("cost_drag_pct", 0)
            liquidations = oos_result.get("liquidation_events", 0)

            windows.append({
                "window": len(windows) + 1,
                "train_start": (
                    window_start.isoformat()
                    if hasattr(window_start, "isoformat")
                    else str(window_start)
                ),
                "train_end": (
                    train_end.isoformat()
                    if hasattr(train_end, "isoformat")
                    else str(train_end)
                ),
                "test_start": (
                    train_end.isoformat()
                    if hasattr(train_end, "isoformat")
                    else str(train_end)
                ),
                "test_end": (
                    test_end.isoformat()
                    if hasattr(test_end, "isoformat")
                    else str(test_end)
                ),
                "is_sharpe": round(is_sharpe, 2),
                "oos_sharpe": round(oos_sharpe, 2),
                "oos_pnl": round(oos_result.get("total_pnl", 0), 2),
                "oos_trades": oos_result.get("n_trades", 0),
                "oos_profitable": oos_profitable,
                "ratio": round(ratio, 2),
                "borrow_drag_pct": round(borrow_drag, 2),
                "earn_yield_pct": round(
                    oos_result.get("earn_yield_pct", 0), 2
                ),
                "liquidation_events": liquidations,
            })

            window_start = window_start + timedelta(
                days=self.test_months * 30
            )

        if len(windows) < self.min_windows:
            return {
                "verdict": "REJECTED",
                "reason": (
                    f"insufficient_windows ({len(windows)} "
                    f"< {self.min_windows})"
                ),
                "windows": windows,
            }

        # Aggregate
        avg_ratio = np.mean([w["ratio"] for w in windows])
        pct_profitable = (
            sum(1 for w in windows if w["oos_profitable"]) / len(windows)
        )
        avg_oos_sharpe = np.mean([w["oos_sharpe"] for w in windows])
        avg_is_sharpe = np.mean([w["is_sharpe"] for w in windows])
        total_oos_trades = sum(w["oos_trades"] for w in windows)
        avg_borrow_drag = np.mean([w["borrow_drag_pct"] for w in windows])
        total_liquidations = sum(
            w["liquidation_events"] for w in windows
        )

        # Verdict — additional margin/earn criteria
        borrow_ok = avg_borrow_drag <= self.max_borrow_drag_pct
        liquidation_ok = total_liquidations <= self.max_liquidations
        ratio_ok = avg_ratio >= self.min_oos_ratio
        profit_ok = pct_profitable >= self.min_profitable_pct

        if ratio_ok and profit_ok and borrow_ok and liquidation_ok:
            verdict = "VALIDATED"
        elif (
            avg_ratio >= self.min_oos_ratio * 0.7
            and pct_profitable >= self.min_profitable_pct * 0.8
            and borrow_ok
        ):
            verdict = "BORDERLINE"
        else:
            verdict = "REJECTED"

        reason_parts = [
            f"ratio={avg_ratio:.2f} (min {self.min_oos_ratio})",
            f"profitable={pct_profitable:.0%} (min {self.min_profitable_pct:.0%})",
            f"borrow_drag={avg_borrow_drag:.1f}% (max {self.max_borrow_drag_pct}%)",
            f"liquidations={total_liquidations} (max {self.max_liquidations})",
            f"OOS trades={total_oos_trades}",
        ]
        reason = ", ".join(reason_parts)

        return {
            "verdict": verdict,
            "reason": reason,
            "n_windows": len(windows),
            "avg_ratio": round(avg_ratio, 2),
            "avg_oos_sharpe": round(avg_oos_sharpe, 2),
            "avg_is_sharpe": round(avg_is_sharpe, 2),
            "pct_oos_profitable": round(pct_profitable, 2),
            "pct_oos_sharpe_positive": round(
                sum(1 for w in windows if w["oos_sharpe"] > 0) / len(windows),
                2,
            ),
            "avg_borrow_drag_pct": round(avg_borrow_drag, 2),
            "total_liquidation_events": total_liquidations,
            "n_trades": total_oos_trades,
            "windows": windows,
        }
