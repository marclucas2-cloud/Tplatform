"""Relative Strength Filter — only buy leaders, only short laggards.

Compares stock returns vs benchmark over 20-day rolling window.
Rejects buy signals on underperformers, rejects short signals on outperformers.

Usage:
    from core.risk.relative_strength import RelativeStrengthFilter
    rsf = RelativeStrengthFilter()
    ok, reason = rsf.should_allow_buy("ASML", alpha_score=0.02)
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Sector benchmark mapping
BENCHMARK_MAP = {
    # US broad
    "SPY": "SPY", "QQQ": "SPY", "IWM": "SPY",
    # US tech
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "GOOGL": "XLK",
    "META": "XLK", "AMZN": "XLY", "TSLA": "XLY", "NFLX": "XLC",
    # US finance
    "JPM": "XLF", "GS": "XLF", "BAC": "XLF", "MS": "XLF",
    # EU
    "ASML": "VGK", "MC.PA": "VGK", "SAP": "VGK", "LVMH": "VGK",
    "BNP.PA": "VGK", "DBK.DE": "VGK", "ING.AS": "VGK",
    "BMW.DE": "VGK", "CON.DE": "VGK", "SIE.DE": "VGK",
    "TTE.PA": "VGK", "BP.L": "VGK", "SHEL.L": "VGK",
    # Japan
    "7203.T": "EWJ", "7267.T": "EWJ", "8058.T": "EWJ",
    "8053.T": "EWJ", "6758.T": "EWJ", "6752.T": "EWJ",
}

# Default benchmark by market
DEFAULT_BENCHMARK = {
    "us": "SPY",
    "eu": "VGK",
    "jp": "EWJ",
    "uk": "VGK",
}


class RelativeStrengthFilter:
    """Filter that only allows buying leaders and shorting laggards."""

    def __init__(
        self,
        lookback_days: int = 20,
        min_outperformance: float = 0.0,
        short_lookback_days: int = 5,
    ):
        self.lookback_days = lookback_days
        self.min_outperformance = min_outperformance
        self.short_lookback_days = short_lookback_days

    def compute_alpha_score(
        self, stock_returns_20d: float, index_returns_20d: float
    ) -> float:
        """Alpha = stock return - index return over N days."""
        return stock_returns_20d - index_returns_20d

    def should_allow_buy(
        self, ticker: str, alpha_score: float
    ) -> tuple[bool, str]:
        """Only allow buy if stock outperforms its benchmark.

        Args:
            ticker: stock symbol
            alpha_score: stock_return - benchmark_return over lookback

        Returns:
            (allowed, reason)
        """
        if alpha_score > self.min_outperformance:
            return True, f"OK — {ticker} alpha={alpha_score:+.4f} > {self.min_outperformance}"
        return False, (
            f"BLOCKED — {ticker} underperforms benchmark "
            f"(alpha={alpha_score:+.4f} <= {self.min_outperformance})"
        )

    def should_allow_short(
        self, ticker: str, alpha_score: float
    ) -> tuple[bool, str]:
        """Only allow short if stock underperforms its benchmark."""
        if alpha_score < -self.min_outperformance:
            return True, f"OK — {ticker} alpha={alpha_score:+.4f}, laggard confirmed"
        return False, (
            f"BLOCKED — {ticker} outperforming benchmark "
            f"(alpha={alpha_score:+.4f}), not a laggard"
        )

    def detect_momentum_divergence(
        self,
        stock_returns: pd.Series,
        index_returns: pd.Series,
        window: int = 5,
    ) -> dict:
        """Detect fake-outs: index moves but stock doesn't follow.

        Returns:
            {divergent, stock_momentum, index_momentum, type}
        """
        if len(stock_returns) < window or len(index_returns) < window:
            return {
                "divergent": False,
                "stock_momentum": 0.0,
                "index_momentum": 0.0,
                "type": "INSUFFICIENT_DATA",
            }

        stock_mom = float(stock_returns.iloc[-window:].sum())
        index_mom = float(index_returns.iloc[-window:].sum())

        # Bearish divergence: index up, stock flat/down
        if index_mom > 0.01 and stock_mom < 0.005:
            return {
                "divergent": True,
                "stock_momentum": round(stock_mom, 6),
                "index_momentum": round(index_mom, 6),
                "type": "BEARISH_DIVERGENCE",
            }

        # Bullish divergence: index down, stock flat/up
        if index_mom < -0.01 and stock_mom > -0.005:
            return {
                "divergent": True,
                "stock_momentum": round(stock_mom, 6),
                "index_momentum": round(index_mom, 6),
                "type": "BULLISH_DIVERGENCE",
            }

        return {
            "divergent": False,
            "stock_momentum": round(stock_mom, 6),
            "index_momentum": round(index_mom, 6),
            "type": "NONE",
        }

    def get_sector_benchmark(self, ticker: str) -> str:
        """Map a ticker to its sector benchmark ETF."""
        if ticker in BENCHMARK_MAP:
            return BENCHMARK_MAP[ticker]
        # Heuristic by suffix
        if ticker.endswith(".T"):
            return DEFAULT_BENCHMARK["jp"]
        if ticker.endswith((".PA", ".DE", ".AS", ".L")):
            return DEFAULT_BENCHMARK["eu"]
        return DEFAULT_BENCHMARK["us"]
