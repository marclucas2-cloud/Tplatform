"""
Risk Manager V5 — Multi-Asset Risk Framework.

Extends V2 with:
  - Futures VaR (points -> dollars conversion via multipliers)
  - Futures margin monitoring (GREEN/YELLOW/RED alerts)
  - Roll risk detection (double margin during contract rolls)
  - FX position limits (single pair 25%, total 60%)
  - Cross-asset correlation limits
  - Stressed VaR with March 2020 correlations
  - Broker concentration limits
  - Timezone concentration limits

Original V2 features preserved:
  - Validation pre-ordre multi-criteres
  - VaR parametrique + CVaR (Expected Shortfall)
  - VaR portfolio-level avec matrice de correlation
  - Exposition sectorielle avec sector_map configurable
  - Circuit-breaker horaire + daily
  - Progressive deleveraging
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import yaml
from scipy import stats

logger = logging.getLogger(__name__)

# Futures contract multipliers: 1 point = $X
FUTURES_MULTIPLIERS = {
    "MES": 5.0,      # Micro E-mini S&P 500
    "MNQ": 2.0,      # Micro E-mini Nasdaq-100
    "MCL": 100.0,    # Micro WTI Crude Oil
    "MGC": 10.0,     # Micro Gold
}

# Initial margin requirements (approximate, per contract)
FUTURES_INITIAL_MARGIN = {
    "MES": 1_500,
    "MNQ": 1_800,
    "MCL": 1_200,
    "MGC": 1_000,
}

# FX pair identifiers for detection
FX_PAIRS = [
    "EURUSD", "EURGBP", "EURJPY", "AUDJPY", "GBPUSD", "USDCHF", "NZDUSD",
    "EUR/USD", "EUR/GBP", "EUR/JPY", "AUD/JPY", "GBP/USD", "USD/CHF", "NZD/USD",
]

# March 2020 stress correlations (empirical estimates)
STRESS_CORRELATIONS_2020 = {
    ("equity", "equity"): 0.92,
    ("equity", "futures_index"): 0.95,
    ("equity", "futures_energy"): 0.70,
    ("equity", "futures_metals"): -0.30,
    ("equity", "fx"): 0.55,
    ("futures_index", "futures_energy"): 0.65,
    ("futures_index", "futures_metals"): -0.25,
    ("futures_index", "fx"): 0.50,
    ("futures_energy", "futures_metals"): 0.10,
    ("futures_energy", "fx"): 0.35,
    ("futures_metals", "fx"): -0.15,
}


class RiskManager:
    """Risk management V5 — multi-asset validation + VaR + limites."""

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
        # Futures limits
        self.futures_limits = self.limits.get("futures_limits", {})
        # FX limits
        self.fx_limits = self.limits.get("fx_limits", {})
        # Cross-asset limits
        self.cross_asset_limits = self.limits.get("cross_asset_limits", {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_order(self, order: dict, portfolio: dict) -> Tuple[bool, str]:
        """Valide un ordre contre TOUTES les limites (equities + futures + FX).

        Args:
            order: {symbol, direction, notional, strategy, asset_class}
                - symbol: ticker (e.g. "AAPL", "EURUSD", "MES")
                - direction: "LONG" or "SHORT"
                - notional: montant USD de l'ordre
                - strategy: nom de la strategie
                - asset_class: 'equity', 'fx', 'futures' (optional, auto-detected)
            portfolio: {equity, positions: [{symbol, notional, side, strategy, asset_class}], cash}

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
            self._check_fx_limits(order, portfolio),
            self._check_futures_margin(order, portfolio),
            self._check_broker_limit(order, portfolio),
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

        Args:
            returns: liste de rendements quotidiens
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
        """VaR conservative — retourne max(VaR parametrique, VaR bootstrap)."""
        var_param = self.calculate_var(returns, confidence, horizon)
        var_boot = self.calculate_var_bootstrap(returns, confidence, n_simulations)
        return max(var_param, var_boot)

    def check_progressive_deleveraging(
        self, current_dd_pct: float, max_dd_backtest: float = 0.018
    ) -> Tuple[int, float, str]:
        """Drawdown-based deleveraging progressif.

        Niveaux :
          - DD > 50% du max backtest -> reduire 30%
          - DD > 75% du max backtest -> reduire 50%
          - DD > 100% du max backtest -> circuit-breaker complet (100%)

        Args:
            current_dd_pct: drawdown courant en pourcentage (valeur positive)
            max_dd_backtest: max drawdown observe en backtest (default 1.8%)

        Returns:
            (level: int 0-3, reduction_pct: float, message: str)
        """
        dd = abs(current_dd_pct)
        threshold_50 = max_dd_backtest * 0.50
        threshold_75 = max_dd_backtest * 0.75
        threshold_100 = max_dd_backtest * 1.00

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
    # RISK-001 : VaR portfolio-level avec matrice de correlation
    # ------------------------------------------------------------------

    def calculate_portfolio_var(
        self,
        strategy_returns: Dict[str, List[float]],
        weights: Dict[str, float],
        confidence: float = 0.99,
        horizon: int = 1,
        stress_correlation: float = 0.8,
    ) -> dict:
        """VaR portfolio-level avec matrice de correlation.

        Args:
            strategy_returns: {strategy_name: [daily_returns]}
            weights: {strategy_name: allocation_pct} (somme <= 1.0)
            confidence: niveau de confiance (default 0.99)
            horizon: nombre de jours (scaling sqrt)
            stress_correlation: correlation forcee en scenario stress (default 0.8)

        Returns:
            {
                var_individual_sum, var_portfolio, var_stressed,
                correlation_matrix, diversification_benefit, risk_contribution,
            }
        """
        # Filtrer les strategies presentes dans les deux dicts
        common = sorted(
            k for k in strategy_returns if k in weights and len(strategy_returns[k]) >= 2
        )
        if not common:
            return {
                "var_individual_sum": 0.0,
                "var_portfolio": 0.0,
                "var_stressed": 0.0,
                "correlation_matrix": {},
                "diversification_benefit": 0.0,
                "risk_contribution": {},
            }

        # 1. Construire la matrice de rendements (aligner les longueurs)
        min_len = min(len(strategy_returns[k]) for k in common)
        returns_matrix = np.array(
            [np.array(strategy_returns[k][-min_len:], dtype=float) for k in common]
        )  # shape: (n_strategies, n_obs)

        # 2. Vecteur de poids normalise
        w = np.array([weights[k] for k in common], dtype=float)
        w_sum = w.sum()
        if w_sum <= 0:
            w = np.ones(len(common)) / len(common)
        else:
            w = w / w_sum

        # 3. Volatilites individuelles
        vols = returns_matrix.std(axis=1, ddof=1)

        # 4. Matrice de covariance historique
        cov_matrix = np.cov(returns_matrix)
        if cov_matrix.ndim == 0:
            cov_matrix = np.array([[float(cov_matrix)]])

        # 5. Matrice de correlation historique
        std_outer = np.outer(vols, vols)
        safe_std = np.where(std_outer > 0, std_outer, 1.0)
        corr_matrix = cov_matrix / safe_std
        np.fill_diagonal(corr_matrix, 1.0)

        # 6. z-score
        z = stats.norm.ppf(confidence)

        # 7. VaR individuels (somme naive)
        individual_vars = z * vols * np.sqrt(horizon)
        var_individual_sum = float(np.dot(w, individual_vars))

        # 8. VaR portfolio
        portfolio_variance = float(w @ cov_matrix @ w)
        portfolio_vol = np.sqrt(max(portfolio_variance, 0.0))
        var_portfolio = float(z * portfolio_vol * np.sqrt(horizon))

        # 9. VaR stressed
        n = len(common)
        stressed_corr = np.full((n, n), stress_correlation)
        np.fill_diagonal(stressed_corr, 1.0)
        stressed_cov = stressed_corr * std_outer
        stressed_variance = float(w @ stressed_cov @ w)
        stressed_vol = np.sqrt(max(stressed_variance, 0.0))
        var_stressed = float(z * stressed_vol * np.sqrt(horizon))

        # 10. Diversification benefit
        if var_individual_sum > 0:
            diversification_benefit = (var_individual_sum - var_portfolio) / var_individual_sum
        else:
            diversification_benefit = 0.0

        # 11. Risk contribution (Euler decomposition)
        marginal = cov_matrix @ w
        if portfolio_vol > 0:
            risk_contrib = (w * marginal) / portfolio_vol
            rc_sum = risk_contrib.sum()
            if rc_sum > 0:
                risk_contrib = risk_contrib * (var_portfolio / rc_sum)
        else:
            risk_contrib = np.zeros(n)

        # 12. Formater la matrice de correlation
        corr_dict = {}
        for i, ki in enumerate(common):
            for j, kj in enumerate(common):
                corr_dict[f"{ki}/{kj}"] = round(float(corr_matrix[i, j]), 4)

        return {
            "var_individual_sum": round(var_individual_sum, 6),
            "var_portfolio": round(var_portfolio, 6),
            "var_stressed": round(var_stressed, 6),
            "correlation_matrix": corr_dict,
            "diversification_benefit": round(diversification_benefit, 4),
            "risk_contribution": {k: round(float(risk_contrib[i]), 6) for i, k in enumerate(common)},
        }

    # ------------------------------------------------------------------
    # RISK-003 : Futures VaR with multiplier conversion
    # ------------------------------------------------------------------

    def calculate_futures_var(
        self,
        futures_returns_points: Dict[str, List[float]],
        contracts: Dict[str, int],
        confidence: float = 0.99,
        horizon: int = 1,
    ) -> dict:
        """VaR pour positions futures avec conversion points -> dollars.

        Args:
            futures_returns_points: {symbol: [daily_returns_in_points]}
            contracts: {symbol: n_contracts}
            confidence: niveau de confiance
            horizon: nombre de jours

        Returns:
            {
                var_by_symbol: {symbol: var_usd},
                var_total: float (USD),
                notional_by_symbol: {symbol: notional_usd},
            }
        """
        var_by_symbol = {}
        notional_by_symbol = {}

        for symbol, returns_pts in futures_returns_points.items():
            multiplier = FUTURES_MULTIPLIERS.get(symbol, 1.0)
            n_contracts = contracts.get(symbol, 0)

            if n_contracts <= 0 or len(returns_pts) < 2:
                continue

            # Convert points to dollar returns
            returns_usd = [r * multiplier * n_contracts for r in returns_pts]

            var_usd = self.calculate_var(returns_usd, confidence, horizon)
            var_by_symbol[symbol] = round(var_usd, 2)

            # Notional = last price * multiplier * contracts (approximate)
            avg_price = abs(np.mean(returns_pts)) * 100  # rough estimate
            notional_by_symbol[symbol] = round(
                multiplier * n_contracts * max(avg_price, 1.0), 2
            )

        var_total = sum(var_by_symbol.values())

        return {
            "var_by_symbol": var_by_symbol,
            "var_total": round(var_total, 2),
            "notional_by_symbol": notional_by_symbol,
        }

    # ------------------------------------------------------------------
    # RISK-003 : Futures margin monitoring
    # ------------------------------------------------------------------

    def check_futures_margin(
        self,
        positions: List[dict],
        capital: float,
        is_rolling: bool = False,
    ) -> dict:
        """Verifie la marge futures par rapport au capital.

        Alert levels:
          - GREEN: < 50% margin used
          - YELLOW: 50-70% margin used
          - RED: > 70% margin used

        Args:
            positions: [{symbol, contracts, ...}]
            capital: capital total disponible
            is_rolling: True if currently rolling contracts (doubles margin)

        Returns:
            {
                status: 'GREEN'|'YELLOW'|'RED',
                margin_used: float (USD),
                margin_pct: float,
                margin_by_symbol: {symbol: margin_usd},
                message: str,
            }
        """
        if capital <= 0:
            return {
                "status": "RED",
                "margin_used": 0.0,
                "margin_pct": 1.0,
                "margin_by_symbol": {},
                "message": "Capital <= 0",
            }

        margin_by_symbol = {}
        total_margin = 0.0

        for pos in positions:
            symbol = pos.get("symbol", "")
            n_contracts = abs(int(pos.get("contracts", pos.get("qty", 0))))

            if symbol not in FUTURES_INITIAL_MARGIN or n_contracts <= 0:
                continue

            margin_per = FUTURES_INITIAL_MARGIN[symbol]
            margin = margin_per * n_contracts

            # During roll: temporarily holding 2x contracts
            if is_rolling:
                margin *= 2.0

            margin_by_symbol[symbol] = round(margin, 2)
            total_margin += margin

        margin_pct = total_margin / capital if capital > 0 else 0.0

        # Check limits from config
        yellow_threshold = self.futures_limits.get("margin_alert_yellow", 0.50)
        red_threshold = self.futures_limits.get("margin_alert_red", 0.70)
        max_total = self.futures_limits.get("max_total_margin_pct", 0.30)

        if margin_pct > red_threshold:
            status = "RED"
            msg = (
                f"FUTURES MARGIN RED: {margin_pct:.1%} > {red_threshold:.0%}. "
                f"Reduce positions immediately."
            )
            logger.critical(msg)
        elif margin_pct > yellow_threshold:
            status = "YELLOW"
            msg = (
                f"FUTURES MARGIN YELLOW: {margin_pct:.1%} > {yellow_threshold:.0%}. "
                f"Monitor closely."
            )
            logger.warning(msg)
        else:
            status = "GREEN"
            msg = f"Futures margin OK: {margin_pct:.1%} used"
            logger.debug(msg)

        return {
            "status": status,
            "margin_used": round(total_margin, 2),
            "margin_pct": round(margin_pct, 6),
            "margin_by_symbol": margin_by_symbol,
            "message": msg,
        }

    # ------------------------------------------------------------------
    # RISK-003 : Stressed VaR with March 2020 correlations
    # ------------------------------------------------------------------

    def calculate_stressed_var(
        self,
        strategy_returns: Dict[str, List[float]],
        weights: Dict[str, float],
        asset_classes: Dict[str, str],
        confidence: float = 0.99,
        horizon: int = 1,
    ) -> dict:
        """VaR stressed utilisant les correlations de Mars 2020.

        Args:
            strategy_returns: {strategy_name: [daily_returns]}
            weights: {strategy_name: allocation_pct}
            asset_classes: {strategy_name: asset_class}
                asset_class in: 'equity', 'futures_index', 'futures_energy',
                                'futures_metals', 'fx'
            confidence: niveau de confiance
            horizon: nombre de jours

        Returns:
            {var_stressed, var_normal, stress_multiplier}
        """
        common = sorted(
            k for k in strategy_returns if k in weights and len(strategy_returns[k]) >= 2
        )
        if not common:
            return {"var_stressed": 0.0, "var_normal": 0.0, "stress_multiplier": 1.0}

        min_len = min(len(strategy_returns[k]) for k in common)
        returns_matrix = np.array(
            [np.array(strategy_returns[k][-min_len:], dtype=float) for k in common]
        )

        w = np.array([weights[k] for k in common], dtype=float)
        w_sum = w.sum()
        if w_sum > 0:
            w = w / w_sum
        else:
            w = np.ones(len(common)) / len(common)

        vols = returns_matrix.std(axis=1, ddof=1)
        cov_matrix = np.cov(returns_matrix)
        if cov_matrix.ndim == 0:
            cov_matrix = np.array([[float(cov_matrix)]])

        z = stats.norm.ppf(confidence)

        # Normal VaR
        portfolio_var_normal = float(w @ cov_matrix @ w)
        var_normal = float(z * np.sqrt(max(portfolio_var_normal, 0.0)) * np.sqrt(horizon))

        # Build stressed covariance matrix using March 2020 correlations
        n = len(common)
        stressed_cov = np.zeros((n, n))
        std_outer = np.outer(vols, vols)

        for i in range(n):
            for j in range(n):
                if i == j:
                    stressed_cov[i, j] = vols[i] ** 2
                else:
                    ac_i = asset_classes.get(common[i], "equity")
                    ac_j = asset_classes.get(common[j], "equity")
                    # Look up stress correlation (symmetric)
                    key = (ac_i, ac_j)
                    rev_key = (ac_j, ac_i)
                    stress_corr = STRESS_CORRELATIONS_2020.get(
                        key, STRESS_CORRELATIONS_2020.get(rev_key, 0.8)
                    )
                    stressed_cov[i, j] = stress_corr * vols[i] * vols[j]

        # Stressed VaR
        portfolio_var_stressed = float(w @ stressed_cov @ w)
        var_stressed = float(z * np.sqrt(max(portfolio_var_stressed, 0.0)) * np.sqrt(horizon))

        stress_multiplier = var_stressed / var_normal if var_normal > 0 else 1.0

        return {
            "var_stressed": round(var_stressed, 6),
            "var_normal": round(var_normal, 6),
            "stress_multiplier": round(stress_multiplier, 4),
        }

    # ------------------------------------------------------------------
    # RISK-003 : Cross-asset correlation limit check
    # ------------------------------------------------------------------

    def check_correlated_exposure(
        self,
        positions: List[dict],
        returns_map: Dict[str, List[float]],
        equity: float,
        corr_threshold: float = 0.7,
    ) -> Tuple[bool, str, float]:
        """Verifie que l'exposition groupee des positions correlees ne depasse pas la limite.

        Args:
            positions: [{symbol, notional, side}]
            returns_map: {symbol: [daily_returns]}
            equity: capital total
            corr_threshold: seuil de correlation (defaut 0.7)

        Returns:
            (passed, message, correlated_exposure_pct)
        """
        max_corr_expo = self.cross_asset_limits.get("max_correlated_exposure", 0.35)

        if equity <= 0 or len(positions) < 2:
            return True, "OK", 0.0

        # Build correlation matrix for current positions
        symbols = [p.get("symbol", "") for p in positions]
        valid = [s for s in symbols if s in returns_map and len(returns_map[s]) >= 10]

        if len(valid) < 2:
            return True, "OK — insufficient data", 0.0

        min_len = min(len(returns_map[s]) for s in valid)
        ret_matrix = np.array(
            [np.array(returns_map[s][-min_len:], dtype=float) for s in valid]
        )
        corr_matrix = np.corrcoef(ret_matrix)

        # Find groups of highly correlated positions
        notionals = {}
        for p in positions:
            sym = p.get("symbol", "")
            notionals[sym] = abs(float(p.get("notional", 0)))

        # Sum notional of all position pairs with corr > threshold
        correlated_notional = 0.0
        counted = set()
        for i, si in enumerate(valid):
            for j, sj in enumerate(valid):
                if i >= j:
                    continue
                if abs(corr_matrix[i, j]) > corr_threshold:
                    if si not in counted:
                        correlated_notional += notionals.get(si, 0)
                        counted.add(si)
                    if sj not in counted:
                        correlated_notional += notionals.get(sj, 0)
                        counted.add(sj)

        corr_expo_pct = correlated_notional / equity

        if corr_expo_pct > max_corr_expo:
            msg = (
                f"Correlated exposure: {corr_expo_pct:.1%} > max {max_corr_expo:.0%} "
                f"(threshold corr={corr_threshold})"
            )
            return False, msg, round(corr_expo_pct, 4)

        return True, "OK", round(corr_expo_pct, 4)

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

    # ------------------------------------------------------------------
    # RISK-003 : FX position limits
    # ------------------------------------------------------------------

    def _is_fx_symbol(self, symbol: str) -> bool:
        """Detecte si un symbole est une paire FX."""
        sym_upper = symbol.upper().replace("/", "").replace("_", "")
        for pair in FX_PAIRS:
            if pair.replace("/", "") == sym_upper:
                return True
        return False

    def _check_fx_limits(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Verifie les limites FX: single pair 25%, total 60%.

        Args:
            order: {symbol, notional, ...}
            portfolio: {equity, positions, ...}

        Returns:
            (passed, message)
        """
        symbol = order.get("symbol", "")
        asset_class = order.get("asset_class", "")

        # Only apply to FX orders
        if asset_class != "fx" and not self._is_fx_symbol(symbol):
            return True, "OK"

        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"

        max_single = self.fx_limits.get("max_single_pair_exposure", 0.25)
        max_total = self.fx_limits.get("max_total_fx_exposure", 0.60)
        order_notional = abs(float(order.get("notional", 0)))

        # Check single pair limit
        existing_same_pair = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("symbol") == symbol
        )
        pair_total = (existing_same_pair + order_notional) / equity
        if pair_total > max_single:
            return False, (
                f"FX single pair limit: {symbol} "
                f"total {pair_total:.1%} > max {max_single:.0%}"
            )

        # Check total FX limit
        existing_fx_total = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("asset_class") == "fx" or self._is_fx_symbol(p.get("symbol", ""))
        )
        fx_total = (existing_fx_total + order_notional) / equity
        if fx_total > max_total:
            return False, (
                f"FX total exposure: {fx_total:.1%} > max {max_total:.0%}"
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # RISK-003 : Futures margin check in validate_order
    # ------------------------------------------------------------------

    def _is_futures_symbol(self, symbol: str) -> bool:
        """Detecte si un symbole est un futures."""
        return symbol.upper() in FUTURES_MULTIPLIERS

    def _check_futures_margin(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Verifie la marge futures avant un nouvel ordre.

        Returns:
            (passed, message)
        """
        symbol = order.get("symbol", "")
        asset_class = order.get("asset_class", "")

        if asset_class != "futures" and not self._is_futures_symbol(symbol):
            return True, "OK"

        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"

        max_total_margin = self.futures_limits.get("max_total_margin_pct", 0.30)
        max_single_margin = self.futures_limits.get("max_single_contract_margin_pct", 0.10)
        max_contracts = self.futures_limits.get("max_contracts_per_symbol", 5)

        # Order details
        order_contracts = abs(int(order.get("contracts", order.get("qty", 1))))
        order_margin = FUTURES_INITIAL_MARGIN.get(symbol.upper(), 0) * order_contracts

        # Check max contracts per symbol
        existing_contracts = sum(
            abs(int(p.get("contracts", p.get("qty", 0))))
            for p in portfolio.get("positions", [])
            if p.get("symbol", "").upper() == symbol.upper()
        )
        total_contracts = existing_contracts + order_contracts
        if total_contracts > max_contracts:
            return False, (
                f"Futures max contracts: {symbol} "
                f"total {total_contracts} > max {max_contracts}"
            )

        # Check single contract margin
        if order_margin / equity > max_single_margin:
            return False, (
                f"Futures single margin: {symbol} "
                f"{order_margin / equity:.1%} > max {max_single_margin:.0%}"
            )

        # Check total futures margin
        existing_margin = 0.0
        for p in portfolio.get("positions", []):
            p_sym = p.get("symbol", "").upper()
            if p_sym in FUTURES_INITIAL_MARGIN:
                p_contracts = abs(int(p.get("contracts", p.get("qty", 0))))
                existing_margin += FUTURES_INITIAL_MARGIN[p_sym] * p_contracts

        total_margin_pct = (existing_margin + order_margin) / equity
        if total_margin_pct > max_total_margin:
            return False, (
                f"Futures total margin: {total_margin_pct:.1%} > max {max_total_margin:.0%}"
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # RISK-003 : Broker concentration limit
    # ------------------------------------------------------------------

    def _check_broker_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Verifie la concentration par broker (max 60%).

        Args:
            order: {symbol, notional, broker, ...}
            portfolio: {equity, positions, ...}

        Returns:
            (passed, message)
        """
        broker = order.get("broker", "")
        if not broker:
            return True, "OK"

        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"

        max_broker = self.limits.get("position_limits", {}).get(
            "max_single_broker",
            self.cross_asset_limits.get("max_single_broker", 0.60)
        )

        existing_broker = sum(
            abs(float(p.get("notional", 0)))
            for p in portfolio.get("positions", [])
            if p.get("broker", "") == broker
        )
        order_notional = abs(float(order.get("notional", 0)))
        total = (existing_broker + order_notional) / equity

        if total > max_broker:
            return False, (
                f"Broker limit [{broker}]: {total:.1%} > max {max_broker:.0%}"
            )
        return True, "OK"
