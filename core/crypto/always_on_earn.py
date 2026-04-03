"""U5-02: Always-On Earn Crypto — idle capital always in Earn.

Rules:
  - USDC idle > $100 for > 1h → subscribe to flexible Earn
  - BTC idle > 0.001 for > 1h → subscribe to flexible Earn
  - When a strategy needs capital → redeem from Earn (instant for flexible)

Yield: USDC ~3-5% APY, BTC ~1-2% APY
Impact: ~$150-250/year passive income on $5K in Earn
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("crypto.always_on_earn")


@dataclass
class EarnConfig:
    """Config for an always-on earn position."""
    asset: str
    min_idle_amount: float    # Min idle before subscribing
    min_idle_hours: float     # Hours idle before subscribing
    product_type: str = "flexible"  # "flexible" or "locked_30d"
    estimated_apy_pct: float = 3.0


DEFAULT_EARN_CONFIGS = [
    EarnConfig(asset="USDC", min_idle_amount=100, min_idle_hours=1, estimated_apy_pct=4.0),
    EarnConfig(asset="BTC", min_idle_amount=0.001, min_idle_hours=1, estimated_apy_pct=1.5),
    EarnConfig(asset="ETH", min_idle_amount=0.01, min_idle_hours=1, estimated_apy_pct=1.0),
    EarnConfig(asset="BNB", min_idle_amount=0.1, min_idle_hours=2, estimated_apy_pct=0.5),
]


@dataclass
class EarnAction:
    """An action to subscribe/redeem from Earn."""
    action: str  # "SUBSCRIBE" or "REDEEM"
    asset: str
    amount: float
    product: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "asset": self.asset,
            "amount": self.amount,
            "product": self.product,
            "reason": self.reason,
        }


@dataclass
class EarnStatus:
    """Current earn deployment status."""
    total_in_earn_usd: float = 0.0
    daily_yield_usd: float = 0.0
    annualized_yield_usd: float = 0.0
    by_asset: Dict[str, Dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_in_earn_usd": round(self.total_in_earn_usd, 2),
            "daily_yield_usd": round(self.daily_yield_usd, 4),
            "annualized_yield_usd": round(self.annualized_yield_usd, 2),
            "by_asset": self.by_asset,
        }


class AlwaysOnEarn:
    """Manages automatic Earn subscriptions for idle crypto capital.

    Usage:
        earn = AlwaysOnEarn()
        actions = earn.check_idle_capital(
            balances={"USDC": 3000, "BTC": 0.05},
            in_earn={"USDC": 2000, "BTC": 0.02},
            prices_usd={"BTC": 68000, "ETH": 3500, "BNB": 600},
        )
        for action in actions:
            if action.action == "SUBSCRIBE":
                binance.subscribe_earn(action.asset, action.amount)
    """

    def __init__(self, configs: List[EarnConfig] = None):
        self.configs = configs or DEFAULT_EARN_CONFIGS
        self._idle_since: Dict[str, datetime] = {}

    def check_idle_capital(
        self,
        balances: Dict[str, float],
        in_earn: Dict[str, float] = None,
        prices_usd: Dict[str, float] = None,
    ) -> List[EarnAction]:
        """Check for idle capital that should go into Earn."""
        in_earn = in_earn or {}
        prices_usd = prices_usd or {"BTC": 68000, "ETH": 3500, "BNB": 600, "USDC": 1.0}
        actions = []
        now = datetime.now()

        for cfg in self.configs:
            available = balances.get(cfg.asset, 0)
            already_in_earn = in_earn.get(cfg.asset, 0)
            idle = available - already_in_earn

            if idle > cfg.min_idle_amount:
                # Track idle duration
                if cfg.asset not in self._idle_since:
                    self._idle_since[cfg.asset] = now

                idle_hours = (now - self._idle_since[cfg.asset]).total_seconds() / 3600

                if idle_hours >= cfg.min_idle_hours:
                    actions.append(EarnAction(
                        action="SUBSCRIBE",
                        asset=cfg.asset,
                        amount=idle,
                        product=f"{cfg.product_type}_earn",
                        reason=f"{cfg.asset} idle {idle:.4f} for {idle_hours:.1f}h > {cfg.min_idle_hours}h",
                    ))
            else:
                # Reset idle tracker
                self._idle_since.pop(cfg.asset, None)

        return actions

    def check_need_redeem(
        self,
        needed: Dict[str, float],
        in_earn: Dict[str, float],
        available: Dict[str, float],
    ) -> List[EarnAction]:
        """Check if strategies need capital redeemed from Earn."""
        actions = []

        for asset, amount_needed in needed.items():
            avail = available.get(asset, 0)
            if avail < amount_needed:
                shortfall = amount_needed - avail
                earn_balance = in_earn.get(asset, 0)
                redeem_amount = min(shortfall, earn_balance)
                if redeem_amount > 0:
                    actions.append(EarnAction(
                        action="REDEEM",
                        asset=asset,
                        amount=redeem_amount,
                        product="flexible_earn",
                        reason=f"Strategy needs {amount_needed:.4f} {asset}, only {avail:.4f} available",
                    ))

        return actions

    def get_status(
        self,
        in_earn: Dict[str, float],
        prices_usd: Dict[str, float] = None,
    ) -> EarnStatus:
        """Get current earn deployment status."""
        prices_usd = prices_usd or {"BTC": 68000, "ETH": 3500, "BNB": 600, "USDC": 1.0}

        total_usd = 0
        daily_yield = 0
        by_asset = {}

        for cfg in self.configs:
            amount = in_earn.get(cfg.asset, 0)
            price = prices_usd.get(cfg.asset, 1.0)
            value_usd = amount * price
            asset_daily_yield = value_usd * cfg.estimated_apy_pct / 100 / 365

            total_usd += value_usd
            daily_yield += asset_daily_yield

            if amount > 0:
                by_asset[cfg.asset] = {
                    "amount": amount,
                    "value_usd": round(value_usd, 2),
                    "apy_pct": cfg.estimated_apy_pct,
                    "daily_yield_usd": round(asset_daily_yield, 4),
                }

        return EarnStatus(
            total_in_earn_usd=total_usd,
            daily_yield_usd=daily_yield,
            annualized_yield_usd=daily_yield * 365,
            by_asset=by_asset,
        )
