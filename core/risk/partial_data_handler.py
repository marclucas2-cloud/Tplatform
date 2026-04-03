"""Graceful degradation for cross-broker calculations.

When a broker is DOWN:
  - Regime engine: that broker's regime = UNKNOWN, global = worst-of
  - Unified portfolio: DD calculated on HEALTHY brokers only
  - NAV: sum(HEALTHY) + last_known(DOWN brokers)
  - Circuit breakers: apply on partial DD (conservative)
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("risk.partial_data")

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class BrokerData:
    name: str
    equity: float
    positions: list
    cash: float
    is_reliable: bool = True
    is_frozen: bool = False
    frozen_at: Optional[str] = None


class PartialDataHandler:
    """Handles partial data when one or more brokers are down."""

    _LAST_KNOWN_PATH = ROOT / "data" / "risk" / "last_known_broker_state.json"

    def __init__(self):
        self._last_known: dict[str, dict] = {}
        self._load_last_known()

    def prepare_broker_data(
        self,
        broker_name: str,
        data: Optional[dict],
        is_healthy: bool,
    ) -> BrokerData:
        """Prepare broker data, using frozen values if broker is down.

        Args:
            broker_name: "binance", "ibkr", "alpaca"
            data: Real-time data dict or None if unavailable
            is_healthy: True if broker health is HEALTHY

        Returns:
            BrokerData with is_reliable/is_frozen flags set appropriately
        """
        if data and is_healthy and data.get("equity", 0) > 0:
            # Fresh, reliable data — save as last known
            bd = BrokerData(
                name=broker_name,
                equity=float(data.get("equity", 0)),
                positions=data.get("positions", []),
                cash=float(data.get("cash", 0)),
                is_reliable=True,
                is_frozen=False,
            )
            self._save_last_known(broker_name, data)
            return bd

        # Broker is down or data unavailable — use frozen values
        frozen = self._last_known.get(broker_name)
        if frozen:
            logger.warning(
                f"Broker {broker_name} DOWN — using frozen data "
                f"from {frozen.get('frozen_at', '?')}"
            )
            return BrokerData(
                name=broker_name,
                equity=float(frozen.get("equity", 0)),
                positions=frozen.get("positions", []),
                cash=float(frozen.get("cash", 0)),
                is_reliable=False,
                is_frozen=True,
                frozen_at=frozen.get("frozen_at"),
            )

        # No frozen data — return zeros
        logger.warning(
            f"Broker {broker_name} DOWN with no frozen data — "
            f"using zeros (conservative)"
        )
        return BrokerData(
            name=broker_name,
            equity=0,
            positions=[],
            cash=0,
            is_reliable=False,
            is_frozen=False,
        )

    def calculate_partial_nav(
        self, broker_data: list[BrokerData]
    ) -> tuple[float, bool, list[str]]:
        """Calculate NAV with partial data.

        Returns:
            (nav, is_partial, excluded_brokers)
        """
        nav = 0.0
        is_partial = False
        excluded = []

        for bd in broker_data:
            nav += bd.equity
            if not bd.is_reliable:
                is_partial = True
                excluded.append(bd.name)

        return nav, is_partial, excluded

    def calculate_partial_dd(
        self,
        broker_data: list[BrokerData],
        peak_nav: float,
    ) -> tuple[float, bool]:
        """Calculate drawdown using only reliable data.

        Conservative approach: if a broker is down, assume
        its PnL is 0 (not positive, not negative).
        """
        reliable_nav = sum(bd.equity for bd in broker_data if bd.is_reliable)
        frozen_nav = sum(bd.equity for bd in broker_data if bd.is_frozen)

        # Total NAV = reliable + frozen (best estimate)
        total_nav = reliable_nav + frozen_nav

        if peak_nav <= 0:
            return 0.0, False

        dd_pct = (1 - total_nav / peak_nav) * 100
        is_partial = any(not bd.is_reliable for bd in broker_data)

        return max(0, dd_pct), is_partial

    def get_regime_with_partial(
        self,
        regimes: dict[str, str],
        healthy_brokers: list[str],
    ) -> dict[str, str]:
        """Compute global regime with partial broker data.

        Brokers that are DOWN get regime=UNKNOWN.
        Global regime = worst of available regimes + UNKNOWN for missing.
        """
        result = dict(regimes)

        # Map broker -> regime asset classes
        broker_asset_map = {
            "ibkr": ["fx", "eu_equity"],
            "binance": ["crypto"],
            "alpaca": ["us_equity"],
        }

        for broker, asset_classes in broker_asset_map.items():
            if broker not in healthy_brokers:
                for ac in asset_classes:
                    result[ac] = "UNKNOWN"

        # Global = worst of all
        regime_severity = {
            "UNKNOWN": 3,
            "RISK_OFF": 2,
            "NEUTRAL": 1,
            "RISK_ON": 0,
        }
        worst_score = 0
        for ac, regime in result.items():
            if ac == "global":
                continue
            score = regime_severity.get(regime, 3)
            worst_score = max(worst_score, score)

        severity_to_regime = {v: k for k, v in regime_severity.items()}
        result["global"] = severity_to_regime.get(worst_score, "UNKNOWN")

        return result

    def format_portfolio_status(
        self, broker_data: list[BrokerData], nav: float
    ) -> str:
        """Format portfolio status for Telegram, showing frozen brokers."""
        lines = []
        for bd in broker_data:
            if bd.is_reliable:
                lines.append(f"  {bd.name}: ${bd.equity:,.0f}")
            elif bd.is_frozen:
                lines.append(
                    f"  {bd.name}: ${bd.equity:,.0f} "
                    f"(FROZEN — down since {bd.frozen_at or '?'})"
                )
            else:
                lines.append(f"  {bd.name}: $0 (DOWN, no data)")

        partial = any(not bd.is_reliable for bd in broker_data)
        partial_tag = " (PARTIAL)" if partial else ""
        lines.append(f"  NAV: ${nav:,.0f}{partial_tag}")
        return "\n".join(lines)

    def _save_last_known(self, broker: str, data: dict) -> None:
        """Persist last known broker state."""
        self._last_known[broker] = {
            **data,
            "frozen_at": datetime.now().isoformat(),
        }
        try:
            self._LAST_KNOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._LAST_KNOWN_PATH.write_text(
                json.dumps(self._last_known, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save last known state: {e}")

    def _load_last_known(self) -> None:
        """Load last known broker states from disk."""
        try:
            if self._LAST_KNOWN_PATH.exists():
                self._last_known = json.loads(
                    self._LAST_KNOWN_PATH.read_text(encoding="utf-8")
                )
        except Exception as e:
            logger.error(f"Failed to load last known state: {e}")
            self._last_known = {}
