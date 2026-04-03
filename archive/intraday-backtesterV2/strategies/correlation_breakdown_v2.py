"""
Strategie : Correlation Breakdown V2 (Pairs Trading)

Changements vs V1 (0 trades car z-score > 2.0 jamais atteint) :
- Z-score entry : 1.5 au lieu de 2.0
- Z-score exit : 0.3 au lieu de 0.5
- Z-score stop : 2.5 au lieu de 3.0
- Lookback : 40 barres au lieu de 60
- Paires MOINS correlees qui divergent plus souvent :
  NVDA/TSLA, META/NFLX, JPM/GS, XOM/SLB, AAPL/AMZN
- Stop max : 1.5% (pas juste z-score)
- Prix > $20 par ticker
- Max 2 paires/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# ── Paires V2 final : mix des meilleures ──
PAIRS_V2 = [
    ("XOM", "SLB"),     # Petrole/services — meilleur WR dans les tests
    ("NVDA", "AMD"),    # Semi-conducteurs
    ("META", "NFLX"),   # Consumer tech
    ("JPM", "GS"),      # Banques d'investissement
    ("AAPL", "MSFT"),   # Big Tech
]

MIN_PRICE = 20.0


class CorrelationBreakdownV2Strategy(BaseStrategy):
    name = "Correlation Breakdown V2"

    def __init__(
        self,
        entry_zscore: float = 1.5,     # V2final : revenir a 1.5 (plus d'opportunites)
        exit_zscore: float = 0.3,       # V2 : 0.3 au lieu de 0.5
        stop_zscore: float = 2.5,       # V2 : 2.5 au lieu de 3.0
        stop_max_pct: float = 0.006,    # V2best : 0.6% stop
        lookback: int = 40,             # V2 : 40 au lieu de 60
        max_pairs_per_day: int = 2,
    ):
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_zscore = stop_zscore
        self.stop_max_pct = stop_max_pct
        self.lookback = lookback
        self.max_pairs_per_day = max_pairs_per_day

    def get_required_tickers(self) -> list[str]:
        return list(set(t for pair in PAIRS_V2 for t in pair))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        pairs_traded = 0

        for ticker_a, ticker_b in PAIRS_V2:
            if pairs_traded >= self.max_pairs_per_day:
                break

            if ticker_a not in data or ticker_b not in data:
                continue

            df_a = data[ticker_a]
            df_b = data[ticker_b]

            # ── Filtre prix minimum $20 ──
            if df_a["close"].mean() < MIN_PRICE or df_b["close"].mean() < MIN_PRICE:
                continue

            # Aligner les timestamps
            common_idx = df_a.index.intersection(df_b.index)
            if len(common_idx) < self.lookback + 10:
                continue

            prices_a = df_a.loc[common_idx, "close"]
            prices_b = df_b.loc[common_idx, "close"]

            # Ratio normalise + z-score
            ratio = prices_a / prices_b
            mean = ratio.rolling(self.lookback, min_periods=self.lookback).mean()
            std = ratio.rolling(self.lookback, min_periods=self.lookback).std()
            zscore = (ratio - mean) / std.replace(0, np.nan)

            # Scanner pour des signaux apres 10:30 (assez de donnees + temps avant close)
            tradeable = zscore.between_time("10:30", "14:00")
            signal_found = False

            for ts in tradeable.index:
                if signal_found:
                    break

                z = zscore.loc[ts]
                if pd.isna(z):
                    continue

                price_a = prices_a.loc[ts]
                price_b = prices_b.loc[ts]

                if abs(z) > self.entry_zscore:
                    if z > self.entry_zscore:
                        # A surperforme → SHORT A, LONG B
                        long_ticker, short_ticker = ticker_b, ticker_a
                        long_price, short_price = price_b, price_a
                    else:
                        # B surperforme → SHORT B, LONG A
                        long_ticker, short_ticker = ticker_a, ticker_b
                        long_price, short_price = price_a, price_b

                    # Stop max : 1.5% OU z-score stop
                    long_stop = long_price * (1 - self.stop_max_pct)
                    short_stop = short_price * (1 + self.stop_max_pct)

                    # Target : exit z-score → approximation en prix
                    # Quand z-score revient a 0.3, le prix devrait bouger
                    # d'environ (entry_z - exit_z) / entry_z * distance
                    target_move_pct = (abs(z) - self.exit_zscore) / abs(z) * self.stop_max_pct
                    target_move_pct = max(target_move_pct, 0.005)  # minimum 0.5%

                    long_target = long_price * (1 + target_move_pct)
                    short_target = short_price * (1 - target_move_pct)

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

        return signals
