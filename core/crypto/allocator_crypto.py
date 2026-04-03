"""
CryptoAllocator V2 — Capital allocation for Binance France $15K.

3-wallet model (NO futures/perp):
  - Spot wallet:   $6,000 (40%)
  - Margin wallet: $4,000 (27%)
  - Earn wallet:   $3,000 (20%)
  - Cash reserve:  $2,000 (13%)

8 strategies with regime-based allocation:
  trend(20%), altcoin_rs(15%), mean_rev(12%), vol_breakout(10%),
  dominance(10%), carry(13%), liquidation(10%), weekend(10%)

3 regimes (BULL/BEAR/CHOP) with different allocation profiles.
Transition speed: max 10%/day towards target allocation.

Wallet transfer logic:
  - Inter-wallet transfers allowed (spot <-> margin <-> earn)
  - Min transfer: $100
  - Earn redemption: instant for flexible, T+1 for locked
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Regime detection
# ──────────────────────────────────────────────────────────────────────

class CryptoRegime:
    BULL = "BULL"
    BEAR = "BEAR"
    CHOP = "CHOP"


def detect_crypto_regime(
    btc_prices: pd.Series,
    funding_rate: float = 0,
    ema_period: int = 50,
) -> str:
    """Detect crypto market regime.

    Args:
        btc_prices: BTC close prices (daily)
        funding_rate: current funding rate (or borrow demand proxy)
        ema_period: EMA period for trend detection

    Returns:
        CryptoRegime
    """
    if len(btc_prices) < ema_period + 5:
        return CryptoRegime.CHOP

    ema = btc_prices.ewm(span=ema_period, adjust=False).mean()
    current_price = btc_prices.iloc[-1]
    current_ema = ema.iloc[-1]

    above_ema = current_price > current_ema
    funding_positive = funding_rate > 0

    if above_ema and funding_positive:
        return CryptoRegime.BULL
    elif not above_ema and not funding_positive:
        return CryptoRegime.BEAR
    else:
        return CryptoRegime.CHOP


# ──────────────────────────────────────────────────────────────────────
# Wallet model
# ──────────────────────────────────────────────────────────────────────

class WalletType:
    SPOT = "spot"
    MARGIN = "margin"
    EARN = "earn"
    CASH = "cash"


class WalletManager:
    """Manage 3 wallets + cash reserve."""

    # Default allocation: $15K total
    DEFAULT_WALLETS = {
        WalletType.SPOT:   6_000,   # 40%
        WalletType.MARGIN: 4_000,   # 27%
        WalletType.EARN:   3_000,   # 20%
        WalletType.CASH:   2_000,   # 13%
    }

    MIN_TRANSFER = 100  # Minimum $100 per transfer

    def __init__(
        self,
        total_capital: float = 15_000,
        wallets: dict[str, float] | None = None,
    ):
        self.total_capital = total_capital
        self._wallets: dict[str, float] = {}

        if wallets:
            self._wallets = dict(wallets)
        else:
            # Scale default wallets to actual capital
            ratio = total_capital / 15_000
            self._wallets = {
                k: round(v * ratio, 2)
                for k, v in self.DEFAULT_WALLETS.items()
            }

        self._transfer_history: list[dict] = []

    @property
    def balances(self) -> dict[str, float]:
        return dict(self._wallets)

    def get_balance(self, wallet: str) -> float:
        return self._wallets.get(wallet, 0)

    def transfer(
        self,
        from_wallet: str,
        to_wallet: str,
        amount: float,
    ) -> tuple[bool, str]:
        """Transfer between wallets.

        Returns (success, message).
        """
        if amount < self.MIN_TRANSFER:
            return False, f"amount ${amount:.0f} < min ${self.MIN_TRANSFER}"

        if from_wallet not in self._wallets:
            return False, f"unknown wallet: {from_wallet}"
        if to_wallet not in self._wallets:
            return False, f"unknown wallet: {to_wallet}"

        if self._wallets[from_wallet] < amount:
            return False, (
                f"insufficient balance: {from_wallet} has "
                f"${self._wallets[from_wallet]:.0f}, need ${amount:.0f}"
            )

        self._wallets[from_wallet] -= amount
        self._wallets[to_wallet] += amount

        self._transfer_history.append({
            "from": from_wallet,
            "to": to_wallet,
            "amount": amount,
            "timestamp": datetime.now(UTC).isoformat(),
        })

        logger.info(
            f"Wallet transfer: {from_wallet} -> {to_wallet} ${amount:.0f}"
        )
        return True, "OK"

    def update_balance(self, wallet: str, new_balance: float):
        """Update a wallet balance (e.g. after P&L)."""
        if wallet in self._wallets:
            self._wallets[wallet] = new_balance

    def status(self) -> dict:
        total = sum(self._wallets.values())
        return {
            "wallets": dict(self._wallets),
            "total": round(total, 2),
            "pct": {
                k: round(v / total * 100, 1) if total > 0 else 0
                for k, v in self._wallets.items()
            },
            "transfers_count": len(self._transfer_history),
        }


# ──────────────────────────────────────────────────────────────────────
# Strategy-to-wallet mapping
# ──────────────────────────────────────────────────────────────────────

STRATEGY_WALLET_MAP = {
    "trend":           WalletType.SPOT,
    "altcoin_rs":      WalletType.SPOT,
    "mean_rev":        WalletType.MARGIN,
    "vol_breakout":    WalletType.SPOT,
    "dominance":       WalletType.SPOT,
    "carry":           WalletType.EARN,      # Earn yield component
    "liquidation":     WalletType.MARGIN,    # Needs margin for shorts
    "weekend":         WalletType.SPOT,
}


# ──────────────────────────────────────────────────────────────────────
# Regime allocations — 8 strategies
# ──────────────────────────────────────────────────────────────────────

REGIME_ALLOCATIONS = {
    CryptoRegime.BULL: {
        "trend":         0.20,   # 20%
        "altcoin_rs":    0.15,   # 15%
        "mean_rev":      0.12,   # 12%
        "vol_breakout":  0.10,   # 10%
        "dominance":     0.10,   # 10%
        "carry":         0.13,   # 13%
        "liquidation":   0.10,   # 10%
        "weekend":       0.10,   # 10%
    },
    CryptoRegime.BEAR: {
        "trend":         0.20,   # 20% (shorts)
        "altcoin_rs":    0.10,   # 10% reduced
        "mean_rev":      0.15,   # 15% increased
        "vol_breakout":  0.10,   # 10%
        "dominance":     0.15,   # 15% increased
        "carry":         0.15,   # 15% (earn yield = safe income)
        "liquidation":   0.15,   # 15% increased (liquidation cascades)
        "weekend":       0.00,   # 0% — weekend gaps too risky in bear
    },
    CryptoRegime.CHOP: {
        "trend":         0.05,   # 5% — trend fails in chop
        "altcoin_rs":    0.10,   # 10%
        "mean_rev":      0.20,   # 20% — MR thrives in chop
        "vol_breakout":  0.15,   # 15% — breakout from range
        "dominance":     0.10,   # 10%
        "carry":         0.20,   # 20% — earn yield = stable income
        "liquidation":   0.10,   # 10%
        "weekend":       0.10,   # 10%
    },
}

# Transition speed: max 10% change per day
TRANSITION_SPEED = 0.10


# ──────────────────────────────────────────────────────────────────────
# Allocator V2
# ──────────────────────────────────────────────────────────────────────

class CryptoAllocator:
    """Allocator V2 for crypto strategies with 3-wallet model.

    $15K total:
      - Spot $6K, Margin $4K, Earn $3K, Cash $2K
      - 8 strategies with regime-based allocation
      - Smooth transitions (10%/day max)
    """

    def __init__(
        self,
        total_capital: float = 15_000,
        regime_allocations: dict | None = None,
        transition_speed: float = TRANSITION_SPEED,
        wallet_config: dict[str, float] | None = None,
    ):
        self.total_capital = total_capital
        self.regime_allocations = regime_allocations or REGIME_ALLOCATIONS
        self.transition_speed = transition_speed
        self.wallet_manager = WalletManager(total_capital, wallet_config)
        self._current_allocations: dict[str, float] = {}
        self._current_regime = CryptoRegime.CHOP
        self._last_update: datetime | None = None
        self._regime_history: list[dict] = []

    @property
    def current_regime(self) -> str:
        return self._current_regime

    @property
    def current_allocations(self) -> dict[str, float]:
        return dict(self._current_allocations)

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    def update(
        self,
        btc_prices: pd.Series,
        funding_rate: float = 0,
        kill_switch_active: bool = False,
        deleveraging_factor: float = 1.0,
    ) -> dict[str, dict]:
        """Update allocations based on current market regime.

        Args:
            btc_prices: BTC daily close prices
            funding_rate: current borrow demand rate (proxy)
            kill_switch_active: if True, all allocations = 0
            deleveraging_factor: 0-1 multiplier (from risk manager)

        Returns:
            {strategy: {pct, capital, regime, wallet}}
        """
        if kill_switch_active:
            self._current_allocations = {
                s: 0 for s in REGIME_ALLOCATIONS[CryptoRegime.CHOP]
            }
            return self._format_result()

        # Detect regime
        regime = detect_crypto_regime(btc_prices, funding_rate)
        old_regime = self._current_regime
        self._current_regime = regime

        if regime != old_regime:
            self._regime_history.append({
                "from": old_regime,
                "to": regime,
                "timestamp": datetime.now(UTC).isoformat(),
            })
            logger.info(
                f"Regime change: {old_regime} -> {regime}"
            )

        target = self.regime_allocations.get(
            regime, REGIME_ALLOCATIONS[CryptoRegime.CHOP]
        )

        # Smooth transition (max 10%/day change per strategy)
        if not self._current_allocations:
            self._current_allocations = dict(target)
        else:
            for strategy, target_pct in target.items():
                current = self._current_allocations.get(strategy, 0)
                diff = target_pct - current
                change = max(
                    -self.transition_speed,
                    min(self.transition_speed, diff),
                )
                self._current_allocations[strategy] = current + change

            # Handle strategies that exist in current but not in target
            for strategy in list(self._current_allocations.keys()):
                if strategy not in target:
                    current = self._current_allocations[strategy]
                    change = max(
                        -self.transition_speed,
                        min(self.transition_speed, -current),
                    )
                    self._current_allocations[strategy] = current + change
                    if self._current_allocations[strategy] <= 0.001:
                        del self._current_allocations[strategy]

        # Apply deleveraging factor
        if deleveraging_factor < 1.0:
            self._current_allocations = {
                s: pct * deleveraging_factor
                for s, pct in self._current_allocations.items()
            }

        self._last_update = datetime.now(UTC)
        return self._format_result()

    # ------------------------------------------------------------------
    # Format & helpers
    # ------------------------------------------------------------------

    def _format_result(self) -> dict[str, dict]:
        """Format allocations as {strategy: {pct, capital, regime, wallet}}."""
        result = {}
        for strategy, pct in self._current_allocations.items():
            wallet = STRATEGY_WALLET_MAP.get(strategy, WalletType.SPOT)
            wallet_balance = self.wallet_manager.get_balance(wallet)
            # Capital for this strategy is limited by wallet balance
            strategy_capital = min(
                pct * self.total_capital,
                wallet_balance,
            )
            result[strategy] = {
                "pct": round(pct * 100, 1),
                "capital": round(strategy_capital, 2),
                "regime": self._current_regime,
                "wallet": wallet,
                "wallet_balance": round(wallet_balance, 2),
            }
        return result

    def get_allocation(self, strategy: str) -> float:
        """Get current allocation for a strategy in dollars."""
        pct = self._current_allocations.get(strategy, 0)
        wallet = STRATEGY_WALLET_MAP.get(strategy, WalletType.SPOT)
        wallet_balance = self.wallet_manager.get_balance(wallet)
        return min(pct * self.total_capital, wallet_balance)

    # ------------------------------------------------------------------
    # Order validation
    # ------------------------------------------------------------------

    def validate_order_size(
        self,
        strategy: str,
        order_notional: float,
    ) -> tuple[bool, str]:
        """Validate that an order doesn't exceed the strategy's allocation
        AND that the wallet has sufficient balance.
        """
        allocation = self.get_allocation(strategy)
        wallet = STRATEGY_WALLET_MAP.get(strategy, WalletType.SPOT)
        wallet_balance = self.wallet_manager.get_balance(wallet)

        if order_notional > allocation * 1.1:  # 10% tolerance
            return False, (
                f"Order ${order_notional:.0f} exceeds allocation "
                f"${allocation:.0f} for {strategy}"
            )

        if order_notional > wallet_balance:
            return False, (
                f"Order ${order_notional:.0f} exceeds {wallet} wallet "
                f"balance ${wallet_balance:.0f}"
            )

        # Minimum order size ($10)
        if order_notional < 10:
            return False, f"Order ${order_notional:.2f} below min $10"

        return True, "OK"

    # ------------------------------------------------------------------
    # Wallet transfer logic
    # ------------------------------------------------------------------

    def rebalance_wallets(self) -> list[dict]:
        """Rebalance wallets based on current strategy allocations.

        Returns list of transfers executed.
        """
        transfers = []

        # Calculate target wallet allocations
        target_wallets = {
            WalletType.SPOT: 0,
            WalletType.MARGIN: 0,
            WalletType.EARN: 0,
        }

        for strategy, pct in self._current_allocations.items():
            wallet = STRATEGY_WALLET_MAP.get(strategy, WalletType.SPOT)
            if wallet in target_wallets:
                target_wallets[wallet] += pct * self.total_capital

        # Cash reserve target = total - sum(wallet targets)
        total_target = sum(target_wallets.values())
        cash_target = max(
            self.total_capital * 0.13,  # Min 13% cash
            self.total_capital - total_target,
        )

        # Check if transfers are needed
        for wallet, target in target_wallets.items():
            current = self.wallet_manager.get_balance(wallet)
            diff = target - current

            if abs(diff) < WalletManager.MIN_TRANSFER:
                continue

            if diff > 0:
                # Need more in this wallet, take from cash
                cash = self.wallet_manager.get_balance(WalletType.CASH)
                transfer_amount = min(diff, cash - cash_target)
                if transfer_amount >= WalletManager.MIN_TRANSFER:
                    ok, msg = self.wallet_manager.transfer(
                        WalletType.CASH, wallet, transfer_amount
                    )
                    if ok:
                        transfers.append({
                            "from": WalletType.CASH,
                            "to": wallet,
                            "amount": transfer_amount,
                        })
            elif diff < 0:
                # Excess in this wallet, send to cash
                transfer_amount = abs(diff)
                if transfer_amount >= WalletManager.MIN_TRANSFER:
                    ok, msg = self.wallet_manager.transfer(
                        wallet, WalletType.CASH, transfer_amount
                    )
                    if ok:
                        transfers.append({
                            "from": wallet,
                            "to": WalletType.CASH,
                            "amount": transfer_amount,
                        })

        return transfers

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return current allocator status."""
        return {
            "regime": self._current_regime,
            "total_capital": self.total_capital,
            "allocations": self._format_result(),
            "wallets": self.wallet_manager.status(),
            "last_update": (
                self._last_update.isoformat() if self._last_update else None
            ),
            "deployed_pct": round(
                sum(self._current_allocations.values()) * 100, 1
            ),
            "regime_history": self._regime_history[-10:],  # Last 10 changes
        }
