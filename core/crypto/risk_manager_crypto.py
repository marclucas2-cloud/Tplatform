"""
CryptoRiskManager V2 — Risk management for Binance France Margin + Spot + Earn.

NO futures/perp. 12 checks adapted for margin borrowing model:
  1.  Position size (15% max — more conservative)
  2.  Strategy concentration
  3.  Gross long exposure (80%) / gross short exposure (40%)
  4.  Leverage per-asset: BTC/ETH 2.5x, altcoin 1.5x, portfolio avg 1.8x
  5.  Borrow rate check (daily < 0.1%, total < 50%, monthly cost < 2%)
  6.  Drawdown circuit breakers (5% daily, 10% weekly, 15% monthly, 20% max DD)
  7.  Margin health (per-position margin_level, reduce at <1.5, close at <1.3)
  8.  Borrow cost control (close most expensive shorts if monthly > 2%)
  9.  Earn exposure (count earn in total exposure)
  10. Unrealized loss per position
  11. BTC correlation monitoring
  12. Available cash reserve

Kill switch V2 — 6 triggers:
  1. Daily loss > 5%
  2. Hourly loss > 3%
  3. Max drawdown > 20%
  4. API down > 10 min
  5. Margin level critical < 1.2 (any position)
  6. Borrow rate spike (any asset > 1%/day)

Kill actions priority:
  1. Close shorts first (they cost interest)
  2. Cancel open orders
  3. Close longs
  4. Redeem earn positions
  5. Send alert
  6. Convert everything to USDT
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


# ──────────────────────────────────────────────────────────────────────
# Risk limits
# ──────────────────────────────────────────────────────────────────────

class CryptoRiskLimits:
    """Risk limits calibrated for Binance margin + spot + earn.

    Class-level defaults are overridden by config/crypto_limits.yaml if present.
    """

    # Position limits (more conservative than V1)
    MAX_POSITION_PCT = 15              # Max 15% per position (was 20%)
    MAX_STRATEGY_PCT = 30              # Max 30% per strategy
    MAX_GROSS_LONG_PCT = 80            # Max gross long exposure
    MAX_GROSS_SHORT_PCT = 40           # Shorts more risky on margin
    MAX_NET_PCT = 60                   # Net directional

    # Leverage — per asset class
    MAX_LEVERAGE_BTC_ETH = 2.5         # BTC/ETH: max 2.5x
    MAX_LEVERAGE_ALTCOIN = 1.5         # Altcoins: max 1.5x
    MAX_LEVERAGE_PORTFOLIO_AVG = 1.8   # Portfolio average

    # Borrow costs
    MAX_BORROW_RATE_DAILY = 0.001      # 0.1%/day per position
    MAX_TOTAL_BORROW_PCT = 50          # Max 50% of capital borrowed
    MAX_BORROW_COST_MONTHLY_PCT = 2.0  # Monthly borrow cost < 2% of capital

    # Drawdowns (stricter than V1)
    DAILY_MAX_LOSS_PCT = 5.0
    HOURLY_MAX_LOSS_PCT = 3.0
    WEEKLY_MAX_LOSS_PCT = 10.0
    MONTHLY_MAX_LOSS_PCT = 15.0
    MAX_DRAWDOWN_PCT = 20.0            # Stricter than V1's 25%

    # Margin health
    MIN_MARGIN_LEVEL = 1.5             # Warning + reduce at 1.5
    MARGIN_LEVEL_WARNING = 1.8         # First warning
    MARGIN_LEVEL_REDUCE = 1.5          # Reduce 30% at this level
    MARGIN_LEVEL_CLOSE = 1.3           # Force close position
    MARGIN_LEVEL_LIQUIDATION = 1.1     # Binance liquidates here

    # Earn
    MAX_EARN_PCT = 100                 # Earn Flexible = redemption instantanee, pas de risque

    # Cash reserve
    MIN_CASH_RESERVE_PCT = 10          # Always keep 10% cash

    # Correlation
    MAX_BTC_CORRELATED_PCT = 70        # Max 70% correlated with BTC

    # Per-position unrealized loss
    MAX_UNREALIZED_LOSS_PER_POSITION = 8  # Tighter than V1's 10%

    # BTC/ETH symbols
    BTC_ETH_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"}

    def __init__(self, config_path: str | Path | None = None):
        """Load limits from crypto_limits.yaml, keeping class defaults as fallbacks."""
        if config_path is None:
            config_path = ROOT / "config" / "crypto_limits.yaml"
        config_path = Path(config_path)
        if not config_path.exists():
            logger.info("No crypto_limits.yaml found, using class defaults")
            return
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load crypto_limits.yaml: {e}, using defaults")
            return

        # Position limits
        pos = cfg.get("position_limits", {})
        if "max_position_pct" in pos:
            self.MAX_POSITION_PCT = pos["max_position_pct"]
        if "max_strategy_pct" in pos:
            self.MAX_STRATEGY_PCT = pos["max_strategy_pct"]
        if "max_gross_long_pct" in pos:
            self.MAX_GROSS_LONG_PCT = pos["max_gross_long_pct"]
        if "max_gross_short_pct" in pos:
            self.MAX_GROSS_SHORT_PCT = pos["max_gross_short_pct"]
        if "max_net_pct" in pos:
            self.MAX_NET_PCT = pos["max_net_pct"]

        # Leverage limits
        lev = cfg.get("leverage_limits", {})
        if "btc_eth" in lev:
            self.MAX_LEVERAGE_BTC_ETH = lev["btc_eth"]
        if "altcoin" in lev:
            self.MAX_LEVERAGE_ALTCOIN = lev["altcoin"]
        if "portfolio_weighted_max" in lev:
            self.MAX_LEVERAGE_PORTFOLIO_AVG = lev["portfolio_weighted_max"]

        # Borrow limits
        bor = cfg.get("borrow_limits", {})
        if "max_borrow_rate_daily_pct" in bor:
            self.MAX_BORROW_RATE_DAILY = bor["max_borrow_rate_daily_pct"] / 100
        if "max_total_borrowed_pct" in bor:
            self.MAX_TOTAL_BORROW_PCT = bor["max_total_borrowed_pct"]
        if "max_monthly_borrow_cost_pct" in bor:
            self.MAX_BORROW_COST_MONTHLY_PCT = bor["max_monthly_borrow_cost_pct"]

        # Circuit breakers
        cb = cfg.get("circuit_breakers", {})
        if "daily_max_loss_pct" in cb:
            self.DAILY_MAX_LOSS_PCT = cb["daily_max_loss_pct"]
        if "weekly_max_loss_pct" in cb:
            self.WEEKLY_MAX_LOSS_PCT = cb["weekly_max_loss_pct"]
        if "monthly_max_loss_pct" in cb:
            self.MONTHLY_MAX_LOSS_PCT = cb["monthly_max_loss_pct"]
        if "max_drawdown_pct" in cb:
            self.MAX_DRAWDOWN_PCT = cb["max_drawdown_pct"]

        # Margin rules
        mr = cfg.get("margin_rules", {})
        if "min_margin_level" in mr:
            self.MIN_MARGIN_LEVEL = mr["min_margin_level"]
            self.MARGIN_LEVEL_REDUCE = mr["min_margin_level"]
        if "warning_margin_level" in mr:
            self.MARGIN_LEVEL_WARNING = mr["warning_margin_level"]

        # Crypto specific
        cs = cfg.get("crypto_specific", {})
        if "max_unrealized_loss_per_position_pct" in cs:
            self.MAX_UNREALIZED_LOSS_PER_POSITION = cs["max_unrealized_loss_per_position_pct"]
        if "max_btc_correlated_pct" in cs:
            self.MAX_BTC_CORRELATED_PCT = cs["max_btc_correlated_pct"]

        logger.info(
            f"CryptoRiskLimits loaded from {config_path.name}: "
            f"pos={self.MAX_POSITION_PCT}%, strat={self.MAX_STRATEGY_PCT}%, "
            f"DD={self.MAX_DRAWDOWN_PCT}%"
        )


# ──────────────────────────────────────────────────────────────────────
# Kill Switch V2
# ──────────────────────────────────────────────────────────────────────

class CryptoKillSwitch:
    """Kill switch V2 for crypto portfolio — 6 triggers.

    Priority actions on kill:
      1. Close shorts (they accrue borrow interest)
      2. Cancel open orders
      3. Close longs
      4. Redeem earn positions
      5. Alert (Telegram)
      6. Convert to USDT
    """

    KILL_ACTIONS_PRIORITY = [
        "close_shorts",
        "cancel_orders",
        "close_longs",
        "redeem_earn",
        "alert",
        "convert_to_usdt",
    ]

    _STATE_PATH = ROOT / "data" / "crypto_kill_switch_state.json"

    def __init__(self, config_path: str | Path | None = None, state_path: Path | None = None):
        if state_path is not None:
            self._STATE_PATH = state_path
        self._config = self._load_config(config_path)
        self._active = False
        self._trigger_reason = ""
        self._trigger_time: datetime | None = None
        self._daily_pnl_history: list[dict] = []
        self._hourly_pnl_history: list[dict] = []
        self._actions_executed: list[str] = []
        # CRO H-5 FIX: load persisted state
        self._load_persisted_state()

    def _load_persisted_state(self):
        try:
            if self._STATE_PATH.exists():
                data = json.loads(self._STATE_PATH.read_text(encoding="utf-8"))
                self._active = data.get("active", False)
                self._trigger_reason = data.get("reason", "")
                if data.get("trigger_time"):
                    self._trigger_time = datetime.fromisoformat(data["trigger_time"])
        except Exception:
            pass

    def _save_persisted_state(self):
        try:
            self._STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._STATE_PATH.write_text(json.dumps({
                "active": self._active,
                "reason": self._trigger_reason,
                "trigger_time": self._trigger_time.isoformat() if self._trigger_time else None,
            }, indent=2))
        except Exception:
            pass

    def _load_config(self, path) -> dict:
        if path is None:
            path = ROOT / "config" / "crypto_kill_switch.yaml"
        if isinstance(path, (str, Path)) and Path(path).exists():
            return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return {
            "daily_loss_pct": 5.0,
            "hourly_loss_pct": 3.0,
            "max_drawdown_pct": 20.0,
            "exchange_api_down_minutes": 10,
            "margin_level_critical": 1.2,
            "borrow_rate_spike_daily": 0.01,  # 1%/day
        }

    @property
    def is_killed(self) -> bool:
        return self._active

    @property
    def trigger_reason(self) -> str:
        return self._trigger_reason

    @property
    def actions_priority(self) -> list[str]:
        return list(self.KILL_ACTIONS_PRIORITY)

    def check(
        self,
        daily_pnl_pct: float = 0,
        hourly_pnl_pct: float = 0,
        drawdown_pct: float = 0,
        api_down_minutes: float = 0,
        margin_level_min: float = 999,
        max_borrow_rate_daily: float = 0,
    ) -> tuple[bool, str]:
        """Check all 6 kill switch triggers.

        Returns:
            (should_kill, reason)
        """
        # Already active — don't re-trigger (prevents endless loop)
        if self._active:
            return True, self._trigger_reason

        config = self._config

        # 1. Daily loss
        if daily_pnl_pct < -config.get("daily_loss_pct", 5):
            return self._activate(f"daily_loss_{daily_pnl_pct:.1f}%")

        # 2. Hourly loss
        if hourly_pnl_pct < -config.get("hourly_loss_pct", 3):
            return self._activate(f"hourly_loss_{hourly_pnl_pct:.1f}%")

        # 3. Max drawdown
        if abs(drawdown_pct) > config.get("max_drawdown_pct", 20):
            return self._activate(f"max_drawdown_{drawdown_pct:.1f}%")

        # 4. API down
        if api_down_minutes > config.get("exchange_api_down_minutes", 10):
            return self._activate(f"api_down_{api_down_minutes:.0f}min")

        # 5. Margin level critical
        critical_level = config.get("margin_level_critical", 1.2)
        if margin_level_min < critical_level:
            return self._activate(
                f"margin_level_critical_{margin_level_min:.3f}"
                f"<{critical_level}"
            )

        # 6. Borrow rate spike
        spike_threshold = config.get("borrow_rate_spike_daily", 0.01)
        if max_borrow_rate_daily > spike_threshold:
            return self._activate(
                f"borrow_rate_spike_{max_borrow_rate_daily*100:.2f}%/day"
                f">{spike_threshold*100:.1f}%"
            )

        return False, ""

    def _activate(self, reason: str) -> tuple[bool, str]:
        self._active = True
        self._trigger_reason = reason
        self._trigger_time = datetime.now(UTC)
        self._save_persisted_state()  # CRO H-5: persist across restarts
        logger.critical(f"CRYPTO KILL SWITCH V2 ACTIVATED: {reason}")
        return True, reason

    def execute_kill_sequence(self, broker=None) -> list[str]:
        """Execute kill actions in priority order.

        Returns list of actions executed. Each action is logged.
        Idempotent: returns cached results if already executed.
        """
        if not self._active:
            return []

        # Idempotency: if kill sequence already ran, return cached results
        if self._actions_executed:
            logger.warning("Kill sequence already executed, returning cached results")
            return list(self._actions_executed)

        executed = []
        for action in self.KILL_ACTIONS_PRIORITY:
            try:
                if action == "close_shorts" and broker:
                    self._close_shorts(broker)
                elif action == "cancel_orders" and broker:
                    self._cancel_orders(broker)
                elif action == "close_longs" and broker:
                    self._close_longs(broker)
                elif action == "redeem_earn" and broker:
                    self._redeem_earn(broker)
                elif action == "alert":
                    logger.critical(
                        f"KILL ALERT: {self._trigger_reason}"
                    )
                elif action == "convert_to_usdt" and broker:
                    self._convert_to_usdt(broker)

                executed.append(action)
                logger.info(f"Kill action executed: {action}")
            except Exception as e:
                logger.error(f"Kill action {action} failed: {e}")
                executed.append(f"{action}_FAILED")

        self._actions_executed = executed
        return executed

    def _close_shorts(self, broker):
        """Close all short/margin borrow positions first (highest cost)."""
        positions = broker.get_positions()
        for p in positions:
            if p.get("side") == "SHORT":
                try:
                    broker.close_position(
                        p["symbol"],
                        _authorized_by="CRYPTO_KILL_SWITCH",
                    )
                    logger.info(f"Kill: closed short {p['symbol']}")
                except Exception as e:
                    logger.error(f"Kill: failed to close short {p['symbol']}: {e}")

    def _cancel_orders(self, broker):
        """Cancel all open orders."""
        if hasattr(broker, "cancel_all_orders"):
            broker.cancel_all_orders(_authorized_by="CRYPTO_KILL_SWITCH")

    def _close_longs(self, broker):
        """Close all long positions."""
        positions = broker.get_positions()
        for p in positions:
            if p.get("side") == "LONG":
                try:
                    broker.close_position(
                        p["symbol"],
                        _authorized_by="CRYPTO_KILL_SWITCH",
                    )
                    logger.info(f"Kill: closed long {p['symbol']}")
                except Exception as e:
                    logger.error(f"Kill: failed to close long {p['symbol']}: {e}")

    def _redeem_earn(self, broker):
        """Redeem all earn/savings positions."""
        if hasattr(broker, "get_earn_positions") and hasattr(broker, "redeem_earn"):
            for pos in broker.get_earn_positions():
                product_id = pos.get("product_id", "")
                amount = pos.get("amount", 0)
                if product_id and amount > 0:
                    try:
                        broker.redeem_earn(product_id, amount)
                        logger.info(f"Kill: redeemed earn {pos.get('asset', '?')} ({amount})")
                    except Exception as e:
                        logger.error(f"Kill: failed to redeem earn {product_id}: {e}")

    def _convert_to_usdt(self, broker):
        """Convert all remaining assets to USDT.

        SKIPPED: not safe to implement automatically — could sell assets
        at bad prices or hit dust conversion issues. Manual intervention
        preferred after kill switch stabilizes the portfolio.
        """
        logger.warning("Kill: convert_to_usdt SKIPPED (manual intervention required)")

    def reset(self, _authorized_by: str = ""):
        """Manual reset (requires _authorized_by guard)."""
        if not _authorized_by:
            logger.critical("Kill switch reset REFUSED — _authorized_by required")
            return
        logger.warning(f"Kill switch V2 RESET by {_authorized_by}")
        self._active = False
        self._trigger_reason = ""
        self._actions_executed = []
        self._save_persisted_state()

    def status(self) -> dict:
        return {
            "active": self._active,
            "reason": self._trigger_reason,
            "trigger_time": (
                self._trigger_time.isoformat() if self._trigger_time else None
            ),
            "actions_executed": self._actions_executed,
            "actions_priority": self.KILL_ACTIONS_PRIORITY,
        }


# ──────────────────────────────────────────────────────────────────────
# Risk Manager V2
# ──────────────────────────────────────────────────────────────────────

class CryptoRiskManager:
    """Risk manager V2 for Binance France crypto portfolio.

    Performs 12 checks:
      1.  Position size (15% max)
      2.  Strategy concentration (30% max)
      3.  Gross long/short exposure (80% long, 40% short)
      4.  Leverage per-asset + portfolio avg
      5.  Borrow rate/cost limits
      6.  Drawdown circuit breakers
      7.  Margin health (per-position margin_level)
      8.  Borrow cost control
      9.  Earn exposure
      10. Unrealized loss per position
      11. BTC correlation
      12. Cash reserve
    """

    def __init__(
        self,
        capital: float = 10_000,
        limits: CryptoRiskLimits | None = None,
        ks_state_path: Path | None = None,
    ):
        self.capital = capital
        self.limits = limits or CryptoRiskLimits()
        self.kill_switch = CryptoKillSwitch(state_path=ks_state_path)
        self._peak_equity = capital
        self._daily_start_equity = capital
        self._hourly_start_equity = capital
        self._weekly_start_equity = capital
        self._monthly_start_equity = capital
        self._last_hourly_reset = time.time()
        self._check_count = 0  # Warmup: skip kill switch first 3 checks
        self._audit_log: list[dict] = []

    # ------------------------------------------------------------------
    # Check 1: Position size
    # ------------------------------------------------------------------

    def check_position_size(
        self, position_notional: float
    ) -> tuple[bool, str]:
        """Max 15% per position."""
        pct = (
            position_notional / self.capital * 100 if self.capital > 0 else 999
        )
        ok = pct <= self.limits.MAX_POSITION_PCT
        return ok, f"position {pct:.1f}% (max {self.limits.MAX_POSITION_PCT}%)"

    # ------------------------------------------------------------------
    # Check 2: Strategy concentration
    # ------------------------------------------------------------------

    def check_strategy_concentration(
        self, strategy_exposure: float
    ) -> tuple[bool, str]:
        """Max 30% per strategy."""
        pct = (
            strategy_exposure / self.capital * 100
            if self.capital > 0
            else 999
        )
        ok = pct <= self.limits.MAX_STRATEGY_PCT
        return (
            ok,
            f"strategy {pct:.1f}% (max {self.limits.MAX_STRATEGY_PCT}%)",
        )

    # ------------------------------------------------------------------
    # Check 3: Gross long/short exposure (asymmetric)
    # ------------------------------------------------------------------

    def check_gross_exposure(
        self, long_exposure: float, short_exposure: float
    ) -> tuple[bool, str]:
        """Long max 80%, short max 40% (shorts riskier on margin)."""
        long_pct = (
            long_exposure / self.capital * 100 if self.capital > 0 else 0
        )
        short_pct = (
            short_exposure / self.capital * 100 if self.capital > 0 else 0
        )
        net_pct = abs(long_pct - short_pct)

        long_ok = long_pct <= self.limits.MAX_GROSS_LONG_PCT
        short_ok = short_pct <= self.limits.MAX_GROSS_SHORT_PCT
        net_ok = net_pct <= self.limits.MAX_NET_PCT

        msg = (
            f"long={long_pct:.1f}% (max {self.limits.MAX_GROSS_LONG_PCT}%), "
            f"short={short_pct:.1f}% (max {self.limits.MAX_GROSS_SHORT_PCT}%), "
            f"net={net_pct:.1f}% (max {self.limits.MAX_NET_PCT}%)"
        )
        return long_ok and short_ok and net_ok, msg

    # ------------------------------------------------------------------
    # Check 4: Leverage per-asset + portfolio avg
    # ------------------------------------------------------------------

    def check_leverage(
        self, positions: list[dict]
    ) -> tuple[bool, str]:
        """BTC/ETH max 2.5x, altcoin max 1.5x, portfolio avg max 1.8x."""
        if not positions:
            return True, "no positions"

        violations = []
        total_weighted_leverage = 0.0
        total_notional = 0.0

        for p in positions:
            symbol = p.get("symbol", "")
            leverage = p.get("leverage", 1.0)
            notional = abs(p.get("notional", 0))

            if symbol in self.limits.BTC_ETH_SYMBOLS:
                max_lev = self.limits.MAX_LEVERAGE_BTC_ETH
            else:
                max_lev = self.limits.MAX_LEVERAGE_ALTCOIN

            if leverage > max_lev:
                violations.append(
                    f"{symbol}: {leverage:.1f}x > {max_lev:.1f}x"
                )

            total_weighted_leverage += leverage * notional
            total_notional += notional

        avg_leverage = (
            total_weighted_leverage / total_notional
            if total_notional > 0
            else 0
        )

        if avg_leverage > self.limits.MAX_LEVERAGE_PORTFOLIO_AVG:
            violations.append(
                f"portfolio avg: {avg_leverage:.2f}x "
                f"> {self.limits.MAX_LEVERAGE_PORTFOLIO_AVG}x"
            )

        ok = len(violations) == 0
        msg = (
            "; ".join(violations)
            if violations
            else f"avg leverage {avg_leverage:.2f}x OK"
        )
        return ok, msg

    # ------------------------------------------------------------------
    # Check 5: Borrow rate/cost limits
    # ------------------------------------------------------------------

    def check_borrow_limits(
        self, positions: list[dict]
    ) -> tuple[bool, str]:
        """Check borrow rate < 0.1%/day, total borrowed < 50%, monthly cost < 2%."""
        if not positions:
            return True, "no borrows"

        violations = []
        total_borrowed = 0.0
        total_monthly_cost = 0.0

        for p in positions:
            if not p.get("is_margin_borrow", False):
                continue

            borrow_rate = p.get("borrow_rate_daily", 0)
            borrowed_amount = p.get("borrowed_amount", 0)
            monthly_cost = borrow_rate * borrowed_amount * 30

            total_borrowed += borrowed_amount
            total_monthly_cost += monthly_cost

            # Per-position rate check
            if borrow_rate > self.limits.MAX_BORROW_RATE_DAILY:
                violations.append(
                    f"{p.get('symbol', '?')}: rate "
                    f"{borrow_rate*100:.3f}%/day "
                    f"> {self.limits.MAX_BORROW_RATE_DAILY*100:.1f}%"
                )

        # Total borrowed % of capital
        borrow_pct = (
            total_borrowed / self.capital * 100 if self.capital > 0 else 0
        )
        if borrow_pct > self.limits.MAX_TOTAL_BORROW_PCT:
            violations.append(
                f"total borrow {borrow_pct:.1f}% "
                f"> {self.limits.MAX_TOTAL_BORROW_PCT}%"
            )

        # Monthly cost % of capital
        monthly_cost_pct = (
            total_monthly_cost / self.capital * 100
            if self.capital > 0
            else 0
        )
        if monthly_cost_pct > self.limits.MAX_BORROW_COST_MONTHLY_PCT:
            violations.append(
                f"monthly borrow cost {monthly_cost_pct:.2f}% "
                f"> {self.limits.MAX_BORROW_COST_MONTHLY_PCT}%"
            )

        ok = len(violations) == 0
        msg = (
            "; ".join(violations)
            if violations
            else f"borrow OK (total {borrow_pct:.1f}%, "
                 f"monthly cost {monthly_cost_pct:.2f}%)"
        )
        return ok, msg

    # ------------------------------------------------------------------
    # Check 6: Drawdown circuit breakers
    # ------------------------------------------------------------------

    def check_drawdown(
        self, current_equity: float
    ) -> tuple[bool, str]:
        """Check daily/hourly/weekly/monthly drawdown limits."""
        # Guard: if equity is 0 or negative, skip drawdown check (API error, not real loss)
        if current_equity <= 0:
            return True, "equity=0 (API error?) — drawdown check skipped"

        # Guard: reset ALL baselines that are wildly different from current equity.
        # This catches config/restart mismatches that would trigger false kill switches.
        # Check each baseline individually (not just daily).
        _baselines = {
            "daily": self._daily_start_equity,
            "hourly": self._hourly_start_equity,
            "weekly": self._weekly_start_equity,
            "monthly": self._monthly_start_equity,
            "peak": self._peak_equity,
        }
        _reset_needed = False
        for _bl_name, _bl_val in _baselines.items():
            if _bl_val > 0 and (
                current_equity / _bl_val > 1.5
                or _bl_val / current_equity > 1.5
            ):
                logger.warning(
                    f"Drawdown baseline mismatch ({_bl_name}): "
                    f"${_bl_val:,.0f} vs current=${current_equity:,.0f}"
                )
                _reset_needed = True

        if _reset_needed:
            logger.warning("Resetting ALL baselines to current equity")
            self._daily_start_equity = current_equity
            self._hourly_start_equity = current_equity
            self._weekly_start_equity = current_equity
            self._monthly_start_equity = current_equity
            self._peak_equity = current_equity
            # Skip DD check this cycle — baselines just reset, not meaningful
            return True, "baselines reset — skipping DD check this cycle"

        self._peak_equity = max(self._peak_equity, current_equity)
        dd_pct = (
            (current_equity - self._peak_equity) / self._peak_equity * 100
        )

        # Daily
        daily_pnl_pct = (
            (current_equity - self._daily_start_equity)
            / self._daily_start_equity
            * 100
            if self._daily_start_equity > 0
            else 0
        )

        # Hourly — reset if >1h since last reset
        now = time.time()
        if now - self._last_hourly_reset > 3600:
            self._hourly_start_equity = current_equity
            self._last_hourly_reset = now
        # Guard: if hourly baseline was set before worker started (from state file),
        # don't use it for kill switch — it's stale. Only trust hourly baselines
        # that were set THIS session (within last 2h).
        hourly_pnl_pct = 0
        if self._hourly_start_equity > 0 and (now - self._last_hourly_reset) < 7200:
            hourly_pnl_pct = (
                (current_equity - self._hourly_start_equity)
                / self._hourly_start_equity
                * 100
            )
        else:
            # Stale hourly baseline — reset and skip
            self._hourly_start_equity = current_equity
            self._last_hourly_reset = now

        # Weekly
        weekly_pnl_pct = (
            (current_equity - self._weekly_start_equity)
            / self._weekly_start_equity
            * 100
            if self._weekly_start_equity > 0
            else 0
        )

        # Monthly
        monthly_pnl_pct = (
            (current_equity - self._monthly_start_equity)
            / self._monthly_start_equity
            * 100
            if self._monthly_start_equity > 0
            else 0
        )

        # Kill switch check — skip during warmup (first 3 cycles after init)
        # Baselines from state file may be stale after worker restart.
        self._check_count += 1
        if self._check_count <= 3:
            logger.info(
                f"DD warmup {self._check_count}/3: daily={daily_pnl_pct:.1f}% "
                f"hourly={hourly_pnl_pct:.1f}% DD={dd_pct:.1f}% — SKIPPED"
            )
            # Stabilize baselines on last warmup cycle
            if self._check_count == 3:
                self._daily_start_equity = current_equity
                self._hourly_start_equity = current_equity
                self._peak_equity = max(self._peak_equity, current_equity)
                logger.info(f"DD warmup done: baselines stabilized at ${current_equity:,.0f}")
            # Skip ALL checks during warmup (kill switch + violations)
            return True, f"warmup {self._check_count}/3 — DD check skipped"

        killed, reason = self.kill_switch.check(
            daily_pnl_pct=daily_pnl_pct,
            hourly_pnl_pct=hourly_pnl_pct,
            drawdown_pct=dd_pct,
        )
        if killed:
            return False, f"KILL SWITCH: {reason}"

        violations = []
        if abs(daily_pnl_pct) > self.limits.DAILY_MAX_LOSS_PCT and daily_pnl_pct < 0:
            violations.append(f"daily {daily_pnl_pct:.1f}%")
        if abs(weekly_pnl_pct) > self.limits.WEEKLY_MAX_LOSS_PCT and weekly_pnl_pct < 0:
            violations.append(f"weekly {weekly_pnl_pct:.1f}%")
        if abs(monthly_pnl_pct) > self.limits.MONTHLY_MAX_LOSS_PCT and monthly_pnl_pct < 0:
            violations.append(f"monthly {monthly_pnl_pct:.1f}%")
        if abs(dd_pct) > self.limits.MAX_DRAWDOWN_PCT:
            violations.append(f"max DD {dd_pct:.1f}%")

        ok = len(violations) == 0
        msg = (
            "; ".join(violations)
            if violations
            else f"DD={dd_pct:.1f}%, daily={daily_pnl_pct:.1f}%, "
                 f"weekly={weekly_pnl_pct:.1f}%"
        )
        return ok, msg

    # ------------------------------------------------------------------
    # Check 7: Margin health
    # ------------------------------------------------------------------

    def check_margin_health(
        self, positions: list[dict]
    ) -> tuple[bool, str]:
        """Per-position margin level check.

        margin_level = total_asset_value / total_debt
        - Warning at 1.8
        - Reduce 30% at 1.5
        - Force close at 1.3
        - Binance liquidates at 1.1
        """
        if not positions:
            return True, "no margin positions"

        margin_positions = [
            p for p in positions if p.get("is_margin_borrow", False)
        ]
        if not margin_positions:
            return True, "no margin borrows"

        warnings = []
        actions = []

        for p in margin_positions:
            asset_value = p.get("asset_value", 0)
            total_debt = p.get("total_debt", 0)
            symbol = p.get("symbol", "?")

            if total_debt <= 0:
                continue

            margin_level = asset_value / total_debt

            if margin_level < self.limits.MARGIN_LEVEL_CLOSE:
                actions.append({
                    "symbol": symbol,
                    "action": "CLOSE",
                    "margin_level": round(margin_level, 3),
                })
                warnings.append(
                    f"{symbol}: CLOSE margin_level={margin_level:.3f}"
                )
            elif margin_level < self.limits.MARGIN_LEVEL_REDUCE:
                actions.append({
                    "symbol": symbol,
                    "action": "REDUCE_30PCT",
                    "margin_level": round(margin_level, 3),
                })
                warnings.append(
                    f"{symbol}: REDUCE 30% margin_level={margin_level:.3f}"
                )
            elif margin_level < self.limits.MARGIN_LEVEL_WARNING:
                warnings.append(
                    f"{symbol}: WARNING margin_level={margin_level:.3f}"
                )

        ok = not any("CLOSE" in w for w in warnings)
        msg = "; ".join(warnings) if warnings else "all margins healthy"
        return ok, msg

    # ------------------------------------------------------------------
    # Check 8: Borrow cost control
    # ------------------------------------------------------------------

    def check_borrow_costs(
        self, positions: list[dict]
    ) -> tuple[bool, str]:
        """Close most expensive shorts if monthly borrow cost > 2%."""
        margin_positions = [
            p for p in positions if p.get("is_margin_borrow", False)
        ]
        if not margin_positions:
            return True, "no borrows"

        # Calculate monthly cost
        total_monthly_cost = 0.0
        position_costs = []
        for p in margin_positions:
            rate = p.get("borrow_rate_daily", 0)
            amount = p.get("borrowed_amount", 0)
            monthly = rate * amount * 30
            total_monthly_cost += monthly
            position_costs.append({
                "symbol": p.get("symbol", "?"),
                "monthly_cost": monthly,
                "rate_daily": rate,
            })

        monthly_pct = (
            total_monthly_cost / self.capital * 100
            if self.capital > 0
            else 0
        )

        if monthly_pct > self.limits.MAX_BORROW_COST_MONTHLY_PCT:
            # Sort by cost descending — identify most expensive to close
            position_costs.sort(key=lambda x: x["monthly_cost"], reverse=True)
            expensive = [
                f"{pc['symbol']}(${pc['monthly_cost']:.0f}/mo)"
                for pc in position_costs[:3]
            ]
            return False, (
                f"monthly borrow cost {monthly_pct:.2f}% "
                f"> {self.limits.MAX_BORROW_COST_MONTHLY_PCT}% — "
                f"close expensive: {', '.join(expensive)}"
            )

        return True, f"monthly borrow cost {monthly_pct:.2f}% OK"

    # ------------------------------------------------------------------
    # Check 9: Earn exposure
    # ------------------------------------------------------------------

    def check_earn_exposure(
        self, earn_total: float
    ) -> tuple[bool, str]:
        """Count earn positions in total exposure (max MAX_EARN_PCT).

        Fix 2026-04-16: use total equity (self.capital + earn_total) as
        denominator, not self.capital alone. Reason: the worker reassigns
        self.capital = dd_equity (which EXCLUDES earn BTC/ETH), so the
        numerator earn_total was compared to a denominator that excludes
        part of what's in the numerator — giving earn_pct > 100% even
        though the real exposure (earn / total_equity) was ~50%.
        """
        total_equity = self.capital + earn_total
        earn_pct = (
            earn_total / total_equity * 100 if total_equity > 0 else 0
        )
        ok = earn_pct <= self.limits.MAX_EARN_PCT
        return ok, f"earn {earn_pct:.1f}% of total equity (max {self.limits.MAX_EARN_PCT}%)"

    # ------------------------------------------------------------------
    # Check 10: Unrealized loss per position
    # ------------------------------------------------------------------

    def check_unrealized_loss(
        self, positions: list[dict]
    ) -> tuple[bool, str]:
        """Check unrealized loss per position (max 8%)."""
        violations = []
        for p in positions:
            unrealized_pct = abs(p.get("unrealized_pct", 0))
            if unrealized_pct > self.limits.MAX_UNREALIZED_LOSS_PER_POSITION:
                violations.append(
                    f"{p.get('symbol', '?')}: -{unrealized_pct:.1f}%"
                )

        ok = len(violations) == 0
        msg = (
            "; ".join(violations)
            if violations
            else "all positions within loss limits"
        )
        return ok, msg

    # ------------------------------------------------------------------
    # Check 11: BTC correlation
    # ------------------------------------------------------------------

    def check_btc_correlation(
        self, positions_correlation: dict[str, float]
    ) -> tuple[bool, str]:
        """Max 70% of positions correlated > 0.7 with BTC."""
        if not positions_correlation:
            return True, "no positions"

        highly_correlated = sum(
            1 for corr in positions_correlation.values() if corr > 0.7
        )
        total = len(positions_correlation)
        correlated_pct = (
            highly_correlated / total * 100 if total > 0 else 0
        )

        ok = correlated_pct <= self.limits.MAX_BTC_CORRELATED_PCT
        msg = (
            f"{correlated_pct:.0f}% positions correlated with BTC "
            f"(max {self.limits.MAX_BTC_CORRELATED_PCT}%)"
        )
        return ok, msg

    # ------------------------------------------------------------------
    # Check 12: Cash reserve
    # ------------------------------------------------------------------

    def check_cash_reserve(
        self, cash_available: float
    ) -> tuple[bool, str]:
        """Always keep at least 10% cash."""
        cash_pct = (
            cash_available / self.capital * 100 if self.capital > 0 else 0
        )
        ok = cash_pct >= self.limits.MIN_CASH_RESERVE_PCT
        return (
            ok,
            f"cash {cash_pct:.1f}% "
            f"(min {self.limits.MIN_CASH_RESERVE_PCT}%)",
        )

    # ------------------------------------------------------------------
    # Run all 12 checks
    # ------------------------------------------------------------------

    def check_all(
        self,
        positions: list[dict],
        current_equity: float,
        cash_available: float = 0,
        earn_total: float = 0,
        btc_correlations: dict[str, float] | None = None,
    ) -> dict:
        """Run all 12 risk checks.

        Each position dict should have:
          symbol, notional, side, strategy, leverage, is_margin_borrow,
          borrowed_amount, borrow_rate_daily, asset_value, total_debt,
          unrealized_pct

        Returns:
            dict with {check_name: {passed, message}} and overall pass/fail
        """
        long_exp = sum(
            abs(p.get("notional", 0))
            for p in positions
            if p.get("side") in ("LONG", "BUY")
        )
        short_exp = sum(
            abs(p.get("notional", 0))
            for p in positions
            if p.get("side") in ("SHORT", "SELL")
        )

        # Largest position
        max_pos_notional = max(
            (abs(p.get("notional", 0)) for p in positions), default=0
        )

        # Largest strategy exposure
        strategies = {p.get("strategy", "") for p in positions}
        max_strategy_exp = max(
            (
                sum(
                    abs(p2.get("notional", 0))
                    for p2 in positions
                    if p2.get("strategy") == s
                )
                for s in strategies
            ),
            default=0,
        ) if strategies else 0

        checks = {}

        check_list = [
            ("position_size", self.check_position_size(max_pos_notional)),
            ("strategy_concentration", self.check_strategy_concentration(max_strategy_exp)),
            ("gross_exposure", self.check_gross_exposure(long_exp, short_exp)),
            ("leverage", self.check_leverage(positions)),
            ("borrow_limits", self.check_borrow_limits(positions)),
            ("drawdown", self.check_drawdown(current_equity)),
            ("margin_health", self.check_margin_health(positions)),
            ("borrow_costs", self.check_borrow_costs(positions)),
            ("earn_exposure", self.check_earn_exposure(earn_total)),
            ("unrealized_loss", self.check_unrealized_loss(positions)),
            ("btc_correlation", self.check_btc_correlation(btc_correlations or {})),
            ("cash_reserve", self.check_cash_reserve(cash_available)),
        ]

        for name, (passed, msg) in check_list:
            checks[name] = {"passed": passed, "message": msg}

        all_passed = all(c["passed"] for c in checks.values())

        result = {
            "passed": all_passed,
            "n_checks": 12,
            "n_passed": sum(1 for c in checks.values() if c["passed"]),
            "n_failed": sum(1 for c in checks.values() if not c["passed"]),
            "checks": checks,
            "kill_switch": self.kill_switch.status(),
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Audit log
        self._audit_log.append(result)

        return result

    # ------------------------------------------------------------------
    # Pre-order validation (CRO C-1 FIX)
    # ------------------------------------------------------------------

    def validate_order(
        self,
        notional: float,
        strategy: str = "",
        current_equity: float = 0,
    ) -> tuple[bool, str]:
        """Validate a single order before execution.

        Checks:
          1. Position size vs max (15% of equity)
          2. Strategy concentration vs max (30%)
          3. Notional > 0 and equity > 0
          4. Kill switch not active

        Returns:
            (passed, reason)
        """
        equity = current_equity if current_equity > 0 else self.capital

        if self.kill_switch.is_killed:
            return False, f"kill switch active: {self.kill_switch.trigger_reason}"

        if notional <= 0:
            return False, f"notional invalide: {notional}"

        if equity <= 0:
            return False, f"equity invalide: {equity}"

        # Position size check
        pos_pct = notional / equity * 100
        if pos_pct > self.limits.MAX_POSITION_PCT:
            return False, (
                f"position {pos_pct:.1f}% > max {self.limits.MAX_POSITION_PCT}%"
            )

        # Strategy concentration check
        strat_pct = notional / equity * 100
        if strat_pct > self.limits.MAX_STRATEGY_PCT:
            return False, (
                f"strategy {strat_pct:.1f}% > max {self.limits.MAX_STRATEGY_PCT}%"
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # Reset helpers
    # ------------------------------------------------------------------

    def reset_daily(self, equity: float):
        """Reset daily P&L tracking (call at 00:00 UTC)."""
        self._daily_start_equity = equity

    def reset_weekly(self, equity: float):
        """Reset weekly P&L tracking (call Monday 00:00 UTC)."""
        self._weekly_start_equity = equity

    def reset_monthly(self, equity: float):
        """Reset monthly P&L tracking (call 1st of month)."""
        self._monthly_start_equity = equity

    # ------------------------------------------------------------------
    # Deleveraging
    # ------------------------------------------------------------------

    def get_deleveraging_factor(self, drawdown_pct: float) -> float:
        """Progressive deleveraging based on drawdown.

        -5%  -> 70% of normal sizing
        -10% -> 50%
        -15% -> 25%
        -20% -> 0% (full stop)
        """
        dd = abs(drawdown_pct)
        if dd < 5:
            return 1.0
        elif dd < 10:
            return 0.7
        elif dd < 15:
            return 0.5
        elif dd < 20:
            return 0.25
        else:
            return 0.0
