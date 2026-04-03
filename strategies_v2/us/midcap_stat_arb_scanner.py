"""
MidCap Statistical Arbitrage — Pair Scanner

Scans the S&P 400 MidCap universe for cointegrated pairs within GICS industry groups.
Produces a ranked list of tradeable pairs with quality metrics.

Usage:
    scanner = PairScanner(data_provider=alpaca_data)
    pairs = scanner.scan(formation_days=120, max_pairs=20)

References:
    - Gatev, Goetzmann & Rouwenhorst (2006) "Pairs Trading"
    - Engle & Granger (1987) "Co-integration and Error Correction"
    - Vidyamurthy (2004) "Pairs Trading: Quantitative Methods and Analysis"
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import logging
import itertools

logger = logging.getLogger("stat_arb.scanner")


# ============================================================
# GICS Industry Groups for MidCap pair clustering
# ============================================================

GICS_INDUSTRY_GROUPS: Dict[str, List[str]] = {
    # Technology
    "software": [
        "CDNS", "SNPS", "MANH", "PAYC", "PCTY", "GWRE", "ALTR", "BILL",
        "TOST", "BRZE", "ZS", "CRWD", "NET", "DDOG", "MDB",
    ],
    "semiconductors": [
        "SWKS", "QRVO", "MKSI", "ENTG", "LSCC", "RMBS", "CRUS",
        "SMTC", "DIOD", "POWI", "SLAB",
    ],
    "it_hardware_networking": [
        "FFIV", "JNPR", "CIEN", "CALX", "VIAV", "LITE", "COMM",
    ],

    # Healthcare
    "healthcare_equipment": [
        "HOLX", "NVCR", "NVST", "MMSI", "NEOG", "LIVN", "SILK",
        "GMED", "PODD", "RGEN",
    ],
    "pharma_biotech_mid": [
        "MEDP", "ICLR", "CRL", "BIO", "TECH", "AZTA", "BRKR",
    ],
    "healthcare_services": [
        "ACHC", "AMN", "EHC", "SEM", "SGRY", "ENSG",
    ],

    # Industrials
    "commercial_services": [
        "ROL", "CTAS", "ARMK", "ABM", "BCO", "TTC", "SCI",
    ],
    "industrial_machinery": [
        "MIDD", "GNRC", "RBC", "ATKR", "WTS", "BMI", "CXT",
    ],
    "building_products": [
        "TREX", "AZEK", "DOOR", "SSD", "SITM", "AWI", "FND",
    ],
    "transportation": [
        "SAIA", "ODFL", "XPO", "ARCB", "WERN", "SNDR", "KNX",
    ],

    # Financials
    "regional_banks": [
        "EWBC", "FNB", "SNV", "CBSH", "UMBF", "BOKF", "GBCI",
        "PNFP", "SFNC", "IBOC", "TRMK",
    ],
    "insurance": [
        "RLI", "KMPR", "SIGI", "THG", "PLMR", "HCI", "KNSL",
    ],
    "capital_markets": [
        "CBOE", "NDAQ", "MKTX", "VIRT", "PIPR", "EVR", "HLI",
    ],

    # Consumer Discretionary
    "restaurants": [
        "DPZ", "WING", "JACK", "TXRH", "CAKE", "SHAK", "DINE",
    ],
    "specialty_retail": [
        "FIVE", "OLLI", "DKS", "ASO", "BOOT", "SIG", "AEO",
    ],
    "homebuilders": [
        "MDC", "TMHC", "KBH", "MHO", "GRBK", "CCS", "SKY",
    ],

    # Consumer Staples
    "food_beverage": [
        "POST", "LNTH", "JJSF", "THS", "BGS", "SMPL", "FRPT",
    ],

    # Materials
    "chemicals": [
        "OLN", "KWR", "AXTA", "GCP", "HUN", "IOSP", "NGVT",
    ],
    "metals_mining": [
        "FCX", "SCCO", "CMP", "CEIX", "ARCH", "HCC", "AMR",
    ],

    # Energy
    "oil_gas_midstream": [
        "VLO", "MPC", "DK", "DINO", "CVI", "PBF", "HFC",
    ],
    "energy_equipment": [
        "WHD", "PUMP", "LBRT", "HP", "PTEN", "RES", "OII",
    ],

    # Real Estate
    "reits_specialty": [
        "COLD", "QTS", "IIPR", "REXR", "STAG", "EGP", "FR",
    ],

    # Utilities
    "utilities_electric": [
        "NRG", "VST", "OGE", "PNW", "IDA", "AVA", "MDU",
    ],
}

# Flatten for quick lookup
TICKER_TO_GROUP: Dict[str, str] = {}
for group, tickers in GICS_INDUSTRY_GROUPS.items():
    for ticker in tickers:
        TICKER_TO_GROUP[ticker] = group

ALL_TICKERS = list(TICKER_TO_GROUP.keys())


# ============================================================
# Data Types
# ============================================================

@dataclass
class PairCandidate:
    """A candidate pair with cointegration metrics."""
    ticker_a: str
    ticker_b: str
    industry_group: str
    adf_pvalue: float               # Augmented Dickey-Fuller p-value
    half_life_days: float            # Mean-reversion half-life
    spread_sharpe: float             # Sharpe of spread trading (historical)
    correlation: float               # Return correlation
    cointegration_coeff: float       # Hedge ratio (gamma)
    avg_daily_volume_a: float        # Average daily $ volume ticker A
    avg_daily_volume_b: float        # Average daily $ volume ticker B
    formation_start: datetime = None
    formation_end: datetime = None
    z_score_current: float = 0.0     # Current z-score of the spread
    hurst_exponent: float = 0.5      # Hurst exponent (< 0.5 = mean reverting)

    @property
    def pair_id(self) -> str:
        return f"{self.ticker_a}_{self.ticker_b}"

    @property
    def quality_score(self) -> float:
        """Composite quality score (0-10)."""
        score = 0.0
        # ADF significance (0-3)
        if self.adf_pvalue < 0.01:
            score += 3.0
        elif self.adf_pvalue < 0.03:
            score += 2.0
        elif self.adf_pvalue < 0.05:
            score += 1.0
        # Half-life (0-2) — prefer 3-15 days
        if 3 <= self.half_life_days <= 15:
            score += 2.0
        elif 1 <= self.half_life_days <= 30:
            score += 1.0
        # Spread Sharpe (0-2)
        if self.spread_sharpe > 2.0:
            score += 2.0
        elif self.spread_sharpe > 1.0:
            score += 1.5
        elif self.spread_sharpe > 0.5:
            score += 0.5
        # Hurst exponent (0-2) — lower = more mean reverting
        if self.hurst_exponent < 0.35:
            score += 2.0
        elif self.hurst_exponent < 0.45:
            score += 1.0
        # Liquidity bonus (0-1)
        min_volume = min(self.avg_daily_volume_a, self.avg_daily_volume_b)
        if min_volume > 20_000_000:
            score += 1.0
        elif min_volume > 10_000_000:
            score += 0.5
        return score

    @property
    def is_tradeable(self) -> bool:
        """Minimum criteria for trading."""
        return (
            self.adf_pvalue < 0.05
            and self.half_life_days < 30
            and self.half_life_days > 1
            and self.spread_sharpe > 0.3
            and min(self.avg_daily_volume_a, self.avg_daily_volume_b) > 5_000_000
            # Hurst is informational, not a gate — R/S estimator biased on log spreads
        )


# ============================================================
# Statistical Functions
# ============================================================

def engle_granger_cointegration(
    prices_a: pd.Series,
    prices_b: pd.Series,
) -> Tuple[float, float, pd.Series]:
    """
    Engle-Granger two-step cointegration test.

    Step 1: OLS regression log(A) = gamma * log(B) + epsilon
    Step 2: ADF test on residuals (epsilon)

    Returns:
        adf_pvalue: p-value of ADF test on residuals
        gamma: cointegration coefficient (hedge ratio)
        spread: residual series (the spread to trade)
    """
    from statsmodels.tsa.stattools import adfuller

    log_a = np.log(prices_a)
    log_b = np.log(prices_b)

    # OLS: log(A) = alpha + gamma * log(B) + epsilon
    X = np.column_stack([np.ones(len(log_b)), log_b.values])
    y = log_a.values
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    gamma = beta[1]
    alpha = beta[0]

    # Residuals = spread
    spread = log_a - (alpha + gamma * log_b)

    # ADF test on spread
    try:
        adf_result = adfuller(spread.dropna(), maxlag=10, autolag="AIC")
        adf_pvalue = adf_result[1]
    except Exception:
        adf_pvalue = 1.0

    return adf_pvalue, gamma, spread


def calculate_half_life(spread: pd.Series) -> float:
    """
    Calculate the half-life of mean reversion using an AR(1) model.

    spread_t = phi * spread_{t-1} + epsilon
    half_life = -log(2) / log(phi)
    """
    spread_clean = spread.dropna()
    if len(spread_clean) < 20:
        return float("inf")

    y = spread_clean.values[1:]
    x = spread_clean.values[:-1]

    X = np.column_stack([np.ones(len(x)), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    phi = beta[1]

    if phi >= 1.0 or phi <= 0.0:
        return float("inf")

    half_life = -np.log(2) / np.log(phi)
    return max(0.1, half_life)


def calculate_hurst_exponent(series: pd.Series, max_lag: int = 40) -> float:
    """
    Hurst exponent via rescaled range (R/S) analysis.

    H < 0.5 : mean-reverting
    H = 0.5 : random walk
    H > 0.5 : trending
    """
    series_clean = series.dropna().values
    n = len(series_clean)
    if n < max_lag * 2:
        return 0.5

    lags = range(2, min(max_lag, n // 4))
    tau = []
    rs = []

    for lag in lags:
        # Split into sub-series
        sub_series = np.array_split(series_clean[:n - n % lag], n // lag)
        rs_values = []
        for sub in sub_series:
            if len(sub) < 2:
                continue
            mean = np.mean(sub)
            cumdev = np.cumsum(sub - mean)
            r = np.max(cumdev) - np.min(cumdev)
            s = np.std(sub, ddof=1)
            if s > 0:
                rs_values.append(r / s)
        if rs_values:
            tau.append(lag)
            rs.append(np.mean(rs_values))

    if len(tau) < 3:
        return 0.5

    # Linear regression of log(R/S) on log(lag)
    log_tau = np.log(tau)
    log_rs = np.log(rs)
    poly = np.polyfit(log_tau, log_rs, 1)
    hurst = poly[0]

    return np.clip(hurst, 0.0, 1.0)


def calculate_spread_sharpe(
    spread: pd.Series,
    z_entry: float = 2.0,
    z_exit: float = 0.5,
) -> float:
    """
    Calculate the historical Sharpe ratio of trading the spread.

    Simple backtest: enter at z > entry, exit at z < exit.
    """
    spread_clean = spread.dropna()
    if len(spread_clean) < 60:
        return 0.0

    mu = spread_clean.mean()
    sigma = spread_clean.std()
    if sigma == 0:
        return 0.0

    z = (spread_clean - mu) / sigma

    # Simple PnL simulation
    position = 0  # 1 = long spread, -1 = short spread, 0 = flat
    pnl_daily = []
    spread_diff = spread_clean.diff()

    for i in range(1, len(z)):
        # PnL from holding
        if position != 0:
            pnl_daily.append(position * spread_diff.iloc[i])
        else:
            pnl_daily.append(0.0)

        # Signal
        if position == 0:
            if z.iloc[i] > z_entry:
                position = -1  # short spread
            elif z.iloc[i] < -z_entry:
                position = 1   # long spread
        elif position == 1:
            if z.iloc[i] > -z_exit:
                position = 0
        elif position == -1:
            if z.iloc[i] < z_exit:
                position = 0

    if not pnl_daily or np.std(pnl_daily) == 0:
        return 0.0

    return np.mean(pnl_daily) / np.std(pnl_daily) * np.sqrt(252)


# ============================================================
# Pair Scanner
# ============================================================

class PairScanner:
    """
    Scans for cointegrated pairs within GICS industry groups.

    Usage:
        scanner = PairScanner()
        pairs = scanner.scan(prices_dict, formation_days=120)
    """

    def __init__(
        self,
        z_entry: float = 2.0,
        z_exit: float = 0.5,
        adf_pvalue_max: float = 0.05,
        half_life_max: float = 30.0,
        min_volume_usd: float = 5_000_000,
        max_pairs: int = 20,
    ):
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.adf_pvalue_max = adf_pvalue_max
        self.half_life_max = half_life_max
        self.min_volume_usd = min_volume_usd
        self.max_pairs = max_pairs

    def scan(
        self,
        prices: Dict[str, pd.DataFrame],
        formation_days: int = 120,
        volumes: Optional[Dict[str, pd.Series]] = None,
    ) -> List[PairCandidate]:
        """
        Scan for cointegrated pairs.

        Args:
            prices: Dict of ticker -> DataFrame with 'close' column
            formation_days: Lookback period for cointegration testing
            volumes: Optional dict of ticker -> daily dollar volume Series

        Returns:
            List of PairCandidate, sorted by quality_score descending
        """
        all_candidates = []

        for group_name, tickers in GICS_INDUSTRY_GROUPS.items():
            # Filter to tickers we have data for
            available = [t for t in tickers if t in prices]
            if len(available) < 2:
                continue

            logger.info(f"Scanning group '{group_name}': {len(available)} tickers, "
                        f"{len(available) * (len(available) - 1) // 2} pairs")

            # Test all pairs within the group
            for ticker_a, ticker_b in itertools.combinations(available, 2):
                candidate = self._test_pair(
                    ticker_a, ticker_b, group_name,
                    prices[ticker_a], prices[ticker_b],
                    formation_days, volumes,
                )
                if candidate and candidate.is_tradeable:
                    all_candidates.append(candidate)

        # Sort by quality score
        all_candidates.sort(key=lambda c: c.quality_score, reverse=True)

        # Take top N
        selected = all_candidates[:self.max_pairs]

        logger.info(f"Scan complete: {len(all_candidates)} tradeable pairs found, "
                     f"selected top {len(selected)}")

        return selected

    def _test_pair(
        self,
        ticker_a: str,
        ticker_b: str,
        group: str,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        formation_days: int,
        volumes: Optional[Dict[str, pd.Series]],
    ) -> Optional[PairCandidate]:
        """Test a single pair for cointegration."""
        try:
            # Align data
            close_a = df_a["close"].iloc[-formation_days:]
            close_b = df_b["close"].iloc[-formation_days:]

            # Ensure same dates
            common_idx = close_a.index.intersection(close_b.index)
            if len(common_idx) < formation_days * 0.8:
                return None

            close_a = close_a.loc[common_idx]
            close_b = close_b.loc[common_idx]

            # Cointegration test
            adf_pvalue, gamma, spread = engle_granger_cointegration(close_a, close_b)

            if adf_pvalue > self.adf_pvalue_max:
                return None

            # Half-life
            hl = calculate_half_life(spread)
            if hl > self.half_life_max or hl < 1:
                return None

            # Hurst exponent
            hurst = calculate_hurst_exponent(spread)

            # Spread Sharpe
            sharpe = calculate_spread_sharpe(spread, self.z_entry, self.z_exit)

            # Correlation
            ret_a = close_a.pct_change().dropna()
            ret_b = close_b.pct_change().dropna()
            common_ret_idx = ret_a.index.intersection(ret_b.index)
            corr = ret_a.loc[common_ret_idx].corr(ret_b.loc[common_ret_idx])

            # Volume
            avg_vol_a = 0.0
            avg_vol_b = 0.0
            if volumes:
                if ticker_a in volumes:
                    avg_vol_a = volumes[ticker_a].iloc[-20:].mean()
                if ticker_b in volumes:
                    avg_vol_b = volumes[ticker_b].iloc[-20:].mean()

            # Current z-score
            mu = spread.mean()
            sigma = spread.std()
            z_current = (spread.iloc[-1] - mu) / sigma if sigma > 0 else 0

            return PairCandidate(
                ticker_a=ticker_a,
                ticker_b=ticker_b,
                industry_group=group,
                adf_pvalue=adf_pvalue,
                half_life_days=hl,
                spread_sharpe=sharpe,
                correlation=corr,
                cointegration_coeff=gamma,
                avg_daily_volume_a=avg_vol_a,
                avg_daily_volume_b=avg_vol_b,
                formation_start=common_idx[0] if hasattr(common_idx[0], "isoformat") else None,
                formation_end=common_idx[-1] if hasattr(common_idx[-1], "isoformat") else None,
                z_score_current=z_current,
                hurst_exponent=hurst,
            )

        except Exception as e:
            logger.debug(f"Error testing pair {ticker_a}/{ticker_b}: {e}")
            return None

    def get_current_signals(
        self,
        active_pairs: List[PairCandidate],
        prices: Dict[str, pd.DataFrame],
        lookback_days: int = 120,
    ) -> List[Dict]:
        """
        Generate current trading signals for active pairs.

        Returns list of signal dicts with action (LONG/SHORT/CLOSE/HOLD).
        """
        signals = []

        for pair in active_pairs:
            try:
                close_a = prices[pair.ticker_a]["close"].iloc[-lookback_days:]
                close_b = prices[pair.ticker_b]["close"].iloc[-lookback_days:]

                common_idx = close_a.index.intersection(close_b.index)
                close_a = close_a.loc[common_idx]
                close_b = close_b.loc[common_idx]

                # Recalculate spread with stored gamma
                log_a = np.log(close_a)
                log_b = np.log(close_b)
                spread = log_a - pair.cointegration_coeff * log_b

                mu = spread.mean()
                sigma = spread.std()
                if sigma == 0:
                    continue

                z = (spread.iloc[-1] - mu) / sigma

                # Determine action
                action = "HOLD"
                if z > self.z_entry:
                    action = "SHORT_SPREAD"   # short A, long B
                elif z < -self.z_entry:
                    action = "LONG_SPREAD"    # long A, short B
                elif abs(z) < self.z_exit:
                    action = "CLOSE"
                elif abs(z) > 4.0:
                    action = "STOP_LOSS"

                signals.append({
                    "pair_id": pair.pair_id,
                    "ticker_a": pair.ticker_a,
                    "ticker_b": pair.ticker_b,
                    "z_score": round(z, 4),
                    "action": action,
                    "gamma": round(pair.cointegration_coeff, 6),
                    "half_life": round(pair.half_life_days, 1),
                    "quality_score": round(pair.quality_score, 2),
                    "spread_sharpe": round(pair.spread_sharpe, 2),
                    "timestamp": datetime.now().isoformat(),
                })

            except Exception as e:
                logger.warning(f"Error generating signal for {pair.pair_id}: {e}")

        return signals


# ============================================================
# Convenience: Print scan results
# ============================================================

def print_scan_results(pairs: List[PairCandidate]) -> None:
    """Pretty-print scan results."""
    print(f"\n{'='*90}")
    print(f"PAIR SCANNER RESULTS — {len(pairs)} tradeable pairs")
    print(f"{'='*90}")
    print(f"{'Rank':<5} {'Pair':<20} {'Group':<20} {'ADF p':<8} {'HL':<6} "
          f"{'Sharpe':<8} {'Hurst':<7} {'Z now':<7} {'Score':<6}")
    print(f"{'-'*90}")

    for i, p in enumerate(pairs, 1):
        print(f"{i:<5} {p.pair_id:<20} {p.industry_group:<20} "
              f"{p.adf_pvalue:<8.4f} {p.half_life_days:<6.1f} "
              f"{p.spread_sharpe:<8.2f} {p.hurst_exponent:<7.3f} "
              f"{p.z_score_current:<7.2f} {p.quality_score:<6.1f}")

    print(f"{'='*90}\n")
