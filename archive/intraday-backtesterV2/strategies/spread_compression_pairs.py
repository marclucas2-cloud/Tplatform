"""
Strategie : Spread Compression Pairs

Edge structurel :
Les paires hautement correlees (NVDA/AMD, XOM/CVX, JPM/BAC) ont un ratio
de prix relativement stable. Quand le z-score du ratio atteint +-1.5 sur
un lookback de 20 barres, le spread tend a se comprimer (mean reversion).

Difference vs correlation_breakdown :
- Utilise le RATIO de prix directement (pas la correlation)
- Lookback plus court (20 barres vs 40)
- Paires differentes optimisees pour le ratio trading
- Z-score entry a 1.5 au lieu de 2.0

Regles :
- Calculer le ratio prix_A / prix_B
- Z-score du ratio sur 20 barres rolling
- Z-score > +1.5 → SHORT A, LONG B (A sur-performe)
- Z-score < -1.5 → LONG A, SHORT B (B sur-performe)
- Stop : z-score a 2.5 OU 0.8% max
- Target : z-score retour a 0.3 OU 0.5%
- Max 3 trades/jour (max 2 paires)
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# Paires optimisees pour le ratio trading
RATIO_PAIRS = [
    ("NVDA", "AMD"),
    ("XOM", "CVX"),
    ("JPM", "BAC"),
    ("AAPL", "MSFT"),
    ("META", "GOOGL"),
    ("GS", "MS"),
]

MIN_PRICE = 10.0
MAX_TRADES_PER_DAY = 3
ENTRY_ZSCORE = 1.5
EXIT_ZSCORE = 0.3
STOP_ZSCORE = 2.5
STOP_MAX_PCT = 0.008      # 0.8%
TARGET_PCT = 0.005         # 0.5%
LOOKBACK = 20


class SpreadCompressionPairsStrategy(BaseStrategy):
    name = "Spread Compression Pairs"

    def __init__(
        self,
        entry_zscore: float = ENTRY_ZSCORE,
        stop_zscore: float = STOP_ZSCORE,
        stop_max_pct: float = STOP_MAX_PCT,
        target_pct: float = TARGET_PCT,
        lookback: int = LOOKBACK,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.entry_zscore = entry_zscore
        self.stop_zscore = stop_zscore
        self.stop_max_pct = stop_max_pct
        self.target_pct = target_pct
        self.lookback = lookback
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return list(set(t for pair in RATIO_PAIRS for t in pair))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        pairs_traded = 0

        for ticker_a, ticker_b in RATIO_PAIRS:
            if pairs_traded >= 2:  # Max 2 paires par jour
                break

            if ticker_a not in data or ticker_b not in data:
                continue

            df_a = data[ticker_a]
            df_b = data[ticker_b]

            if df_a.iloc[0]["open"] < MIN_PRICE or df_b.iloc[0]["open"] < MIN_PRICE:
                continue

            # Aligner les timestamps
            common_idx = df_a.index.intersection(df_b.index)
            if len(common_idx) < self.lookback + 10:
                continue

            prices_a = df_a.loc[common_idx, "close"]
            prices_b = df_b.loc[common_idx, "close"]

            # Ratio de prix et z-score
            ratio = prices_a / prices_b.replace(0, np.nan)
            ratio_mean = ratio.rolling(self.lookback, min_periods=self.lookback).mean()
            ratio_std = ratio.rolling(self.lookback, min_periods=self.lookback).std()
            zscore = (ratio - ratio_mean) / ratio_std.replace(0, np.nan)

            # Scanner apres 10:30 (assez de donnees)
            tradeable = zscore.between_time("10:30", "14:30")
            signal_found = False

            for ts in tradeable.index:
                if signal_found:
                    break

                z = zscore.loc[ts]
                if pd.isna(z):
                    continue

                if abs(z) < self.entry_zscore:
                    continue

                price_a = prices_a.loc[ts]
                price_b = prices_b.loc[ts]

                if z > self.entry_zscore:
                    # A surperforme → SHORT A, LONG B
                    long_ticker, short_ticker = ticker_b, ticker_a
                    long_price, short_price = price_b, price_a
                else:
                    # B surperforme → LONG A, SHORT B
                    long_ticker, short_ticker = ticker_a, ticker_b
                    long_price, short_price = price_a, price_b

                long_stop = long_price * (1 - self.stop_max_pct)
                short_stop = short_price * (1 + self.stop_max_pct)
                long_target = long_price * (1 + self.target_pct)
                short_target = short_price * (1 - self.target_pct)

                signals.append(Signal(
                    action="LONG",
                    ticker=long_ticker,
                    entry_price=long_price,
                    stop_loss=long_stop,
                    take_profit=long_target,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "pair": f"{ticker_a}/{ticker_b}",
                        "zscore": round(z, 2),
                        "direction": f"LONG {long_ticker} / SHORT {short_ticker}",
                    },
                ))
                signals.append(Signal(
                    action="SHORT",
                    ticker=short_ticker,
                    entry_price=short_price,
                    stop_loss=short_stop,
                    take_profit=short_target,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "pair": f"{ticker_a}/{ticker_b}",
                        "zscore": round(z, 2),
                        "direction": f"LONG {long_ticker} / SHORT {short_ticker}",
                    },
                ))
                signal_found = True
                pairs_traded += 1

        return signals[:self.max_trades_per_day]
