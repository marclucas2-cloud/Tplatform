"""
Live VaR Calculator -- daily portfolio Value-at-Risk with real positions.

Recalculated every day after US market close using:
  - Actual live positions (not backtest)
  - 60-day rolling returns (real market data)
  - Live correlation matrix (not static backtest)
  - Stressed VaR (March 2020 correlations)

Alerts:
  - VaR live diverges > 50% from backtest VaR -> WARNING
  - VaR live > 3% of capital -> reduce exposure
  - VaR live > 5% of capital -> CRITICAL

Storage: SQLite for VaR history (data/var_history.db)
"""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

try:
    from scipy.stats import norm as _norm
    _norm_ppf = _norm.ppf
except ImportError:
    # Fallback: approximate z-scores for common confidence levels
    _APPROX_Z = {0.90: 1.2816, 0.95: 1.6449, 0.99: 2.3263}

    def _norm_ppf(q):
        if q in _APPROX_Z:
            return _APPROX_Z[q]
        # Linear interpolation between known values
        for (lo, hi) in [(0.90, 0.95), (0.95, 0.99)]:
            if lo <= q <= hi:
                t = (q - lo) / (hi - lo)
                return _APPROX_Z[lo] + t * (_APPROX_Z[hi] - _APPROX_Z[lo])
        raise ValueError(f"Cannot approximate z-score for quantile {q}")


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Futures contract multipliers: 1 point = $X
FUTURES_MULTIPLIERS = {
    "MES": 5.0,      # Micro E-mini S&P 500
    "MNQ": 2.0,      # Micro E-mini Nasdaq-100
    "MCL": 100.0,    # Micro WTI Crude Oil
    "MGC": 10.0,     # Micro Gold
}

# FX standard lot size (100,000 units of base currency)
FX_LOT_SIZE = 100_000

# Asset class mapping for correlation groups
ASSET_CLASS_MAP = {
    # Equities / ETFs
    "AAPL": "equity", "MSFT": "equity", "NVDA": "equity",
    "SPY": "equity", "QQQ": "equity", "IWM": "equity",
    "GLD": "equity", "TLT": "equity", "SVXY": "equity",
    "COIN": "equity", "MARA": "equity", "MSTR": "equity",
    # Futures
    "MES": "futures_index", "MNQ": "futures_index",
    "MCL": "futures_energy", "MGC": "futures_metals",
    # FX
    "EURUSD": "fx", "EURGBP": "fx", "EURJPY": "fx",
    "AUDJPY": "fx", "GBPUSD": "fx", "USDCHF": "fx",
}

# Stress correlations (March 2020 empirical estimates)
STRESS_CORRELATIONS = {
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

# Alert thresholds (fraction of capital)
ALERT_WARNING_PCT = 0.03   # VaR > 3% capital
ALERT_CRITICAL_PCT = 0.05  # VaR > 5% capital
ALERT_DIVERGENCE_PCT = 0.50  # VaR diverges > 50% from backtest


# ============================================================================
# LiveVaRCalculator
# ============================================================================

class LiveVaRCalculator:
    """Daily portfolio VaR calculator for live positions.

    Supports:
    - Parametric VaR (normal distribution)
    - Historical VaR (empirical distribution)
    - Stressed VaR (March 2020 correlations)
    - CVaR / Expected Shortfall
    - Portfolio-level with correlation matrix
    - Per-asset-class breakdown
    """

    def __init__(
        self,
        capital: float = 10_000,
        lookback_days: int = 60,
        confidence: float = 0.95,
        db_path: str = None,
        alert_callback=None,
    ):
        """
        Args:
            capital: current live capital (USD)
            lookback_days: rolling window for returns
            confidence: VaR confidence level (0.95 or 0.99)
            db_path: SQLite path for VaR history
            alert_callback: function(message, level) for alerts
        """
        self.capital = capital
        self.lookback_days = lookback_days
        self.confidence = confidence
        self._db_path = Path(db_path or "data/var_history.db")
        self._alert = alert_callback
        self._init_db()

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    def _init_db(self):
        """Create VaR history table if not exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS var_history (
                    date TEXT PRIMARY KEY,
                    portfolio_var_95 REAL,
                    portfolio_var_99 REAL,
                    portfolio_cvar_95 REAL,
                    stressed_var_95 REAL,
                    stressed_var_99 REAL,
                    capital REAL,
                    var_pct_of_capital REAL,
                    n_positions INTEGER,
                    details TEXT
                )
            """)
            conn.commit()

    def record_daily_var(self, var_result: dict):
        """Store daily VaR in SQLite for history/trending."""
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO var_history
                    (date, portfolio_var_95, portfolio_var_99, portfolio_cvar_95,
                     stressed_var_95, stressed_var_99, capital,
                     var_pct_of_capital, n_positions, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_str,
                    var_result.get("portfolio_var_95", 0.0),
                    var_result.get("portfolio_var_99", 0.0),
                    var_result.get("portfolio_cvar_95", 0.0),
                    var_result.get("stressed_var_95", 0.0),
                    var_result.get("stressed_var_99", 0.0),
                    self.capital,
                    var_result.get("var_pct_of_capital", 0.0),
                    var_result.get("n_positions", 0),
                    str(var_result.get("per_position_var", [])),
                ),
            )
            conn.commit()
        logger.info(
            "VaR recorded for %s: $%.2f (%.2f%% of capital)",
            date_str,
            var_result.get("portfolio_var_95", 0.0),
            var_result.get("var_pct_of_capital", 0.0) * 100,
        )

    def get_var_history(self, days: int = 30) -> list:
        """Get VaR history for trending analysis.

        Returns:
            List of dicts with date, portfolio_var_95, var_pct_of_capital, etc.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM var_history WHERE date >= ? ORDER BY date",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_var_trend(self, days: int = 30) -> dict:
        """Analyze VaR trend: increasing, stable, or decreasing.

        Returns:
            {trend: str, avg_var: float, max_var: float, current_var: float}
        """
        history = self.get_var_history(days)
        if not history:
            return {"trend": "unknown", "avg_var": 0.0, "max_var": 0.0, "current_var": 0.0}

        vars_list = [h["portfolio_var_95"] for h in history]
        current = vars_list[-1]
        avg_var = float(np.mean(vars_list))
        max_var = float(np.max(vars_list))

        # Determine trend using linear regression slope
        if len(vars_list) >= 3:
            x = np.arange(len(vars_list), dtype=float)
            slope = float(np.polyfit(x, vars_list, 1)[0])
            # Slope relative to average
            if avg_var > 0:
                relative_slope = slope / avg_var
            else:
                relative_slope = 0.0
            if relative_slope > 0.05:
                trend = "increasing"
            elif relative_slope < -0.05:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "trend": trend,
            "avg_var": round(avg_var, 2),
            "max_var": round(max_var, 2),
            "current_var": round(current, 2),
        }

    # ------------------------------------------------------------------
    # Single position VaR
    # ------------------------------------------------------------------

    def calculate_position_var(
        self,
        symbol: str,
        quantity: float,
        current_price: float,
        returns: np.ndarray,
        instrument_type: str = "EQUITY",
    ) -> dict:
        """Calculate VaR for a single position.

        Args:
            symbol: ticker (e.g. "AAPL", "EURUSD", "MES")
            quantity: position size (shares / contracts / lots)
            current_price: current market price
            returns: array of daily returns (percentage, e.g. 0.01 = +1%)
            instrument_type: EQUITY, FX, FUTURES

        Returns:
            {
                var_95, var_99, cvar_95 (in dollars),
                daily_vol, position_value, var_pct_of_capital
            }
        """
        returns = np.asarray(returns, dtype=float)
        if len(returns) < 2:
            return {
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
                "daily_vol": 0.0,
                "position_value": 0.0,
                "var_pct_of_capital": 0.0,
            }

        # Compute position value depending on instrument type
        position_value = self._position_value(
            symbol, quantity, current_price, instrument_type
        )

        var_95 = self._parametric_var(returns, 0.95, position_value)
        var_99 = self._parametric_var(returns, 0.99, position_value)
        cvar_95 = self._cvar(returns, 0.95, position_value)
        daily_vol = float(returns.std(ddof=1))

        var_pct = var_95 / self.capital if self.capital > 0 else 0.0

        return {
            "var_95": round(var_95, 2),
            "var_99": round(var_99, 2),
            "cvar_95": round(cvar_95, 2),
            "daily_vol": round(daily_vol, 6),
            "position_value": round(position_value, 2),
            "var_pct_of_capital": round(var_pct, 6),
        }

    # ------------------------------------------------------------------
    # Portfolio VaR
    # ------------------------------------------------------------------

    def calculate_portfolio_var(
        self,
        positions: list,
        returns_dict: dict,
    ) -> dict:
        """Calculate portfolio-level VaR with correlations.

        Args:
            positions: [{symbol, quantity, current_price, instrument_type}]
            returns_dict: {symbol: np.array of daily returns}

        Returns:
            {
                portfolio_var_95, portfolio_var_99, portfolio_cvar_95,
                diversification_benefit, correlation_matrix,
                per_position_var, var_pct_of_capital,
                stressed_var_95, stressed_var_99, n_positions
            }
        """
        # Filter positions that have returns data with enough observations
        valid = []
        for pos in positions:
            sym = pos["symbol"]
            if sym in returns_dict:
                ret = np.asarray(returns_dict[sym], dtype=float)
                if len(ret) >= 2:
                    valid.append(pos)

        if not valid:
            return self._empty_portfolio_result()

        symbols = [p["symbol"] for p in valid]
        n = len(symbols)

        # Align return lengths
        min_len = min(len(returns_dict[s]) for s in symbols)
        returns_matrix = np.array(
            [np.asarray(returns_dict[s], dtype=float)[-min_len:] for s in symbols]
        )  # shape: (n_positions, n_obs)

        # Position values
        pos_values = np.array([
            self._position_value(
                p["symbol"], p["quantity"], p["current_price"],
                p.get("instrument_type", "EQUITY"),
            )
            for p in valid
        ])

        # Individual VaRs
        vols = returns_matrix.std(axis=1, ddof=1)
        z_95 = _norm_ppf(0.95)
        z_99 = _norm_ppf(0.99)
        individual_vars_95 = z_95 * vols * pos_values
        individual_vars_99 = z_99 * vols * pos_values

        # Correlation matrix (empirical)
        corr_matrix = self._build_correlation_matrix(returns_dict, symbols)

        # Covariance matrix for dollar VaR
        vol_dollar = vols * pos_values  # dollar volatility per position
        cov_matrix = np.outer(vol_dollar, vol_dollar) * corr_matrix

        # Portfolio VaR (parametric)
        portfolio_variance_95 = float(np.ones(n) @ cov_matrix @ np.ones(n))
        portfolio_vol = np.sqrt(max(portfolio_variance_95, 0.0))
        portfolio_var_95 = float(z_95 * portfolio_vol)
        portfolio_var_99 = float(z_99 * portfolio_vol)

        # Portfolio CVaR (historical simulation)
        portfolio_cvar_95 = self._portfolio_cvar(returns_matrix, pos_values, 0.95)

        # Undiversified VaR (sum of individual VaRs)
        undiversified_var = float(individual_vars_95.sum())

        # Diversification benefit
        if undiversified_var > 0:
            div_benefit = undiversified_var / portfolio_var_95
        else:
            div_benefit = 1.0

        # Per-position contribution (Euler decomposition)
        per_position_var = []
        if portfolio_vol > 0:
            marginal = cov_matrix @ np.ones(n)
            risk_contrib = marginal / portfolio_vol * z_95
            rc_sum = risk_contrib.sum()
            for i, sym in enumerate(symbols):
                if rc_sum > 0:
                    contrib_pct = risk_contrib[i] / rc_sum
                else:
                    contrib_pct = 1.0 / n
                per_position_var.append({
                    "symbol": sym,
                    "var_95": round(float(individual_vars_95[i]), 2),
                    "contribution_pct": round(float(contrib_pct), 4),
                })
        else:
            for i, sym in enumerate(symbols):
                per_position_var.append({
                    "symbol": sym,
                    "var_95": 0.0,
                    "contribution_pct": 1.0 / n if n > 0 else 0.0,
                })

        # Correlation matrix as dict
        corr_dict = {}
        for i, si in enumerate(symbols):
            for j, sj in enumerate(symbols):
                corr_dict[f"{si}/{sj}"] = round(float(corr_matrix[i, j]), 4)

        # Stressed VaR
        stressed = self.calculate_stressed_var(valid, returns_dict)

        var_pct = portfolio_var_95 / self.capital if self.capital > 0 else 0.0

        return {
            "portfolio_var_95": round(portfolio_var_95, 2),
            "portfolio_var_99": round(portfolio_var_99, 2),
            "portfolio_cvar_95": round(portfolio_cvar_95, 2),
            "diversification_benefit": round(div_benefit, 4),
            "correlation_matrix": corr_dict,
            "per_position_var": per_position_var,
            "var_pct_of_capital": round(var_pct, 6),
            "stressed_var_95": stressed["stressed_var_95"],
            "stressed_var_99": stressed["stressed_var_99"],
            "n_positions": n,
        }

    # ------------------------------------------------------------------
    # Stressed VaR
    # ------------------------------------------------------------------

    def calculate_stressed_var(
        self,
        positions: list,
        returns_dict: dict,
    ) -> dict:
        """VaR with stress correlations (March 2020 scenario).

        Uses STRESS_CORRELATIONS instead of empirical correlations.
        Individual volatilities remain empirical (historical).

        Returns:
            {stressed_var_95, stressed_var_99}
        """
        valid = []
        for pos in positions:
            sym = pos["symbol"]
            if sym in returns_dict:
                ret = np.asarray(returns_dict[sym], dtype=float)
                if len(ret) >= 2:
                    valid.append(pos)

        if not valid:
            return {"stressed_var_95": 0.0, "stressed_var_99": 0.0}

        symbols = [p["symbol"] for p in valid]
        n = len(symbols)
        min_len = min(len(returns_dict[s]) for s in symbols)
        returns_matrix = np.array(
            [np.asarray(returns_dict[s], dtype=float)[-min_len:] for s in symbols]
        )

        pos_values = np.array([
            self._position_value(
                p["symbol"], p["quantity"], p["current_price"],
                p.get("instrument_type", "EQUITY"),
            )
            for p in valid
        ])

        vols = returns_matrix.std(axis=1, ddof=1)
        vol_dollar = vols * pos_values

        # Build stress correlation matrix
        stress_corr = self._build_stress_correlation_matrix(symbols)

        # Stress covariance
        stress_cov = np.outer(vol_dollar, vol_dollar) * stress_corr

        z_95 = _norm_ppf(0.95)
        z_99 = _norm_ppf(0.99)

        stress_variance = float(np.ones(n) @ stress_cov @ np.ones(n))
        stress_vol = np.sqrt(max(stress_variance, 0.0))

        return {
            "stressed_var_95": round(float(z_95 * stress_vol), 2),
            "stressed_var_99": round(float(z_99 * stress_vol), 2),
        }

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def check_var_alerts(
        self,
        var_result: dict,
        backtest_var: float = None,
    ) -> list:
        """Check VaR thresholds and send alerts.

        Returns:
            List of alert dicts: [{level, message}]
            - VaR > 3% capital -> WARNING
            - VaR > 5% capital -> CRITICAL (reduce exposure)
            - VaR diverges > 50% from backtest -> WARNING
        """
        alerts = []
        var_pct = var_result.get("var_pct_of_capital", 0.0)
        var_95 = var_result.get("portfolio_var_95", 0.0)

        # CRITICAL: VaR > 5% of capital
        if var_pct > ALERT_CRITICAL_PCT:
            alert = {
                "level": "CRITICAL",
                "message": (
                    f"VaR CRITICAL: ${var_95:.2f} = {var_pct:.1%} of capital "
                    f"(threshold: {ALERT_CRITICAL_PCT:.0%}). REDUCE EXPOSURE."
                ),
            }
            alerts.append(alert)
            logger.critical(alert["message"])
            if self._alert:
                self._alert(alert["message"], "CRITICAL")

        # WARNING: VaR > 3% of capital
        elif var_pct > ALERT_WARNING_PCT:
            alert = {
                "level": "WARNING",
                "message": (
                    f"VaR WARNING: ${var_95:.2f} = {var_pct:.1%} of capital "
                    f"(threshold: {ALERT_WARNING_PCT:.0%})."
                ),
            }
            alerts.append(alert)
            logger.warning(alert["message"])
            if self._alert:
                self._alert(alert["message"], "WARNING")

        # WARNING: VaR diverges > 50% from backtest
        if backtest_var is not None and backtest_var > 0:
            divergence = abs(var_95 - backtest_var) / backtest_var
            if divergence > ALERT_DIVERGENCE_PCT:
                alert = {
                    "level": "WARNING",
                    "message": (
                        f"VaR DIVERGENCE: live=${var_95:.2f} vs "
                        f"backtest=${backtest_var:.2f} "
                        f"({divergence:.0%} divergence, threshold: "
                        f"{ALERT_DIVERGENCE_PCT:.0%})."
                    ),
                }
                alerts.append(alert)
                logger.warning(alert["message"])
                if self._alert:
                    self._alert(alert["message"], "WARNING")

        return alerts

    # ------------------------------------------------------------------
    # Private: VaR computation methods
    # ------------------------------------------------------------------

    def _parametric_var(
        self,
        returns: np.ndarray,
        confidence: float,
        position_value: float,
    ) -> float:
        """VaR assuming normal distribution.

        VaR = z * sigma * position_value
        where z = norm.ppf(confidence), sigma = std(returns)
        """
        if len(returns) < 2 or position_value == 0:
            return 0.0
        sigma = float(returns.std(ddof=1))
        z = _norm_ppf(confidence)
        return abs(z * sigma * position_value)

    def _historical_var(
        self,
        returns: np.ndarray,
        confidence: float,
        position_value: float,
    ) -> float:
        """VaR from empirical distribution (non-parametric).

        Takes the (1 - confidence) percentile of P&L distribution.
        """
        if len(returns) < 2 or position_value == 0:
            return 0.0
        pnl = returns * position_value
        var = -float(np.percentile(pnl, (1 - confidence) * 100))
        return max(var, 0.0)

    def _cvar(
        self,
        returns: np.ndarray,
        confidence: float,
        position_value: float,
    ) -> float:
        """Conditional VaR (Expected Shortfall).

        Average of losses beyond the VaR threshold.
        CVaR >= VaR by definition.
        """
        if len(returns) < 2 or position_value == 0:
            return 0.0
        pnl = returns * position_value
        var_threshold = np.percentile(pnl, (1 - confidence) * 100)
        tail = pnl[pnl <= var_threshold]
        if len(tail) > 0:
            return max(-float(tail.mean()), 0.0)
        # Fallback: if no observations in tail, use parametric VaR
        return self._parametric_var(returns, confidence, position_value)

    def _portfolio_cvar(
        self,
        returns_matrix: np.ndarray,
        pos_values: np.ndarray,
        confidence: float,
    ) -> float:
        """Portfolio-level CVaR via historical simulation.

        Computes daily portfolio P&L, then average of tail losses.
        """
        # Daily portfolio P&L: sum of (return_i * value_i) for each day
        daily_pnl = (returns_matrix * pos_values[:, np.newaxis]).sum(axis=0)
        if len(daily_pnl) < 2:
            return 0.0
        var_threshold = np.percentile(daily_pnl, (1 - confidence) * 100)
        tail = daily_pnl[daily_pnl <= var_threshold]
        if len(tail) > 0:
            return round(max(-float(tail.mean()), 0.0), 2)
        return 0.0

    # ------------------------------------------------------------------
    # Private: correlation matrices
    # ------------------------------------------------------------------

    def _build_correlation_matrix(
        self,
        returns_dict: dict,
        symbols: list,
    ) -> np.ndarray:
        """Build correlation matrix from historical returns.

        Returns:
            np.ndarray of shape (n, n) with correlations.
        """
        n = len(symbols)
        if n == 0:
            return np.array([[]])
        if n == 1:
            return np.array([[1.0]])

        min_len = min(len(returns_dict[s]) for s in symbols)
        matrix = np.array(
            [np.asarray(returns_dict[s], dtype=float)[-min_len:] for s in symbols]
        )
        corr = np.corrcoef(matrix)
        # Ensure diagonal is exactly 1.0
        np.fill_diagonal(corr, 1.0)
        return corr

    def _build_stress_correlation_matrix(self, symbols: list) -> np.ndarray:
        """Build stress correlation matrix using March 2020 estimates.

        Uses ASSET_CLASS_MAP to determine each symbol's class, then
        looks up pairwise stress correlations from STRESS_CORRELATIONS.

        Falls back to 0.80 if asset class pair not found (conservative).
        """
        n = len(symbols)
        if n == 0:
            return np.array([[]])
        if n == 1:
            return np.array([[1.0]])

        stress_corr = np.eye(n)
        for i in range(n):
            cls_i = ASSET_CLASS_MAP.get(symbols[i], "equity")
            for j in range(i + 1, n):
                cls_j = ASSET_CLASS_MAP.get(symbols[j], "equity")
                # Look up in both orders
                corr_val = STRESS_CORRELATIONS.get(
                    (cls_i, cls_j),
                    STRESS_CORRELATIONS.get((cls_j, cls_i), 0.80),
                )
                stress_corr[i, j] = corr_val
                stress_corr[j, i] = corr_val

        return stress_corr

    # ------------------------------------------------------------------
    # Private: position value calculation
    # ------------------------------------------------------------------

    def _position_value(
        self,
        symbol: str,
        quantity: float,
        current_price: float,
        instrument_type: str,
    ) -> float:
        """Compute notional position value in USD.

        EQUITY:  quantity * price
        FUTURES: quantity * price * multiplier
        FX:      quantity * lot_size * price
        """
        itype = instrument_type.upper()
        if itype == "FUTURES":
            multiplier = FUTURES_MULTIPLIERS.get(symbol, 1.0)
            return abs(quantity) * current_price * multiplier
        elif itype == "FX":
            return abs(quantity) * FX_LOT_SIZE * current_price
        else:
            # EQUITY (default)
            return abs(quantity) * current_price

    # ------------------------------------------------------------------
    # Private: empty result helper
    # ------------------------------------------------------------------

    def _empty_portfolio_result(self) -> dict:
        """Return empty portfolio VaR result."""
        return {
            "portfolio_var_95": 0.0,
            "portfolio_var_99": 0.0,
            "portfolio_cvar_95": 0.0,
            "diversification_benefit": 1.0,
            "correlation_matrix": {},
            "per_position_var": [],
            "var_pct_of_capital": 0.0,
            "stressed_var_95": 0.0,
            "stressed_var_99": 0.0,
            "n_positions": 0,
        }
