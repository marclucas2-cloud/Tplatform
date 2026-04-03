"""
Pairs Trading — découverte et analyse de paires coïntégrées.

Logique :
  1. Pour chaque secteur, aligner les prix daily des actifs
  2. Tester chaque paire : hedge ratio OLS, ADF sur spread, half-life OU
  3. Retourner les paires classées par qualité (ADF p-value croissant)

ADF simplifié (lag=1, sans statsmodels) :
  Régression : ΔS[t] = γ * S[t-1] + α + ε
  H0 : γ = 0 (racine unitaire)
  H1 : γ < 0 (mean-reverting)
  p-value via scipy.stats.t (approximation suffisante pour screening)

Usage :
    from core.data.pairs import PairDiscovery, SECTOR_MAP
    discovery = PairDiscovery()
    pairs = discovery.find_pairs("tech_us", ohlcv_dict)
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

# ─── Mapping secteurs → symboles ─────────────────────────────────────────────

SECTOR_MAP: dict[str, list[str]] = {
    # S&P 500 Information Technology (GICS 45) + Communication Services tech
    # ~45 valeurs → C(45,2) = 990 paires possibles
    "tech_us": [
        # ── Mega cap ──────────────────────────────────────────────────────
        "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA",
        # ── Semiconducteurs ───────────────────────────────────────────────
        "AVGO", "AMD", "QCOM", "TXN", "INTC", "MU", "AMAT",
        "LRCX", "KLAC", "MCHP", "ADI", "ON", "MPWR", "TER",
        # ── Software / Cloud / SaaS ───────────────────────────────────────
        "ORCL", "CRM", "NOW", "ADBE", "SNPS", "CDNS",
        "FTNT", "PANW", "ANSS", "PTC", "PAYC",
        # ── Internet / Plateforme ─────────────────────────────────────────
        "NFLX", "UBER", "ABNB", "RBLX", "SPOT",
        # ── Hardware / Réseau ─────────────────────────────────────────────
        "CSCO", "ANET", "IBM", "HPE", "HPQ", "CDW", "WDC", "STX",
        # ── IT Services ───────────────────────────────────────────────────
        "ACN", "CTSH",
    ],
    # S&P 500 Financials (GICS 40)
    "finance_us": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW",
        "AXP", "USB", "PNC", "TFC", "COF", "BRKB", "CB", "MET",
        "PRU", "AFL", "ALL", "ICE", "CME", "SPGI", "MCO",
    ],
    # Europe — indices + blue chips cotés aux US (ADR ou ETF proxies)
    "europe": [
        "ASML", "SAP", "LVMH", "TTE", "SHEL", "NVO", "AZN",
        "HSBC", "UL", "BP", "RIO", "DEO", "PHG",
    ],
    # Crypto — top 8 par market cap (via yfinance tickers -USD)
    "crypto": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK"],
}


@dataclass
class PairStats:
    """Statistiques d'une paire d'actifs pour la coïntégration."""
    symbol_a: str
    symbol_b: str
    sector: str
    hedge_ratio: float        # β via OLS : log(A) = α + β·log(B)
    ols_alpha: float          # intercept OLS
    correlation: float        # corrélation des rendements log
    adf_stat: float           # statistique ADF (neg = plus stationnaire)
    adf_pvalue: float         # p-value ADF (< 0.05 = coïntégré)
    half_life_days: float     # half-life mean-reversion (jours)
    spread_mean: float        # moyenne historique du spread
    spread_std: float         # std historique du spread
    is_cointegrated: bool     # adf_pvalue < seuil
    n_obs: int                # barres utilisées pour l'estimation

    def __str__(self) -> str:
        flag = "✅" if self.is_cointegrated else "⚠️ "
        return (f"{flag} {self.symbol_a}/{self.symbol_b}"
                f" β={self.hedge_ratio:.3f}"
                f" corr={self.correlation:+.3f}"
                f" ADF_p={self.adf_pvalue:.3f}"
                f" HL={self.half_life_days:.1f}j"
                f" N={self.n_obs}")


# ─── Fonctions mathématiques ──────────────────────────────────────────────────

def compute_hedge_ratio(log_a: np.ndarray, log_b: np.ndarray) -> tuple[float, float]:
    """
    OLS : log(A) = α + β·log(B) + ε
    Retourne (β, α).
    """
    n = len(log_a)
    if n < 30:
        return 1.0, 0.0
    X = np.column_stack([log_b, np.ones(n)])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, log_a, rcond=None)
        return float(coeffs[0]), float(coeffs[1])
    except Exception:
        return 1.0, 0.0


def compute_spread(
    log_a: np.ndarray,
    log_b: np.ndarray,
    hedge_ratio: float,
    ols_alpha: float,
) -> np.ndarray:
    """Spread = log(A) - β·log(B) - α (résidu OLS, centré sur 0)."""
    return log_a - hedge_ratio * log_b - ols_alpha


def compute_halflife(spread: np.ndarray) -> float:
    """
    Half-life du processus OU estimé par régression AR(1) :
      ΔS[t] = γ · S[t-1] + α + ε
    Half-life = -log(2) / γ  (γ < 0 pour mean-reversion)
    Retourne inf si pas de mean-reversion.
    """
    if len(spread) < 10:
        return np.inf
    delta = np.diff(spread)
    lagged = spread[:-1]
    X = np.column_stack([lagged, np.ones(len(lagged))])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, delta, rcond=None)
        gamma = float(coeffs[0])
        if gamma >= 0:
            return np.inf
        return float(-np.log(2) / gamma)
    except Exception:
        return np.inf


def adf_test(series: np.ndarray) -> tuple[float, float]:
    """
    ADF simplifié lag=1 sans statsmodels.
    Régression : ΔS[t] = γ · S[t-1] + α + ε
    H0 : γ = 0 (racine unitaire)
    H1 : γ < 0 (stationnaire, mean-reverting)

    Retourne (t_stat, p_value).
    p_value approchée via scipy.stats.t (distribution t de Student).
    Limitation : cette approximation est valide pour screening, pas pour publication.
    """
    from scipy.stats import t as t_dist

    n = len(series)
    if n < 15:
        return 0.0, 1.0

    delta = np.diff(series)
    lagged = series[:-1]
    X = np.column_stack([lagged, np.ones(n - 1)])

    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, delta, rcond=None)
        gamma = float(coeffs[0])

        y_hat = X @ coeffs
        resid = delta - y_hat
        df = n - 3  # n-1 obs, 2 paramètres
        if df < 1:
            return 0.0, 1.0

        sigma2 = float(np.sum(resid**2) / df)
        try:
            XtX_inv = np.linalg.inv(X.T @ X)
        except np.linalg.LinAlgError:
            return 0.0, 1.0

        se_gamma = float(np.sqrt(sigma2 * XtX_inv[0, 0]))
        if se_gamma <= 0:
            return 0.0, 1.0

        t_stat = gamma / se_gamma
        # Test unilatéral gauche (H1 : γ < 0)
        p_value = float(t_dist.cdf(t_stat, df=df))
        return t_stat, p_value

    except Exception:
        return 0.0, 1.0


# ─── Classe principale ────────────────────────────────────────────────────────

class PairDiscovery:
    """Découverte et analyse des paires coïntégrées dans un secteur."""

    def __init__(
        self,
        adf_pvalue_threshold: float = 0.05,
        min_correlation: float = 0.50,
        min_halflife_days: float = 5.0,
        max_halflife_days: float = 120.0,
    ):
        """
        adf_pvalue_threshold : seuil pour considérer une paire coïntégrée
        min_correlation      : corrélation minimale des rendements (filtre qualité)
        min_halflife_days    : demi-vie minimale (évite les paires trop instables)
        max_halflife_days    : demi-vie maximale (évite les paires trop lentes à converger)
        """
        self.adf_pvalue_threshold = adf_pvalue_threshold
        self.min_correlation = min_correlation
        self.min_halflife_days = min_halflife_days
        self.max_halflife_days = max_halflife_days

    def analyze_pair(
        self,
        data_a,  # OHLCVData
        data_b,  # OHLCVData
        symbol_a: str,
        symbol_b: str,
        sector: str,
    ) -> PairStats | None:
        """
        Analyse complète d'une paire : hedge ratio, spread, ADF, half-life.
        Retourne None si les données sont insuffisantes.
        """
        try:
            # Aligner sur dates communes (inner join)
            close_a = data_a.df["close"].rename("a")
            close_b = data_b.df["close"].rename("b")
            df = pd.concat([close_a, close_b], axis=1).dropna()

            if len(df) < 60:
                return None

            log_a = np.log(df["a"].values)
            log_b = np.log(df["b"].values)

            # Hedge ratio OLS
            beta, alpha = compute_hedge_ratio(log_a, log_b)

            # Spread résiduel
            spread = compute_spread(log_a, log_b, beta, alpha)

            # Half-life mean reversion
            hl = compute_halflife(spread)

            # ADF test
            t_stat, p_val = adf_test(spread)

            # Corrélation sur rendements log
            ret_a = np.diff(log_a)
            ret_b = np.diff(log_b)
            if ret_b.std() > 0 and ret_a.std() > 0:
                corr = float(np.corrcoef(ret_a, ret_b)[0, 1])
            else:
                corr = 0.0

            return PairStats(
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                sector=sector,
                hedge_ratio=round(beta, 4),
                ols_alpha=round(alpha, 4),
                correlation=round(corr, 4),
                adf_stat=round(t_stat, 4),
                adf_pvalue=round(p_val, 4),
                half_life_days=round(hl, 1),
                spread_mean=round(float(spread.mean()), 6),
                spread_std=round(float(spread.std()), 6),
                is_cointegrated=(p_val < self.adf_pvalue_threshold),
                n_obs=len(df),
            )
        except Exception:
            return None

    def find_pairs(
        self,
        sector: str,
        ohlcv_dict: dict,  # {symbol: OHLCVData}
    ) -> list[PairStats]:
        """
        Teste toutes les combinaisons C(n,2) du secteur.
        Retourne les paires filtrées triées par ADF p-value croissant.
        """
        symbols = [s for s in SECTOR_MAP.get(sector, []) if s in ohlcv_dict]
        pairs = []

        for sym_a, sym_b in combinations(symbols, 2):
            ps = self.analyze_pair(
                ohlcv_dict[sym_a], ohlcv_dict[sym_b], sym_a, sym_b, sector
            )
            if ps is None:
                continue
            if abs(ps.correlation) < self.min_correlation:
                continue
            if ps.half_life_days > self.max_halflife_days:
                continue
            if ps.half_life_days < self.min_halflife_days:
                continue
            pairs.append(ps)

        pairs.sort(key=lambda p: p.adf_pvalue)
        return pairs
