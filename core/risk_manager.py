"""
Risk Manager V2 — Validation pre-ordre + VaR + limites sectorielles.

Ajoute par-dessus le pipeline existant (paper_portfolio.py non modifie) :
  - Validation multi-criteres avant chaque ordre
  - VaR parametrique + CVaR (Expected Shortfall)
  - Exposition sectorielle avec sector_map configurable
  - Circuit-breaker horaire (en plus du daily existant)
"""

import numpy as np
from scipy import stats
import yaml
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class RiskManager:
    """Risk management V2 — validation pre-ordre + VaR + limites."""

    def __init__(self, limits_path=None):
        if limits_path is None:
            limits_path = Path(__file__).parent.parent / "config" / "limits.yaml"
        with open(limits_path) as f:
            self.limits = yaml.safe_load(f)
        self.sector_map = self.limits.get("sector_map", {})
        # Build reverse map: symbol -> sector
        self._symbol_to_sector = {}
        for sector, symbols in self.sector_map.items():
            for sym in symbols:
                self._symbol_to_sector[sym] = sector

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_order(self, order: dict, portfolio: dict) -> Tuple[bool, str]:
        """Valide un ordre contre TOUTES les limites.

        Args:
            order: {symbol, direction, notional, strategy}
                - symbol: ticker (e.g. "AAPL")
                - direction: "LONG" or "SHORT"
                - notional: montant USD de l'ordre
                - strategy: nom de la strategie
            portfolio: {equity, positions: [{symbol, notional, side, strategy}], cash}
                - equity: valeur totale du portefeuille
                - positions: liste de positions ouvertes
                - cash: cash disponible

        Returns:
            (passed: bool, message: str)
        """
        checks = [
            self._check_position_limit(order, portfolio),
            self._check_strategy_limit(order, portfolio),
            self._check_exposure_long(order, portfolio),
            self._check_exposure_short(order, portfolio),
            self._check_gross_exposure(order, portfolio),
            self._check_cash_reserve(order, portfolio),
            self._check_sector_limit(order, portfolio),
        ]
        for passed, msg in checks:
            if not passed:
                logger.warning(f"RISK REJECT: {msg}")
                return False, msg
        return True, "OK"

    def calculate_var(
        self, returns: list, confidence: float = 0.99, horizon: int = 1
    ) -> float:
        """VaR parametrique (hypothese normale).

        Args:
            returns: liste de rendements quotidiens (e.g. [-0.01, 0.02, ...])
            confidence: niveau de confiance (default 0.99)
            horizon: nombre de jours (scaling sqrt)

        Returns:
            VaR en valeur positive (perte maximale attendue)
        """
        arr = np.array(returns, dtype=float)
        if len(arr) < 2:
            return 0.0
        mean = arr.mean()
        std = arr.std(ddof=1)
        z = stats.norm.ppf(1 - confidence)
        return -(mean + z * std) * np.sqrt(horizon)

    def calculate_cvar(self, returns: list, confidence: float = 0.99) -> float:
        """CVaR (Expected Shortfall) — moyenne des pertes au-dela du VaR.

        Args:
            returns: liste de rendements quotidiens
            confidence: niveau de confiance

        Returns:
            CVaR en valeur positive (>= VaR)
        """
        arr = np.array(returns, dtype=float)
        if len(arr) < 2:
            return 0.0
        var = self.calculate_var(returns, confidence)
        tail = arr[arr < -var]
        if len(tail) > 0:
            return float(-tail.mean())
        return var

    def get_sector_exposure(self, positions: list, equity: float) -> dict:
        """Calcule l'exposition par secteur.

        Args:
            positions: [{symbol, notional, side}]
            equity: valeur totale du portefeuille

        Returns:
            {sector: exposure_ratio} (signed: long positive, short negative)
        """
        if equity <= 0:
            return {}
        sector_expo = {}
        for pos in positions:
            symbol = pos.get("symbol", "")
            notional = abs(float(pos.get("notional", 0)))
            side = pos.get("side", "long").upper()
            sector = self._symbol_to_sector.get(symbol, "other")
            sign = 1.0 if side == "LONG" else -1.0
            sector_expo[sector] = sector_expo.get(sector, 0.0) + sign * notional
        # Convertir en ratio
        return {k: v / equity for k, v in sector_expo.items()}

    def calculate_var_bootstrap(
        self, returns: list, confidence: float = 0.99, n_simulations: int = 10000
    ) -> float:
        """VaR bootstrap — resample les vrais returns pour capturer les fat tails.

        Au lieu de supposer une distribution normale, on re-echantillonne
        les rendements historiques reels (avec remise) pour construire
        la distribution empirique des pertes cumulees.

        Args:
            returns: liste de rendements quotidiens (e.g. [-0.01, 0.02, ...])
            confidence: niveau de confiance (default 0.99)
            n_simulations: nombre de tirages bootstrap (default 10000)

        Returns:
            VaR en valeur positive (perte maximale attendue)
        """
        arr = np.array(returns, dtype=float)
        if len(arr) < 2:
            return 0.0
        bootstrap_losses = []
        for _ in range(n_simulations):
            sample = np.random.choice(arr, size=len(arr), replace=True)
            bootstrap_losses.append(sample.sum())
        return -np.percentile(bootstrap_losses, (1 - confidence) * 100)

    def calculate_var_max(
        self, returns: list, confidence: float = 0.99, horizon: int = 1,
        n_simulations: int = 10000
    ) -> float:
        """VaR conservative — retourne max(VaR parametrique, VaR bootstrap).

        Combine les deux approches pour une estimation robuste :
        - VaR parametrique capture bien les rendements proches de la normale
        - VaR bootstrap capture les fat tails et l'asymetrie

        Args:
            returns: liste de rendements quotidiens
            confidence: niveau de confiance (default 0.99)
            horizon: nombre de jours pour la VaR parametrique
            n_simulations: nombre de tirages bootstrap

        Returns:
            max(VaR parametrique, VaR bootstrap) en valeur positive
        """
        var_param = self.calculate_var(returns, confidence, horizon)
        var_boot = self.calculate_var_bootstrap(returns, confidence, n_simulations)
        return max(var_param, var_boot)

    def check_progressive_deleveraging(
        self, current_dd_pct: float, max_dd_backtest: float = 0.018
    ) -> Tuple[int, float, str]:
        """Drawdown-based deleveraging progressif.

        Reduit l'exposition de facon progressive selon le drawdown courant
        par rapport au max drawdown observe en backtest.

        Niveaux :
          - DD > 50% du max backtest → reduire 30%
          - DD > 75% du max backtest → reduire 50%
          - DD > 100% du max backtest → circuit-breaker complet (100%)

        Args:
            current_dd_pct: drawdown courant en pourcentage (valeur positive, ex: 0.01 = 1%)
            max_dd_backtest: max drawdown observe en backtest (default 1.8%)

        Returns:
            (level: int 0-3, reduction_pct: float, message: str)
              - level 0: pas de reduction
              - level 1: reduction 30%
              - level 2: reduction 50%
              - level 3: circuit-breaker complet
        """
        dd = abs(current_dd_pct)
        threshold_50 = max_dd_backtest * 0.50   # 0.9% par defaut
        threshold_75 = max_dd_backtest * 0.75   # 1.35% par defaut
        threshold_100 = max_dd_backtest * 1.00  # 1.8% par defaut

        if dd >= threshold_100:
            msg = (
                f"CIRCUIT-BREAKER: DD {dd:.2%} >= max backtest {threshold_100:.2%}. "
                f"Fermeture totale des positions."
            )
            logger.critical(msg)
            return 3, 1.0, msg

        if dd >= threshold_75:
            msg = (
                f"DELEVERAGING LEVEL 2: DD {dd:.2%} >= 75% max backtest ({threshold_75:.2%}). "
                f"Reduction 50% de l'exposition."
            )
            logger.warning(msg)
            return 2, 0.50, msg

        if dd >= threshold_50:
            msg = (
                f"DELEVERAGING LEVEL 1: DD {dd:.2%} >= 50% max backtest ({threshold_50:.2%}). "
                f"Reduction 30% de l'exposition."
            )
            logger.warning(msg)
            return 1, 0.30, msg

        return 0, 0.0, "OK — drawdown dans les limites normales"

    def check_circuit_breaker(
        self, daily_pnl_pct: float, hourly_pnl_pct: float = None
    ) -> Tuple[bool, str]:
        """Check circuit-breakers (daily 5% + hourly 3%).

        Args:
            daily_pnl_pct: PnL journalier en pourcentage (negatif = perte)
            hourly_pnl_pct: PnL horaire (optionnel)

        Returns:
            (triggered: bool, message: str)
        """
        daily_limit = self.limits["risk_limits"]["circuit_breaker_daily_dd"]
        hourly_limit = self.limits["risk_limits"]["circuit_breaker_hourly_dd"]

        if abs(daily_pnl_pct) > daily_limit:
            return True, (
                f"CIRCUIT BREAKER DAILY: DD {daily_pnl_pct:.2%} > {daily_limit:.0%}"
            )
        if hourly_pnl_pct is not None and abs(hourly_pnl_pct) > hourly_limit:
            return True, (
                f"CIRCUIT BREAKER HOURLY: DD {hourly_pnl_pct:.2%} > {hourly_limit:.0%}"
            )
        return False, "OK"

    # ------------------------------------------------------------------
    # Private checks
    # ------------------------------------------------------------------

    def _check_position_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Ordre notional / equity < max_single_position."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["position_limits"]["max_single_position"]
        order_notional = abs(float(order.get("notional", 0)))
        # Existing position in same symbol
        existing = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("symbol") == order.get("symbol")
        )
        total = (existing + order_notional) / equity
        if total > limit:
            return False, (
                f"Position limit: {order['symbol']} "
                f"total {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_strategy_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of strategy positions / equity < max_single_strategy."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["position_limits"]["max_single_strategy"]
        strategy = order.get("strategy", "")
        existing = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("strategy") == strategy
        )
        order_notional = abs(float(order.get("notional", 0)))
        total = (existing + order_notional) / equity
        if total > limit:
            return False, (
                f"Strategy limit: {strategy} "
                f"total {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_exposure_long(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of long positions / equity < max_long_net."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["exposure_limits"]["max_long_net"]
        current_long = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("side", "").upper() == "LONG"
        )
        direction = order.get("direction", "").upper()
        addition = abs(float(order.get("notional", 0))) if direction == "LONG" else 0
        total = (current_long + addition) / equity
        if total > limit:
            return False, (
                f"Long exposure: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_exposure_short(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of short positions / equity < max_short_net."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["exposure_limits"]["max_short_net"]
        current_short = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("side", "").upper() == "SHORT"
        )
        direction = order.get("direction", "").upper()
        addition = abs(float(order.get("notional", 0))) if direction == "SHORT" else 0
        total = (current_short + addition) / equity
        if total > limit:
            return False, (
                f"Short exposure: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_gross_exposure(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """(long + short abs) / equity < max_gross."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["exposure_limits"]["max_gross"]
        current_gross = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
        )
        order_notional = abs(float(order.get("notional", 0)))
        total = (current_gross + order_notional) / equity
        if total > limit:
            return False, (
                f"Gross exposure: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_cash_reserve(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Cash after order / equity > min_cash."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["exposure_limits"]["min_cash"]
        cash = float(portfolio.get("cash", 0))
        order_notional = abs(float(order.get("notional", 0)))
        remaining_cash_ratio = (cash - order_notional) / equity
        if remaining_cash_ratio < limit:
            return False, (
                f"Cash reserve: {remaining_cash_ratio:.1%} < min {limit:.0%}"
            )
        return True, "OK"

    def _check_sector_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sector exposure < max_sector_exposure."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.limits["position_limits"]["max_sector_exposure"]
        symbol = order.get("symbol", "")
        order_sector = self._symbol_to_sector.get(symbol, "other")

        # Current sector exposure (absolute)
        sector_total = 0.0
        for p in portfolio.get("positions", []):
            p_symbol = p.get("symbol", "")
            p_sector = self._symbol_to_sector.get(p_symbol, "other")
            if p_sector == order_sector:
                sector_total += abs(float(p.get("notional", 0)))

        order_notional = abs(float(order.get("notional", 0)))
        total = (sector_total + order_notional) / equity
        if total > limit:
            return False, (
                f"Sector limit [{order_sector}]: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"
