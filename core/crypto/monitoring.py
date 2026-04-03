"""
MON-002 — Crypto monitoring V2 for Binance France (Margin + Spot + Earn).

CryptoAlerter: Telegram alerts with margin-specific alerts
  - margin_level_warning, borrow_rate_spike, earn_apy_change
CryptoReconciliation V2: margin positions, interest, earn, isolated mode
Dashboard router: /api/crypto/* endpoints (overview, positions, margin, earn, risk, strategies, alerts, health)
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


# ------------------------------------------------------------------
# Telegram Alerting V2
# ------------------------------------------------------------------

class CryptoAlerter:
    """Telegram alerting for crypto portfolio — V2 with margin alerts."""

    LEVELS = {
        "INFO": "INFO",
        "WARNING": "WARNING",
        "CRITICAL": "CRITICAL",
    }

    def __init__(self, telegram_bot=None):
        self._telegram = telegram_bot
        self._alerts: list[dict] = []
        self._cooldowns: dict[str, float] = {}
        self._cooldown_seconds = 300  # 5 min between same alerts
        self._borrow_rate_history: dict[str, list[dict]] = {}  # symbol -> [{rate, ts}]

    def alert(self, level: str, message: str, category: str = "general"):
        """Send an alert via Telegram and log it."""
        now = time.time()
        cooldown_key = f"{level}:{category}"

        # Cooldown check (CRITICAL always goes through)
        last_sent = self._cooldowns.get(cooldown_key, 0)
        if now - last_sent < self._cooldown_seconds and level != "CRITICAL":
            return

        prefix = self.LEVELS.get(level, "")
        full_msg = f"[CRYPTO {prefix}] {message}"

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "category": category,
            "message": message,
        }
        self._alerts.append(entry)
        self._cooldowns[cooldown_key] = now

        if level == "CRITICAL":
            logger.critical(full_msg)
        elif level == "WARNING":
            logger.warning(full_msg)
        else:
            logger.info(full_msg)

        # Send to Telegram if available
        if self._telegram:
            try:
                self._telegram.send_message(full_msg)
            except Exception as e:
                logger.error(f"Telegram send failed: {e}")

    def get_recent_alerts(self, limit: int = 50) -> list[dict]:
        return self._alerts[-limit:]

    # --- Trade alerts ---

    def trade_executed(self, symbol: str, direction: str, qty: float, price: float, strategy: str):
        self.alert(
            "INFO",
            f"Trade: {direction} {qty:.4f} {symbol} @ ${price:.2f} [{strategy}]",
            category="trade",
        )

    def rebalance(self, strategy: str, changes: list[str]):
        self.alert(
            "INFO",
            f"Rebalance [{strategy}]: {', '.join(changes)}",
            category="rebalance",
        )

    # --- Margin-specific alerts ---

    def margin_level_warning(self, margin_level: float, threshold: float = 1.8):
        """Alert when margin level drops below warning threshold."""
        if margin_level < 1.2:
            self.alert(
                "CRITICAL",
                f"MARGIN LEVEL CRITICAL: {margin_level:.2f} (liquidation imminent, threshold 1.2)",
                category="margin_level",
            )
        elif margin_level < threshold:
            self.alert(
                "WARNING",
                f"Margin level low: {margin_level:.2f} (warning threshold {threshold:.1f})",
                category="margin_level",
            )

    def borrow_rate_spike(self, symbol: str, current_rate: float, previous_rate: float, multiplier: float):
        """Alert when borrow rate spikes significantly."""
        if previous_rate > 0 and current_rate / previous_rate >= multiplier:
            self.alert(
                "WARNING",
                f"Borrow rate spike {symbol}: {previous_rate:.4f}% -> {current_rate:.4f}% "
                f"({current_rate/previous_rate:.1f}x in window)",
                category="borrow_rate",
            )

    def borrow_cost_warning(self, daily_cost: float, monthly_budget: float):
        """Alert when daily borrow cost is high relative to monthly budget."""
        projected_monthly = daily_cost * 30
        if projected_monthly > monthly_budget * 0.8:
            level = "CRITICAL" if projected_monthly > monthly_budget else "WARNING"
            self.alert(
                level,
                f"Borrow cost high: ${daily_cost:.2f}/day "
                f"(projected ${projected_monthly:.0f}/month vs budget ${monthly_budget:.0f})",
                category="borrow_cost",
            )

    def earn_apy_change(self, asset: str, old_apy: float, new_apy: float):
        """Alert on significant APY change for earn positions."""
        if old_apy <= 0:
            return

        change_pct = (new_apy - old_apy) / old_apy * 100
        if abs(change_pct) > 20:  # > 20% relative change
            direction = "increased" if change_pct > 0 else "decreased"
            self.alert(
                "INFO",
                f"Earn APY {direction} for {asset}: {old_apy:.2f}% -> {new_apy:.2f}% "
                f"({change_pct:+.0f}%)",
                category="earn_apy",
            )

    def margin_borrow_executed(self, symbol: str, amount: float, rate: float):
        """Alert on new margin borrow."""
        self.alert(
            "INFO",
            f"Margin borrow: {symbol} ${amount:.2f} @ {rate:.4f}%/day",
            category="margin_borrow",
        )

    def margin_repay_executed(self, symbol: str, amount: float, interest_paid: float):
        """Alert on margin repay."""
        self.alert(
            "INFO",
            f"Margin repay: {symbol} ${amount:.2f} (interest paid: ${interest_paid:.2f})",
            category="margin_repay",
        )

    # --- Existing alerts ---

    def liquidation_warning(self, symbol: str, distance_pct: float):
        self.alert(
            "WARNING" if distance_pct > 15 else "CRITICAL",
            f"Liquidation risk {symbol}: {distance_pct:.1f}% from liq price",
            category="liquidation",
        )

    def api_down(self, minutes: float):
        level = "WARNING" if minutes < 10 else "CRITICAL"
        self.alert(level, f"Binance API down for {minutes:.0f} minutes", category="api")

    def kill_switch_triggered(self, reason: str):
        self.alert(
            "CRITICAL",
            f"KILL SWITCH TRIGGERED: {reason}",
            category="kill_switch",
        )

    def reconciliation_divergence(self, details: str):
        self.alert("CRITICAL", f"Reconciliation divergence: {details}", category="recon")

    def drawdown_warning(self, dd_pct: float):
        self.alert(
            "WARNING" if abs(dd_pct) < 10 else "CRITICAL",
            f"Drawdown: {dd_pct:.1f}%",
            category="drawdown",
        )

    def correlation_warning(self, correlated_pct: float):
        self.alert(
            "WARNING",
            f"{correlated_pct:.0f}% of positions highly correlated with BTC",
            category="correlation",
        )

    def wallet_transfer(self, from_wallet: str, to_wallet: str, amount: float):
        self.alert(
            "INFO",
            f"Wallet transfer: {from_wallet} -> {to_wallet} ${amount:.2f}",
            category="wallet",
        )

    # --- Borrow rate tracking ---

    def track_borrow_rate(self, symbol: str, rate: float):
        """Track borrow rate for spike detection."""
        now = time.time()
        if symbol not in self._borrow_rate_history:
            self._borrow_rate_history[symbol] = []

        self._borrow_rate_history[symbol].append({"rate": rate, "ts": now})

        # Keep only last 2 hours of history
        cutoff = now - 7200
        self._borrow_rate_history[symbol] = [
            r for r in self._borrow_rate_history[symbol] if r["ts"] > cutoff
        ]

        # Check for spike (3x in 1 hour)
        hour_ago = now - 3600
        old_rates = [r["rate"] for r in self._borrow_rate_history[symbol] if r["ts"] < hour_ago]
        if old_rates:
            avg_old = sum(old_rates) / len(old_rates)
            if avg_old > 0:
                self.borrow_rate_spike(symbol, rate, avg_old, 3.0)


# ------------------------------------------------------------------
# Reconciliation V2
# ------------------------------------------------------------------

class CryptoReconciliation:
    """Reconcile local state with Binance API every 5 minutes.

    V2 checks (Margin + Spot + Earn):
      1. Position count match (local vs exchange) — spot + margin
      2. Balance match (spot + margin + earn wallets)
      3. Every margin position has a stop loss
      4. Margin mode is ISOLATED for all pairs
      5. Borrowed amounts match (local vs exchange)
      6. Interest accrued tracking
      7. Earn positions match (subscribed products)
      8. Margin level within safe range
    """

    def __init__(
        self,
        broker=None,
        capital_manager=None,
        alerter: CryptoAlerter | None = None,
    ):
        self._broker = broker
        self._capital_manager = capital_manager
        self._alerter = alerter
        self._last_check: datetime | None = None
        self._divergences: list[dict] = []

    def reconcile(
        self,
        local_positions: list[dict],
        local_balance: float,
    ) -> dict:
        """Run full reconciliation V2.

        Args:
            local_positions: positions tracked locally (spot + margin)
            local_balance: locally tracked total balance

        Returns:
            dict with {ok, checks, divergences}
        """
        if self._broker is None:
            return {"ok": False, "error": "no broker"}

        checks = {}
        divergences = []

        # 1. Get exchange state
        try:
            exchange_positions = self._broker.get_positions()
            account = self._broker.get_account_info()
        except Exception as e:
            if self._alerter:
                self._alerter.alert("CRITICAL", f"Reconciliation failed: {e}", "recon")
            return {"ok": False, "error": str(e)}

        # 2. Position count — spot + margin
        local_spot = {p.get("symbol") for p in local_positions if p.get("wallet") == "spot"}
        local_margin = {p.get("symbol") for p in local_positions if p.get("wallet") == "margin"}
        local_all = local_spot | local_margin

        exchange_spot = {
            p.get("symbol") for p in exchange_positions
            if p.get("asset_type") in ("CRYPTO_SPOT", "SPOT")
        }
        exchange_margin = {
            p.get("symbol") for p in exchange_positions
            if p.get("asset_type") in ("CRYPTO_MARGIN", "MARGIN")
        }
        exchange_all = exchange_spot | exchange_margin

        phantom = local_all - exchange_all
        orphan = exchange_all - local_all

        checks["positions"] = {
            "local_spot": len(local_spot),
            "local_margin": len(local_margin),
            "exchange_spot": len(exchange_spot),
            "exchange_margin": len(exchange_margin),
            "phantom": list(phantom),
            "orphan": list(orphan),
        }

        if phantom:
            divergences.append({
                "type": "phantom",
                "symbols": list(phantom),
                "severity": "WARNING",
            })
        if orphan:
            divergences.append({
                "type": "orphan",
                "symbols": list(orphan),
                "severity": "CRITICAL",
            })

        # 3. Balance check — spot + margin + earn
        exchange_spot_balance = account.get("spot_usdt", 0)
        exchange_margin_balance = account.get("margin_usdt", 0)
        exchange_earn_balance = account.get("earn_usdt", 0)
        exchange_total = exchange_spot_balance + exchange_margin_balance + exchange_earn_balance

        balance_diff = abs(exchange_total - local_balance)
        checks["balance"] = {
            "local": local_balance,
            "exchange_total": round(exchange_total, 2),
            "exchange_spot": round(exchange_spot_balance, 2),
            "exchange_margin": round(exchange_margin_balance, 2),
            "exchange_earn": round(exchange_earn_balance, 2),
            "diff": round(balance_diff, 2),
        }
        if balance_diff > 10:
            divergences.append({
                "type": "balance_mismatch",
                "diff": balance_diff,
                "severity": "WARNING" if balance_diff < 100 else "CRITICAL",
            })

        # 4. Stop loss check (margin positions must have stops)
        open_orders = self._broker.get_orders(status="open")
        symbols_with_sl = {
            o["symbol"] for o in open_orders
            if o.get("type") in ("STOP_LOSS", "STOP_LOSS_LIMIT", "STOP_MARKET", "STOP")
        }
        margin_without_sl = exchange_margin - symbols_with_sl
        checks["stop_losses"] = {
            "margin_positions": len(exchange_margin),
            "with_sl": len(symbols_with_sl & exchange_margin),
            "without_sl": list(margin_without_sl),
        }
        if margin_without_sl:
            divergences.append({
                "type": "missing_stop_loss",
                "symbols": list(margin_without_sl),
                "severity": "CRITICAL",
            })

        # 5. Margin mode check — must be ISOLATED
        margin_violations = []
        for p in exchange_positions:
            if p.get("asset_type") in ("CRYPTO_MARGIN", "MARGIN"):
                mode = p.get("margin_type", "").upper()
                if mode and mode != "ISOLATED":
                    margin_violations.append({
                        "symbol": p["symbol"],
                        "mode": mode,
                    })
        checks["margin_mode"] = {
            "violations": margin_violations,
            "expected": "ISOLATED",
        }
        if margin_violations:
            divergences.append({
                "type": "wrong_margin_mode",
                "details": margin_violations,
                "severity": "CRITICAL",
            })

        # 6. Borrowed amounts check
        if self._capital_manager:
            local_borrowed = self._capital_manager.get_total_borrowed()
            exchange_borrowed = account.get("total_borrowed", 0)
            borrow_diff = abs(local_borrowed - exchange_borrowed)
            checks["borrowed"] = {
                "local": round(local_borrowed, 2),
                "exchange": round(exchange_borrowed, 2),
                "diff": round(borrow_diff, 2),
            }
            if borrow_diff > 5:
                divergences.append({
                    "type": "borrow_mismatch",
                    "diff": borrow_diff,
                    "severity": "WARNING",
                })

        # 7. Interest accrued
        exchange_interest = account.get("total_interest_accrued", 0)
        checks["interest"] = {
            "exchange": round(exchange_interest, 2),
        }
        if self._capital_manager:
            local_interest = self._capital_manager.get_total_interest()
            interest_diff = abs(local_interest - exchange_interest)
            checks["interest"]["local"] = round(local_interest, 2)
            checks["interest"]["diff"] = round(interest_diff, 2)

        # 8. Earn positions check
        if self._capital_manager:
            local_earn = self._capital_manager.get_earn_positions()
            exchange_earn_positions = account.get("earn_positions", {})
            earn_mismatches = []

            for asset, local_pos in local_earn.items():
                exchange_pos = exchange_earn_positions.get(asset, {})
                if not exchange_pos:
                    earn_mismatches.append({"asset": asset, "type": "phantom_earn"})
                else:
                    amount_diff = abs(local_pos.get("amount", 0) - exchange_pos.get("amount", 0))
                    if amount_diff > 1:
                        earn_mismatches.append({
                            "asset": asset,
                            "type": "earn_amount_mismatch",
                            "diff": amount_diff,
                        })

            for asset in exchange_earn_positions:
                if asset not in local_earn:
                    earn_mismatches.append({"asset": asset, "type": "orphan_earn"})

            checks["earn"] = {
                "local_count": len(local_earn),
                "exchange_count": len(exchange_earn_positions),
                "mismatches": earn_mismatches,
            }
            if earn_mismatches:
                divergences.append({
                    "type": "earn_mismatch",
                    "details": earn_mismatches,
                    "severity": "WARNING",
                })

        # 9. Margin level check
        margin_level = account.get("margin_level", 999)
        checks["margin_level"] = {
            "level": round(margin_level, 2),
            "healthy": margin_level > 1.5,
            "warning": margin_level < 1.8,
            "critical": margin_level < 1.2,
        }
        if margin_level < 1.5:
            divergences.append({
                "type": "low_margin_level",
                "level": margin_level,
                "severity": "CRITICAL" if margin_level < 1.2 else "WARNING",
            })
            if self._alerter:
                self._alerter.margin_level_warning(margin_level)

        # Alert on divergences
        if divergences and self._alerter:
            for d in divergences:
                if d["severity"] == "CRITICAL":
                    self._alerter.reconciliation_divergence(
                        f"{d['type']}: {d.get('symbols', d.get('details', d.get('diff', '')))}"
                    )

        self._divergences.extend(divergences)
        self._last_check = datetime.now(UTC)

        critical_count = len([d for d in divergences if d["severity"] == "CRITICAL"])
        ok = critical_count == 0

        return {
            "ok": ok,
            "checks": checks,
            "divergences": divergences,
            "critical_count": critical_count,
            "timestamp": self._last_check.isoformat(),
        }

    def get_history(self, limit: int = 20) -> list[dict]:
        return self._divergences[-limit:]


# ------------------------------------------------------------------
# Dashboard Endpoints V2 (FastAPI router)
# ------------------------------------------------------------------

def create_crypto_router():
    """Create FastAPI router for crypto dashboard — V2 Margin + Spot + Earn."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/crypto", tags=["crypto"])

    # State populated at startup
    _state = {
        "broker": None,
        "risk_manager": None,
        "allocator": None,
        "capital_manager": None,
        "alerter": CryptoAlerter(),
        "reconciliation": None,
    }

    def _get(key):
        return _state.get(key)

    @router.get("/overview")
    def crypto_overview():
        """Full overview: wallets, P&L, positions, exposure, regime."""
        broker = _get("broker")
        if not broker:
            return {"error": "broker not initialized"}

        try:
            account = broker.get_account_info()
            positions = broker.get_positions()
            allocator = _get("allocator")
            capital_mgr = _get("capital_manager")

            spot_positions = [
                p for p in positions
                if p.get("asset_type") in ("CRYPTO_SPOT", "SPOT")
            ]
            margin_positions = [
                p for p in positions
                if p.get("asset_type") in ("CRYPTO_MARGIN", "MARGIN")
            ]

            total_unrealized = sum(p.get("unrealized_pl", 0) for p in positions)

            result = {
                "equity": account.get("equity", 0),
                "spot_balance": account.get("spot_usdt", 0),
                "margin_balance": account.get("margin_usdt", 0),
                "earn_balance": account.get("earn_usdt", 0),
                "unrealized_pnl": round(total_unrealized, 2),
                "spot_positions": len(spot_positions),
                "margin_positions": len(margin_positions),
                "regime": allocator.current_regime if allocator else "UNKNOWN",
                "kill_switch": (
                    _get("risk_manager").kill_switch.status()
                    if _get("risk_manager") else {}
                ),
                "timestamp": datetime.now(UTC).isoformat(),
            }

            if capital_mgr:
                result["wallets"] = capital_mgr.get_wallet_balances()
                result["margin_level"] = round(capital_mgr.get_margin_level(), 2)
                result["cash_pct"] = round(capital_mgr.cash_pct * 100, 1)

            return result
        except Exception as e:
            return {"error": str(e)}

    @router.get("/positions")
    def crypto_positions():
        """Detailed open positions with wallet and bracket status."""
        broker = _get("broker")
        if not broker:
            return {"error": "broker not initialized", "positions": []}

        try:
            positions = broker.get_positions()
            spot = [p for p in positions if p.get("asset_type") in ("CRYPTO_SPOT", "SPOT")]
            margin = [p for p in positions if p.get("asset_type") in ("CRYPTO_MARGIN", "MARGIN")]

            return {
                "spot": spot,
                "margin": margin,
                "total_count": len(positions),
                "spot_count": len(spot),
                "margin_count": len(margin),
            }
        except Exception as e:
            return {"error": str(e), "positions": []}

    @router.get("/margin")
    def crypto_margin():
        """Margin account details: borrows, interest, margin level, collateral."""
        capital_mgr = _get("capital_manager")
        broker = _get("broker")

        result = {
            "margin_mode": "ISOLATED",
        }

        if capital_mgr:
            result.update({
                "margin_level": round(capital_mgr.get_margin_level(), 2),
                "total_borrowed": round(capital_mgr.get_total_borrowed(), 2),
                "total_interest": round(capital_mgr.get_total_interest(), 2),
                "free_collateral_pct": round(capital_mgr.get_free_collateral_pct(), 1),
                "wallet_balance": round(capital_mgr.get_wallet_balance("margin"), 2),
            })

        if broker:
            try:
                account = broker.get_account_info()
                result["exchange_margin_level"] = account.get("margin_level", 0)
                result["exchange_borrowed"] = account.get("total_borrowed", 0)
                result["exchange_interest"] = account.get("total_interest_accrued", 0)
            except Exception as e:
                result["exchange_error"] = str(e)

        return result

    @router.get("/earn")
    def crypto_earn():
        """Earn positions: products, APY, balances."""
        capital_mgr = _get("capital_manager")
        broker = _get("broker")

        result = {"positions": {}, "total": 0}

        if capital_mgr:
            result["positions"] = capital_mgr.get_earn_positions()
            result["total"] = round(capital_mgr.get_wallet_balance("earn"), 2)

        if broker:
            try:
                account = broker.get_account_info()
                result["exchange_earn"] = account.get("earn_positions", {})
                result["exchange_total"] = account.get("earn_usdt", 0)
            except Exception as e:
                result["exchange_error"] = str(e)

        return result

    @router.get("/risk")
    def crypto_risk():
        """Risk dashboard: margin level, kill switch, drawdown, borrow costs."""
        risk_mgr = _get("risk_manager")
        capital_mgr = _get("capital_manager")

        result = {}

        if risk_mgr:
            result["kill_switch"] = risk_mgr.kill_switch.status()
            result["capital"] = risk_mgr.capital

        if capital_mgr:
            result["margin"] = {
                "level": round(capital_mgr.get_margin_level(), 2),
                "borrowed": round(capital_mgr.get_total_borrowed(), 2),
                "interest": round(capital_mgr.get_total_interest(), 2),
                "free_collateral_pct": round(capital_mgr.get_free_collateral_pct(), 1),
            }
            result["wallets"] = capital_mgr.get_wallet_balances()
            result["cash_healthy"] = capital_mgr.cash_pct >= capital_mgr.MIN_CASH_PCT

        if not result:
            return {"error": "risk manager and capital manager not initialized"}

        return result

    @router.get("/strategies")
    def crypto_strategies():
        """Performance by strategy with wallet breakdown."""
        allocator = _get("allocator")
        if not allocator:
            return {"error": "allocator not initialized"}
        return allocator.status()

    @router.get("/alerts")
    def crypto_alerts(limit: int = 50):
        """Recent alerts including margin and earn alerts."""
        alerter = _get("alerter")
        return {"alerts": alerter.get_recent_alerts(limit) if alerter else []}

    @router.get("/health")
    def crypto_health():
        """System health check — broker, margin, earn, kill switch."""
        broker = _get("broker")
        capital_mgr = _get("capital_manager")

        status = {
            "broker_connected": False,
            "kill_switch_active": False,
            "margin_healthy": False,
            "earn_accessible": False,
            "cash_sufficient": False,
            "last_reconciliation": None,
        }

        if broker:
            try:
                broker.get_account_info()
                status["broker_connected"] = True
            except Exception:
                pass

        risk_mgr = _get("risk_manager")
        if risk_mgr:
            status["kill_switch_active"] = risk_mgr.kill_switch.is_killed

        if capital_mgr:
            status["margin_healthy"] = capital_mgr.get_margin_level() > 1.5
            status["cash_sufficient"] = capital_mgr.cash_pct >= capital_mgr.MIN_CASH_PCT
            status["earn_accessible"] = True  # Flexible earn is always accessible

        recon = _get("reconciliation")
        if recon and recon._last_check:
            status["last_reconciliation"] = recon._last_check.isoformat()

        # Overall health
        status["healthy"] = (
            status["broker_connected"]
            and not status["kill_switch_active"]
            and status["margin_healthy"]
            and status["cash_sufficient"]
        )

        return status

    return router, _state
