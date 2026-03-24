"""
Stratégie 4 : Correlation Breakdown (Pairs Trading)
Quand deux actifs normalement corrélés divergent, ils reconvergent.

Règles :
- Calcule le z-score du spread normalisé entre paires corrélées
- Entrée quand z-score > 2.0 : LONG le sous-performant, SHORT le surperformant
- Exit quand z-score revient à 0.5
- Stop-loss : z-score atteint 3.0
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


class CorrelationBreakdownStrategy(BaseStrategy):
    name = "Correlation Breakdown"

    def __init__(self, entry_zscore: float = 2.0, exit_zscore: float = 0.5,
                 stop_zscore: float = 3.0, lookback: int = 60):
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_zscore = stop_zscore
        self.lookback = lookback  # Périodes de lookback pour mean/std du ratio

    def get_required_tickers(self) -> list[str]:
        return list(set(t for pair in config.PAIR_TICKERS for t in pair))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker_a, ticker_b in config.PAIR_TICKERS:
            if ticker_a not in data or ticker_b not in data:
                continue

            df_a = data[ticker_a]
            df_b = data[ticker_b]

            # Aligner les timestamps
            common_idx = df_a.index.intersection(df_b.index)
            if len(common_idx) < self.lookback + 10:
                continue

            prices_a = df_a.loc[common_idx, "close"]
            prices_b = df_b.loc[common_idx, "close"]

            # Ratio normalisé
            ratio = prices_a / prices_b
            mean = ratio.rolling(self.lookback, min_periods=self.lookback).mean()
            std = ratio.rolling(self.lookback, min_periods=self.lookback).std()
            zscore = (ratio - mean) / std.replace(0, np.nan)

            # Scanner pour des signaux après 10:00 (assez de données intraday)
            tradeable = zscore.between_time("10:00", "15:30")
            signal_found = False

            for ts in tradeable.index:
                if signal_found:
                    break

                z = zscore.loc[ts]
                if pd.isna(z):
                    continue

                price_a = prices_a.loc[ts]
                price_b = prices_b.loc[ts]

                if z > self.entry_zscore:
                    # A est surperformant → SHORT A, LONG B
                    # On simplifie en ne prenant qu'un côté (le long)
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker_b,
                        entry_price=price_b,
                        stop_loss=price_b * 0.99,    # ~1% stop
                        take_profit=price_b * 1.015,  # ~1.5% target
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "pair": f"{ticker_a}/{ticker_b}",
                            "zscore": round(z, 2),
                            "direction": f"SHORT {ticker_a} / LONG {ticker_b}",
                        },
                    ))
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker_a,
                        entry_price=price_a,
                        stop_loss=price_a * 1.01,
                        take_profit=price_a * 0.985,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "pair": f"{ticker_a}/{ticker_b}",
                            "zscore": round(z, 2),
                            "direction": f"SHORT {ticker_a} / LONG {ticker_b}",
                        },
                    ))
                    signal_found = True

                elif z < -self.entry_zscore:
                    # B est surperformant → SHORT B, LONG A
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker_a,
                        entry_price=price_a,
                        stop_loss=price_a * 0.99,
                        take_profit=price_a * 1.015,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "pair": f"{ticker_a}/{ticker_b}",
                            "zscore": round(z, 2),
                            "direction": f"LONG {ticker_a} / SHORT {ticker_b}",
                        },
                    ))
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker_b,
                        entry_price=price_b,
                        stop_loss=price_b * 1.01,
                        take_profit=price_b * 0.985,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "pair": f"{ticker_a}/{ticker_b}",
                            "zscore": round(z, 2),
                            "direction": f"LONG {ticker_a} / SHORT {ticker_b}",
                        },
                    ))
                    signal_found = True

        return signals
