"""
Correlation Regime Hedge

Edge : SPY/TLT et GLD/USO sont normalement inversement correles.
Quand leur correlation rolling sur 20 barres devient positive (> 0.5),
c'est une anomalie de regime. L'un des deux va revert vers son VWAP.
On short celui qui a le plus devie de son VWAP intraday (mean reversion).

Stop 0.5%, target 0.8%. Max 2 trades/jour.
Iteration barre par barre 11:00-15:00.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap


# Paires normalement inversement correlees
PAIRS = [
    ("SPY", "TLT"),
    ("GLD", "USO"),
]

CORR_LOOKBACK = 20             # Fenetre rolling pour la correlation
CORR_THRESHOLD = 0.5           # Correlation positive = anomalie


class CorrelationRegimeHedgeStrategy(BaseStrategy):
    name = "Correlation Regime Hedge"

    STOP_PCT = 0.005           # 0.5%
    TARGET_PCT = 0.008         # 0.8%
    MAX_TRADES_PER_DAY = 2

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "TLT", "GLD", "USO"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker_a, ticker_b in PAIRS:
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break

            if ticker_a not in data or ticker_b not in data:
                continue

            df_a = data[ticker_a]
            df_b = data[ticker_b]

            if len(df_a) < CORR_LOOKBACK + 10 or len(df_b) < CORR_LOOKBACK + 10:
                continue

            # Aligner les timestamps
            common_idx = df_a.index.intersection(df_b.index)
            if len(common_idx) < CORR_LOOKBACK + 10:
                continue

            a_aligned = df_a.loc[common_idx].copy()
            b_aligned = df_b.loc[common_idx].copy()

            # Calculer les returns bar-a-bar
            a_ret = a_aligned["close"].pct_change()
            b_ret = b_aligned["close"].pct_change()

            # Correlation rolling
            rolling_corr = a_ret.rolling(CORR_LOOKBACK, min_periods=CORR_LOOKBACK).corr(b_ret)

            # Calculer le VWAP pour chaque actif
            vwap_a = vwap(df_a)
            vwap_b = vwap(df_b)

            signal_found = False

            # Iterer barre par barre 11:00-15:00
            tradeable_idx = a_aligned.between_time("11:00", "15:00").index

            for ts in tradeable_idx:
                if signal_found:
                    break

                if ts not in rolling_corr.index:
                    continue

                corr_val = rolling_corr.loc[ts]
                if pd.isna(corr_val):
                    continue

                # Condition : correlation anormalement positive
                if corr_val < CORR_THRESHOLD:
                    continue

                # Anomalie detectee — determiner qui a le plus devie de son VWAP
                price_a = a_aligned.loc[ts, "close"]
                price_b = b_aligned.loc[ts, "close"]

                # VWAP deviation en pourcentage
                vwap_a_at = vwap_a[vwap_a.index <= ts]
                vwap_b_at = vwap_b[vwap_b.index <= ts]

                if vwap_a_at.empty or vwap_b_at.empty:
                    continue

                vwap_a_val = vwap_a_at.iloc[-1]
                vwap_b_val = vwap_b_at.iloc[-1]

                if pd.isna(vwap_a_val) or pd.isna(vwap_b_val):
                    continue
                if vwap_a_val == 0 or vwap_b_val == 0:
                    continue

                dev_a = (price_a - vwap_a_val) / vwap_a_val  # Positive = above VWAP
                dev_b = (price_b - vwap_b_val) / vwap_b_val

                # Short celui qui a le plus devie (en valeur absolue) de son VWAP
                if abs(dev_a) > abs(dev_b):
                    # Ticker A a plus devie — il va revert
                    target_ticker = ticker_a
                    entry_price = price_a
                    deviation = dev_a
                else:
                    # Ticker B a plus devie — il va revert
                    target_ticker = ticker_b
                    entry_price = price_b
                    deviation = dev_b

                # Direction : si prix au-dessus du VWAP → SHORT (va baisser)
                #             si prix en-dessous du VWAP → LONG (va monter)
                if deviation > 0:
                    action = "SHORT"
                    stop = entry_price * (1 + self.STOP_PCT)
                    target = entry_price * (1 - self.TARGET_PCT)
                else:
                    action = "LONG"
                    stop = entry_price * (1 - self.STOP_PCT)
                    target = entry_price * (1 + self.TARGET_PCT)

                signals.append(Signal(
                    action=action,
                    ticker=target_ticker,
                    entry_price=entry_price,
                    stop_loss=stop,
                    take_profit=target,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "pair": f"{ticker_a}/{ticker_b}",
                        "correlation": round(corr_val, 3),
                        "vwap_deviation_pct": round(deviation * 100, 2),
                        "traded": target_ticker,
                    },
                ))
                trades_today += 1
                signal_found = True

        return signals[:self.MAX_TRADES_PER_DAY]
