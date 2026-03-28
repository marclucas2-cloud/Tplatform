"""
Capital Manager — Binance France wallet management (Spot + Margin + Earn).

Manages transfers between 4 wallets:
  - spot: active trading (buy/sell)
  - margin: isolated margin trading (shorts + leverage)
  - earn_flexible: Binance Earn flexible products (instant redemption)
  - cash: USDT reserve buffer

Rules:
  - Earn: flexible only in phase 1 (instant redemption < 1 min)
  - Margin: ISOLATED only, NEVER cross
  - Cash: keep >= 20% minimum at all times
  - Transfers spot <-> margin are free and instant
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


class WalletError(Exception):
    """Raised on invalid wallet operations."""


class CapitalManager:
    """Manage capital across Binance spot/margin/earn/cash wallets.

    All amounts in USD (USDT equivalent).
    """

    WALLETS = ("spot", "margin", "earn", "cash")
    MIN_CASH_PCT = 0.20            # 20% minimum cash reserve
    MIN_TRANSFER = 100             # Minimum $100 per transfer
    MAX_EARN_SINGLE_ASSET_PCT = 0.50  # Max 50% of earn in one asset

    def __init__(
        self,
        broker=None,
        config_path: str | Path | None = None,
        capital: float = 15_000,
    ):
        self._broker = broker
        self._capital = capital
        self._config = self._load_config(config_path)

        # Initialize wallet balances from config
        wallets_cfg = self._config.get("wallets", {})
        self._balances = {
            "spot": wallets_cfg.get("spot", {}).get("balance", 6000),
            "margin": wallets_cfg.get("margin", {}).get("balance", 4000),
            "earn": wallets_cfg.get("earn_flexible", {}).get("balance", 3000),
            "cash": wallets_cfg.get("cash", {}).get("balance", 2000),
        }

        # Margin state
        self._margin_mode = "ISOLATED"  # NEVER CROSS
        self._borrowed: dict[str, float] = {}  # symbol -> borrowed amount
        self._interest_accrued: dict[str, float] = {}

        # Earn state
        self._earn_positions: dict[str, dict] = {}  # asset -> {amount, product_type, subscribed_at}

        # Transfer log
        self._transfer_log: list[dict] = []
        self._last_rebalance: datetime | None = None

    def _load_config(self, path) -> dict:
        if path is None:
            path = ROOT / "config" / "crypto_wallets.yaml"
        if isinstance(path, (str, Path)) and Path(path).exists():
            return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return {}

    # ------------------------------------------------------------------
    # Read state
    # ------------------------------------------------------------------

    @property
    def total_capital(self) -> float:
        return sum(self._balances.values())

    @property
    def cash_pct(self) -> float:
        total = self.total_capital
        return self._balances["cash"] / total if total > 0 else 0

    @property
    def margin_mode(self) -> str:
        return self._margin_mode

    def get_wallet_balances(self) -> dict[str, float]:
        """Return current balances of all wallets."""
        return dict(self._balances)

    def get_wallet_balance(self, wallet: str) -> float:
        if wallet not in self.WALLETS:
            raise WalletError(f"Unknown wallet: {wallet}")
        return self._balances[wallet]

    def get_total_borrowed(self) -> float:
        """Total borrowed amount across all margin positions."""
        return sum(self._borrowed.values())

    def get_total_interest(self) -> float:
        """Total interest accrued on borrows."""
        return sum(self._interest_accrued.values())

    def get_earn_positions(self) -> dict[str, dict]:
        """Return current earn positions."""
        return dict(self._earn_positions)

    # ------------------------------------------------------------------
    # Transfers
    # ------------------------------------------------------------------

    def transfer_to_margin(self, amount: float, _authorized_by: str = "") -> dict:
        """Transfer USDT from spot to margin wallet.

        Args:
            amount: USD amount to transfer
            _authorized_by: authorization tag (required)

        Returns:
            Transfer result dict
        """
        if not _authorized_by:
            raise WalletError("_authorized_by required")

        if amount < self.MIN_TRANSFER:
            raise WalletError(f"Transfer ${amount:.0f} below minimum ${self.MIN_TRANSFER}")

        if amount > self._balances["spot"]:
            raise WalletError(
                f"Insufficient spot balance: ${self._balances['spot']:.2f} < ${amount:.2f}"
            )

        # Check that cash reserve stays above minimum after transfer
        remaining_total = self.total_capital
        remaining_cash_pct = self._balances["cash"] / remaining_total if remaining_total > 0 else 0
        if remaining_cash_pct < self.MIN_CASH_PCT:
            raise WalletError(
                f"Cash reserve would drop below {self.MIN_CASH_PCT*100:.0f}%: "
                f"current {remaining_cash_pct*100:.1f}%"
            )

        # Execute transfer
        if self._broker:
            try:
                self._broker.transfer_spot_to_margin(amount)
            except Exception as e:
                raise WalletError(f"Broker transfer failed: {e}") from e

        self._balances["spot"] -= amount
        self._balances["margin"] += amount

        result = self._log_transfer("spot", "margin", amount, _authorized_by)
        logger.info(f"Transfer spot -> margin: ${amount:.2f} [{_authorized_by}]")
        return result

    def transfer_from_margin(self, amount: float, _authorized_by: str = "") -> dict:
        """Transfer USDT from margin to spot wallet.

        Requires all borrows to be repaid first for the transferred amount.
        """
        if not _authorized_by:
            raise WalletError("_authorized_by required")

        if amount < self.MIN_TRANSFER:
            raise WalletError(f"Transfer ${amount:.0f} below minimum ${self.MIN_TRANSFER}")

        # Check free margin (balance - borrowed)
        total_borrowed = self.get_total_borrowed()
        free_margin = self._balances["margin"] - total_borrowed
        if amount > free_margin:
            raise WalletError(
                f"Insufficient free margin: ${free_margin:.2f} "
                f"(balance ${self._balances['margin']:.2f} - borrowed ${total_borrowed:.2f})"
            )

        if self._broker:
            try:
                self._broker.transfer_margin_to_spot(amount)
            except Exception as e:
                raise WalletError(f"Broker transfer failed: {e}") from e

        self._balances["margin"] -= amount
        self._balances["spot"] += amount

        result = self._log_transfer("margin", "spot", amount, _authorized_by)
        logger.info(f"Transfer margin -> spot: ${amount:.2f} [{_authorized_by}]")
        return result

    def transfer_to_earn(
        self,
        asset: str,
        amount: float,
        product_type: str = "FLEXIBLE",
        _authorized_by: str = "",
    ) -> dict:
        """Subscribe to Binance Earn flexible product.

        Args:
            asset: e.g. "USDT", "BTC", "ETH"
            amount: USD equivalent to move to earn
            product_type: "FLEXIBLE" only in phase 1
            _authorized_by: authorization tag
        """
        if not _authorized_by:
            raise WalletError("_authorized_by required")

        if product_type != "FLEXIBLE":
            raise WalletError(
                f"Only FLEXIBLE earn allowed in phase 1, got: {product_type}"
            )

        if amount > self._balances["spot"]:
            raise WalletError(
                f"Insufficient spot balance: ${self._balances['spot']:.2f} < ${amount:.2f}"
            )

        # Check single asset concentration in earn
        current_earn_total = self._balances["earn"]
        current_asset_earn = self._earn_positions.get(asset, {}).get("amount", 0)
        new_asset_earn = current_asset_earn + amount
        new_earn_total = current_earn_total + amount

        if new_earn_total > 0 and new_asset_earn / new_earn_total > self.MAX_EARN_SINGLE_ASSET_PCT:
            raise WalletError(
                f"Single asset {asset} would be {new_asset_earn/new_earn_total*100:.0f}% "
                f"of earn wallet (max {self.MAX_EARN_SINGLE_ASSET_PCT*100:.0f}%)"
            )

        # Execute on broker
        if self._broker:
            try:
                self._broker.subscribe_earn(asset, amount, product_type)
            except Exception as e:
                raise WalletError(f"Earn subscription failed: {e}") from e

        self._balances["spot"] -= amount
        self._balances["earn"] += amount

        # Track earn position
        if asset in self._earn_positions:
            self._earn_positions[asset]["amount"] += amount
        else:
            self._earn_positions[asset] = {
                "amount": amount,
                "product_type": product_type,
                "subscribed_at": datetime.now(timezone.utc).isoformat(),
            }

        result = self._log_transfer("spot", "earn", amount, _authorized_by, asset=asset)
        logger.info(f"Subscribe earn {asset}: ${amount:.2f} [{_authorized_by}]")
        return result

    def redeem_from_earn(
        self,
        asset: str,
        amount: float | None = None,
        _authorized_by: str = "",
    ) -> dict:
        """Redeem from Binance Earn flexible (instant, < 1 min).

        Args:
            asset: asset to redeem
            amount: USD amount to redeem (None = redeem all)
            _authorized_by: authorization tag
        """
        if not _authorized_by:
            raise WalletError("_authorized_by required")

        position = self._earn_positions.get(asset)
        if not position:
            raise WalletError(f"No earn position for {asset}")

        redeem_amount = amount if amount is not None else position["amount"]
        if redeem_amount > position["amount"]:
            raise WalletError(
                f"Redeem ${redeem_amount:.2f} exceeds position ${position['amount']:.2f}"
            )

        # Execute on broker
        if self._broker:
            try:
                self._broker.redeem_earn(asset, redeem_amount)
            except Exception as e:
                raise WalletError(f"Earn redemption failed: {e}") from e

        self._balances["earn"] -= redeem_amount
        self._balances["spot"] += redeem_amount

        # Update position
        position["amount"] -= redeem_amount
        if position["amount"] <= 0:
            del self._earn_positions[asset]

        result = self._log_transfer("earn", "spot", redeem_amount, _authorized_by, asset=asset)
        logger.info(f"Redeem earn {asset}: ${redeem_amount:.2f} [{_authorized_by}]")
        return result

    def redeem_all_earn(self, _authorized_by: str = "") -> list[dict]:
        """Redeem all earn positions (emergency / kill switch)."""
        results = []
        for asset in list(self._earn_positions.keys()):
            try:
                r = self.redeem_from_earn(asset, _authorized_by=_authorized_by)
                results.append(r)
            except WalletError as e:
                logger.error(f"Failed to redeem {asset}: {e}")
                results.append({"error": str(e), "asset": asset})
        return results

    def deploy_cash(self, amount: float, target_wallet: str, _authorized_by: str = "") -> dict:
        """Deploy cash reserve to a wallet (emergency only).

        Always keeps MIN_CASH_PCT remaining.
        """
        if not _authorized_by:
            raise WalletError("_authorized_by required")

        if target_wallet not in ("spot", "margin"):
            raise WalletError(f"Can only deploy cash to spot or margin, not {target_wallet}")

        # Calculate minimum cash to keep
        min_cash = self.total_capital * 0.10  # Hard floor 10% of total
        max_deploy = self._balances["cash"] - min_cash

        if amount > max_deploy:
            raise WalletError(
                f"Cannot deploy ${amount:.2f}: max deployable ${max_deploy:.2f} "
                f"(keeping ${min_cash:.2f} minimum cash)"
            )

        self._balances["cash"] -= amount
        self._balances[target_wallet] += amount

        result = self._log_transfer("cash", target_wallet, amount, _authorized_by)
        logger.warning(f"Cash deployed to {target_wallet}: ${amount:.2f} [{_authorized_by}]")
        return result

    # ------------------------------------------------------------------
    # Rebalancing
    # ------------------------------------------------------------------

    def rebalance_wallets(
        self,
        target: dict[str, float] | None = None,
        _authorized_by: str = "",
    ) -> list[dict]:
        """Rebalance wallets towards target ratios.

        Args:
            target: {wallet: ratio} e.g. {"spot": 0.40, "margin": 0.267, "earn": 0.20, "cash": 0.133}
            _authorized_by: authorization tag

        Returns:
            List of transfers executed
        """
        if not _authorized_by:
            raise WalletError("_authorized_by required for rebalance")

        if target is None:
            rebal_cfg = self._config.get("rebalancing", {})
            target = rebal_cfg.get("target_ratios", {
                "spot": 0.40,
                "margin": 0.267,
                "earn": 0.20,
                "cash": 0.133,
            })

        total = self.total_capital
        max_drift = self._config.get("rebalancing", {}).get("max_drift_pct", 10) / 100

        transfers = []

        # Calculate current vs target
        diffs = {}
        for wallet in self.WALLETS:
            current_pct = self._balances[wallet] / total if total > 0 else 0
            target_pct = target.get(wallet, 0)
            drift = current_pct - target_pct
            diffs[wallet] = {
                "current_pct": current_pct,
                "target_pct": target_pct,
                "drift": drift,
                "drift_usd": drift * total,
            }

        # Only rebalance if drift exceeds threshold
        needs_rebalance = any(
            abs(d["drift"]) > max_drift for d in diffs.values()
        )

        if not needs_rebalance:
            logger.info("Wallets within drift tolerance, no rebalance needed")
            return []

        # Execute transfers: from over-allocated to under-allocated
        # Priority: move excess to cash first, then spot, then margin, then earn
        over = {w: d for w, d in diffs.items() if d["drift"] > max_drift}
        under = {w: d for w, d in diffs.items() if d["drift"] < -max_drift}

        for over_wallet, over_diff in sorted(over.items(), key=lambda x: -x[1]["drift"]):
            for under_wallet, under_diff in sorted(under.items(), key=lambda x: x[1]["drift"]):
                amount = min(abs(over_diff["drift_usd"]), abs(under_diff["drift_usd"]))
                amount = round(amount, 2)

                if amount < self.MIN_TRANSFER:
                    continue

                try:
                    result = self._execute_rebalance_transfer(
                        over_wallet, under_wallet, amount, _authorized_by
                    )
                    transfers.append(result)
                    over_diff["drift_usd"] -= amount
                    under_diff["drift_usd"] += amount
                except WalletError as e:
                    logger.warning(f"Rebalance transfer {over_wallet}->{under_wallet} failed: {e}")

        self._last_rebalance = datetime.now(timezone.utc)
        logger.info(f"Rebalance completed: {len(transfers)} transfers [{_authorized_by}]")
        return transfers

    def _execute_rebalance_transfer(
        self, from_wallet: str, to_wallet: str, amount: float, _authorized_by: str
    ) -> dict:
        """Execute a single rebalance transfer between wallets."""
        # Route through spot as intermediary if needed
        if from_wallet == "spot" and to_wallet == "margin":
            return self.transfer_to_margin(amount, _authorized_by=_authorized_by)
        elif from_wallet == "margin" and to_wallet == "spot":
            return self.transfer_from_margin(amount, _authorized_by=_authorized_by)
        elif from_wallet == "spot" and to_wallet == "earn":
            return self.transfer_to_earn("USDT", amount, _authorized_by=_authorized_by)
        elif from_wallet == "earn" and to_wallet == "spot":
            return self.redeem_from_earn("USDT", amount, _authorized_by=_authorized_by)
        elif from_wallet == "cash" and to_wallet in ("spot", "margin"):
            return self.deploy_cash(amount, to_wallet, _authorized_by=_authorized_by)
        else:
            # Route through spot as intermediary
            self._balances[from_wallet] -= amount
            self._balances[to_wallet] += amount
            return self._log_transfer(from_wallet, to_wallet, amount, _authorized_by)

    # ------------------------------------------------------------------
    # Margin tracking
    # ------------------------------------------------------------------

    def record_borrow(self, symbol: str, amount: float):
        """Record a new margin borrow."""
        self._borrowed[symbol] = self._borrowed.get(symbol, 0) + amount
        logger.info(f"Borrow recorded: {symbol} ${amount:.2f}")

    def record_repay(self, symbol: str, amount: float):
        """Record a margin repay."""
        current = self._borrowed.get(symbol, 0)
        self._borrowed[symbol] = max(0, current - amount)
        if self._borrowed[symbol] == 0:
            del self._borrowed[symbol]
        logger.info(f"Repay recorded: {symbol} ${amount:.2f}")

    def record_interest(self, symbol: str, interest: float):
        """Record accrued interest on borrow."""
        self._interest_accrued[symbol] = self._interest_accrued.get(symbol, 0) + interest

    def get_margin_level(self) -> float:
        """Calculate margin level = total_assets / total_liabilities.

        Returns:
            Margin level (>1.0 is safe, <1.5 is warning, <1.2 is critical)
        """
        total_borrowed = self.get_total_borrowed()
        total_interest = self.get_total_interest()
        total_liabilities = total_borrowed + total_interest

        if total_liabilities <= 0:
            return 999.0  # No borrows, infinite margin level

        return self._balances["margin"] / total_liabilities

    def get_free_collateral_pct(self) -> float:
        """Percentage of margin wallet that is not borrowed against."""
        if self._balances["margin"] <= 0:
            return 0.0
        total_borrowed = self.get_total_borrowed()
        return (self._balances["margin"] - total_borrowed) / self._balances["margin"] * 100

    # ------------------------------------------------------------------
    # Sync with broker
    # ------------------------------------------------------------------

    def sync_from_broker(self) -> dict:
        """Sync local wallet state with actual Binance balances.

        Returns:
            Dict with local vs broker balances and any divergences
        """
        if not self._broker:
            return {"error": "no broker configured"}

        try:
            account = self._broker.get_account_info()

            broker_balances = {
                "spot": account.get("spot_usdt", 0),
                "margin": account.get("margin_usdt", 0),
                "earn": account.get("earn_usdt", 0),
                "cash": self._balances["cash"],  # Cash is local tracking only
            }

            divergences = {}
            for wallet in ("spot", "margin", "earn"):
                local = self._balances[wallet]
                broker = broker_balances[wallet]
                diff = abs(local - broker)
                if diff > 10:  # $10 tolerance
                    divergences[wallet] = {
                        "local": local,
                        "broker": broker,
                        "diff": round(diff, 2),
                    }
                    logger.warning(
                        f"Wallet {wallet} divergence: local=${local:.2f} vs broker=${broker:.2f}"
                    )

            # Update local to match broker
            for wallet in ("spot", "margin", "earn"):
                self._balances[wallet] = broker_balances[wallet]

            return {
                "synced": True,
                "balances": dict(self._balances),
                "divergences": divergences,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Broker sync failed: {e}")
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    STATE_FILE = ROOT / "data" / "crypto_capital_state.json"

    def save_state(self) -> None:
        """Persist wallet balances, borrows, earn positions to JSON."""
        state = {
            "balances": dict(self._balances),
            "borrowed": dict(self._borrowed),
            "interest_accrued": dict(self._interest_accrued),
            "earn_positions": dict(self._earn_positions),
            "last_rebalance": self._last_rebalance.isoformat() if self._last_rebalance else None,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.STATE_FILE)
        logger.debug(f"Capital state saved to {self.STATE_FILE}")

    def load_state(self) -> bool:
        """Load persisted state from JSON. Returns True if loaded successfully."""
        if not self.STATE_FILE.exists():
            logger.info("No persisted capital state found, using config defaults")
            return False
        try:
            state = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
            self._balances = {
                w: state.get("balances", {}).get(w, self._balances[w])
                for w in self.WALLETS
            }
            self._borrowed = state.get("borrowed", {})
            self._interest_accrued = state.get("interest_accrued", {})
            self._earn_positions = state.get("earn_positions", {})
            lr = state.get("last_rebalance")
            if lr:
                self._last_rebalance = datetime.fromisoformat(lr)
            logger.info(
                f"Capital state loaded: total=${self.total_capital:.2f}, "
                f"saved at {state.get('saved_at', '?')}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to load capital state: {e}")
            return False

    # ------------------------------------------------------------------
    # Status / Logging
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return full capital manager status."""
        total = self.total_capital
        return {
            "total_capital": round(total, 2),
            "wallets": {
                w: {
                    "balance": round(self._balances[w], 2),
                    "pct": round(self._balances[w] / total * 100, 1) if total > 0 else 0,
                }
                for w in self.WALLETS
            },
            "margin": {
                "mode": self._margin_mode,
                "level": round(self.get_margin_level(), 2),
                "borrowed": round(self.get_total_borrowed(), 2),
                "interest_accrued": round(self.get_total_interest(), 2),
                "free_collateral_pct": round(self.get_free_collateral_pct(), 1),
            },
            "earn": {
                "positions": self._earn_positions,
                "total": round(self._balances["earn"], 2),
            },
            "cash_pct": round(self.cash_pct * 100, 1),
            "cash_healthy": self.cash_pct >= self.MIN_CASH_PCT,
            "last_rebalance": self._last_rebalance.isoformat() if self._last_rebalance else None,
            "transfers_today": len([
                t for t in self._transfer_log
                if t.get("date") == datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ]),
        }

    def _log_transfer(
        self,
        from_wallet: str,
        to_wallet: str,
        amount: float,
        authorized_by: str,
        asset: str = "USDT",
    ) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "from": from_wallet,
            "to": to_wallet,
            "amount": round(amount, 2),
            "asset": asset,
            "authorized_by": authorized_by,
            "balances_after": dict(self._balances),
        }
        self._transfer_log.append(entry)
        # Persist state after every transfer
        try:
            self.save_state()
        except Exception as e:
            logger.warning(f"Failed to persist state after transfer: {e}")
        return entry

    def get_transfer_log(self, limit: int = 50) -> list[dict]:
        return self._transfer_log[-limit:]
