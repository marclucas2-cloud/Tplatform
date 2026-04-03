"""EU/FX Risk Calibrator -- ERE adjustments for EU equities and FX positions.

Accounts for:
  - FX leverage (20-30x notional vs actual margin)
  - EU equity higher vol (vs US baseline)
  - Dynamic budget reduction on EUR vol spikes
  - Cross-market correlation detection (DAX/SPY, EUR/USD vs EU equities)

Usage:
    calibrator = EUFXRiskCalibrator()
    fx_ere = calibrator.calibrate_ere_for_fx(notional=40000, margin=2000, pair="EUR.USD")
    eu_ere = calibrator.calibrate_ere_for_eu_equity(position_value=1500, ticker="SAP")
    budget = calibrator.get_dynamic_risk_budget("fx", current_vol=0.12)
    exposure = calibrator.check_cross_market_exposure(positions)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# --- Volatility multipliers (annualized vol relative to US baseline ~16%) ---
# EU equities are ~20-30% more volatile than US large caps
EU_EQUITY_VOL_MULTIPLIER = {
    # German blue chips
    "SAP": 1.15,
    "SIE": 1.25,      # Siemens
    "ALV": 1.20,      # Allianz
    "BAS": 1.30,      # BASF
    "DTE": 1.10,      # Deutsche Telekom
    "BMW": 1.30,
    "VOW3": 1.35,     # Volkswagen
    "MBG": 1.30,      # Mercedes-Benz
    "ADS": 1.25,      # Adidas
    "MUV2": 1.15,     # Munich Re
    # French
    "MC": 1.20,       # LVMH
    "TTE": 1.25,      # TotalEnergies
    "AIR": 1.30,      # Airbus
    "SAN": 1.25,      # Sanofi
    "BNP": 1.35,      # BNP Paribas
    # Dutch
    "ASML": 1.30,
    # Indices / ETFs
    "DAX": 1.20,
    "ESTX50": 1.15,   # Euro Stoxx 50
    "CAC40": 1.20,
}
EU_EQUITY_VOL_DEFAULT = 1.25  # Default for unknown EU tickers

# FX pair volatility tiers (annualized vol)
FX_VOL_TIER = {
    "EUR.USD": 0.08,  "EURUSD": 0.08,
    "EUR.GBP": 0.07,  "EURGBP": 0.07,
    "EUR.JPY": 0.10,  "EURJPY": 0.10,
    "GBP.USD": 0.09,  "GBPUSD": 0.09,
    "AUD.JPY": 0.11,  "AUDJPY": 0.11,
    "USD.CHF": 0.08,  "USDCHF": 0.08,
    "NZD.USD": 0.10,  "NZDUSD": 0.10,
    "USD.JPY": 0.09,  "USDJPY": 0.09,
}
FX_VOL_DEFAULT = 0.10

# EUR volatility threshold for budget reduction
EURUSD_VOL_THRESHOLD_WARNING = 0.10   # 10% annualized -> start reducing
EURUSD_VOL_THRESHOLD_CRITICAL = 0.14  # 14% annualized -> severe reduction

# Cross-market correlation map (known directional relationships)
# Positive = correlated, Negative = inverse
CROSS_MARKET_CORRELATIONS = {
    ("DAX", "SPY"): 0.75,
    ("DAX", "ESTX50"): 0.95,
    ("DAX", "CAC40"): 0.90,
    ("EUR.USD", "DAX"): 0.40,
    ("EURUSD", "DAX"): 0.40,
    ("EUR.USD", "SPY"): -0.20,
    ("EURUSD", "SPY"): -0.20,
    ("EUR.JPY", "DAX"): 0.55,
    ("EURJPY", "DAX"): 0.55,
    ("GBP.USD", "EUR.USD"): 0.80,
    ("GBPUSD", "EURUSD"): 0.80,
}


@dataclass
class CrossMarketExposureResult:
    """Result of cross-market exposure analysis."""
    correlated_exposure: float    # $ in correlated positions
    hedged_exposure: float        # $ in hedged (offsetting) positions
    net_directional: float        # Net directional exposure
    alerts: List[str]
    details: List[Dict[str, Any]]
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "correlated_exposure": round(self.correlated_exposure, 2),
            "hedged_exposure": round(self.hedged_exposure, 2),
            "net_directional": round(self.net_directional, 2),
            "alerts": self.alerts,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


class EUFXRiskCalibrator:
    """Calibrate risk metrics for EU equities and FX positions."""

    def __init__(
        self,
        eu_vol_multipliers: Dict[str, float] | None = None,
        fx_vol_tiers: Dict[str, float] | None = None,
        eurusd_vol_warn: float = EURUSD_VOL_THRESHOLD_WARNING,
        eurusd_vol_crit: float = EURUSD_VOL_THRESHOLD_CRITICAL,
    ):
        self._eu_vol = eu_vol_multipliers or EU_EQUITY_VOL_MULTIPLIER
        self._fx_vol = fx_vol_tiers or FX_VOL_TIER
        self._eurusd_vol_warn = eurusd_vol_warn
        self._eurusd_vol_crit = eurusd_vol_crit

    # ------------------------------------------------------------------
    # 1. FX ERE calibration
    # ------------------------------------------------------------------

    def calibrate_ere_for_fx(
        self,
        notional: float,
        margin: float,
        pair: str,
    ) -> float:
        """Calculate Effective Risk Exposure for an FX position.

        FX trades are leveraged (20-30x). The ERE should reflect the
        actual risk, not the full notional nor just the margin.

        ERE = margin * leverage_factor * vol_factor

        Where:
          - leverage_factor = sqrt(notional / margin) -- penalizes leverage
            but less than linear (diversification benefit)
          - vol_factor = pair_vol / baseline_vol -- higher vol pairs get
            higher ERE per margin dollar

        Args:
            notional: Total notional exposure (e.g., $40,000 for 40K EUR.USD).
            margin: Margin required / used for this position.
            pair: FX pair identifier (e.g., "EUR.USD", "EURUSD").

        Returns:
            ERE in dollar terms.
        """
        if margin <= 0 or notional <= 0:
            return 0.0

        # Leverage factor: sqrt penalizes leverage sub-linearly
        # At 20x leverage, factor = sqrt(20) ~ 4.47
        # At 30x leverage, factor = sqrt(30) ~ 5.48
        leverage_ratio = notional / margin
        leverage_factor = leverage_ratio ** 0.5

        # Volatility factor: scale by pair vol relative to baseline (8%)
        baseline_vol = 0.08  # EUR.USD as baseline
        pair_vol = self._fx_vol.get(pair, FX_VOL_DEFAULT)
        vol_factor = pair_vol / baseline_vol

        ere = margin * leverage_factor * vol_factor

        logger.debug(
            f"FX ERE: {pair} notional=${notional:.0f} margin=${margin:.0f} "
            f"leverage={leverage_ratio:.0f}x lev_factor={leverage_factor:.2f} "
            f"vol_factor={vol_factor:.2f} -> ERE=${ere:.0f}"
        )
        return round(ere, 2)

    # ------------------------------------------------------------------
    # 2. EU equity ERE calibration
    # ------------------------------------------------------------------

    def calibrate_ere_for_eu_equity(
        self,
        position_value: float,
        ticker: str,
    ) -> float:
        """Calculate ERE for an EU equity position.

        EU stocks are generally more volatile than US large caps.
        ERE = position_value * vol_multiplier

        Args:
            position_value: Market value of the position ($).
            ticker: EU stock ticker (e.g., "SAP", "DAX").

        Returns:
            ERE in dollar terms.
        """
        if position_value <= 0:
            return 0.0

        vol_mult = self._eu_vol.get(ticker, EU_EQUITY_VOL_DEFAULT)
        ere = position_value * vol_mult

        logger.debug(
            f"EU ERE: {ticker} value=${position_value:.0f} "
            f"vol_mult={vol_mult:.2f} -> ERE=${ere:.0f}"
        )
        return round(ere, 2)

    # ------------------------------------------------------------------
    # 3. Dynamic risk budget based on EUR volatility
    # ------------------------------------------------------------------

    def get_dynamic_risk_budget(
        self,
        market: str,
        current_vol: float,
    ) -> float:
        """Return a risk budget multiplier (0-1) based on EUR volatility.

        When EURUSD 30d realized vol exceeds threshold, reduce exposure
        for FX and EU markets. US market gets smaller reduction.

        Args:
            market: Asset class -- "fx", "eu", "us", "futures".
            current_vol: Current EURUSD 30d annualized realized vol.

        Returns:
            Multiplier 0.0-1.0 to apply to base risk budget.
        """
        market_lower = market.lower()

        if current_vol <= self._eurusd_vol_warn:
            # Normal vol -- full budget
            return 1.0

        if current_vol >= self._eurusd_vol_crit:
            # Critical vol -- severe reduction for EUR-exposed markets
            if market_lower in ("fx", "eu"):
                multiplier = 0.30
            elif market_lower == "futures":
                multiplier = 0.50
            else:  # us
                multiplier = 0.70
        else:
            # Warning zone -- linear interpolation between warn and crit
            warn_to_crit_pct = (
                (current_vol - self._eurusd_vol_warn)
                / (self._eurusd_vol_crit - self._eurusd_vol_warn)
            )
            if market_lower in ("fx", "eu"):
                # 1.0 at warn -> 0.30 at crit
                multiplier = 1.0 - warn_to_crit_pct * 0.70
            elif market_lower == "futures":
                multiplier = 1.0 - warn_to_crit_pct * 0.50
            else:  # us
                multiplier = 1.0 - warn_to_crit_pct * 0.30

        logger.info(
            f"Dynamic risk budget: market={market} vol={current_vol:.2%} "
            f"-> multiplier={multiplier:.2f}"
        )
        return round(max(0.0, min(1.0, multiplier)), 2)

    # ------------------------------------------------------------------
    # 4. Cross-market exposure detection
    # ------------------------------------------------------------------

    def check_cross_market_exposure(
        self,
        positions: List[Dict[str, Any]],
    ) -> CrossMarketExposureResult:
        """Analyze cross-market correlations across open positions.

        Flags dangerous concentrations (e.g., long DAX + long EUR/USD both
        benefit from EU strength) and beneficial hedges (e.g., long US +
        short EUR/USD).

        Each position dict should have:
            symbol, direction (LONG|SHORT), market_value (or notional for FX)

        Returns:
            CrossMarketExposureResult with correlated/hedged exposure and alerts.
        """
        now = datetime.utcnow()
        if len(positions) < 2:
            return CrossMarketExposureResult(
                correlated_exposure=0.0,
                hedged_exposure=0.0,
                net_directional=0.0,
                alerts=[],
                details=[],
                timestamp=now,
            )

        correlated_total = 0.0
        hedged_total = 0.0
        alerts = []
        details = []

        # Check all pairs of positions
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                pos_a = positions[i]
                pos_b = positions[j]

                sym_a = pos_a.get("symbol", "")
                sym_b = pos_b.get("symbol", "")
                dir_a = pos_a.get("direction", "LONG").upper()
                dir_b = pos_b.get("direction", "LONG").upper()
                val_a = abs(float(pos_a.get("market_value", pos_a.get("notional", 0))))
                val_b = abs(float(pos_b.get("market_value", pos_b.get("notional", 0))))

                # Look up known correlation (try both orderings)
                known_corr = CROSS_MARKET_CORRELATIONS.get(
                    (sym_a, sym_b),
                    CROSS_MARKET_CORRELATIONS.get((sym_b, sym_a)),
                )

                if known_corr is None:
                    continue

                # Effective correlation considering directions:
                # Same direction + positive corr = correlated (bad)
                # Opposite direction + positive corr = hedged (good)
                # Same direction + negative corr = partially hedged
                same_direction = dir_a == dir_b
                if same_direction:
                    effective_corr = known_corr
                else:
                    effective_corr = -known_corr

                overlap_value = min(val_a, val_b) * abs(known_corr)

                if effective_corr > 0.3:
                    # Correlated exposure (risky concentration)
                    correlated_total += overlap_value
                    detail = {
                        "pair": (sym_a, sym_b),
                        "directions": (dir_a, dir_b),
                        "type": "CORRELATED",
                        "correlation": round(known_corr, 2),
                        "effective_correlation": round(effective_corr, 2),
                        "overlap_value": round(overlap_value, 2),
                    }
                    details.append(detail)

                    if effective_corr > 0.6:
                        alert = (
                            f"HIGH correlation: {sym_a} ({dir_a}) + "
                            f"{sym_b} ({dir_b}) corr={known_corr:.2f} "
                            f"overlap=${overlap_value:.0f}"
                        )
                        alerts.append(alert)
                        logger.warning(f"Cross-market: {alert}")

                elif effective_corr < -0.3:
                    # Hedged exposure (risk-reducing)
                    hedged_total += overlap_value
                    detail = {
                        "pair": (sym_a, sym_b),
                        "directions": (dir_a, dir_b),
                        "type": "HEDGED",
                        "correlation": round(known_corr, 2),
                        "effective_correlation": round(effective_corr, 2),
                        "overlap_value": round(overlap_value, 2),
                    }
                    details.append(detail)

        # Net directional: long - short across all positions
        net_long = sum(
            abs(float(p.get("market_value", p.get("notional", 0))))
            for p in positions
            if p.get("direction", "LONG").upper() == "LONG"
        )
        net_short = sum(
            abs(float(p.get("market_value", p.get("notional", 0))))
            for p in positions
            if p.get("direction", "LONG").upper() == "SHORT"
        )
        net_directional = net_long - net_short

        return CrossMarketExposureResult(
            correlated_exposure=round(correlated_total, 2),
            hedged_exposure=round(hedged_total, 2),
            net_directional=round(net_directional, 2),
            alerts=alerts,
            details=details,
            timestamp=now,
        )
