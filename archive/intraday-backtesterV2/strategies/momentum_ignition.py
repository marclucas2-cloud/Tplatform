"""
Strategie : Momentum Ignition

Edge structurel :
3 bougies consecutives dans la meme direction avec un volume croissant et
chaque bougie depasse le range de la precedente (close > prev high pour bullish,
close < prev low pour bearish) = "momentum ignition".
Ce pattern indique un flux institutionnel agressif qui va continuer
pendant 1-3 barres supplementaires.

Regles :
- 3 barres consecutives vertes (bullish) ou rouges (bearish)
- Volume croissant : vol_bar2 > vol_bar1, vol_bar3 > vol_bar2
- Bullish : close de chaque barre > high de la barre precedente
- Bearish : close de chaque barre < low de la barre precedente
- Entry au close de la 3eme barre (confirmation du pattern)
- Stop : 0.8% — Target : 1.2% (continuation du momentum)
- Max 3 trades/jour, prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
}

MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
STOP_PCT = 0.008       # 0.8%
TARGET_PCT = 0.012     # 1.2%


class MomentumIgnitionStrategy(BaseStrategy):
    name = "Momentum Ignition"

    def __init__(
        self,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 15:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # Scanner entre 9:45 et 15:30
            tradeable = df.between_time("09:45", "15:30")
            if len(tradeable) < 4:
                continue

            tradeable_list = list(tradeable.iterrows())
            signal_found = False

            for i in range(3, len(tradeable_list)):
                if signal_found:
                    break

                ts_1, bar_1 = tradeable_list[i - 3]
                ts_2, bar_2 = tradeable_list[i - 2]
                ts_3, bar_3 = tradeable_list[i - 1]
                # bar_3 est la 3eme barre du pattern — on entre apres sa completion

                # ── Verifier pattern bullish ──
                bullish_bars = (
                    bar_1["close"] > bar_1["open"]
                    and bar_2["close"] > bar_2["open"]
                    and bar_3["close"] > bar_3["open"]
                )

                bullish_breakout = (
                    bar_2["close"] > bar_1["high"]
                    and bar_3["close"] > bar_2["high"]
                )

                bullish_volume = (
                    bar_2["volume"] > bar_1["volume"]
                    and bar_3["volume"] > bar_2["volume"]
                    and bar_1["volume"] > 0
                )

                if bullish_bars and bullish_breakout and bullish_volume:
                    entry_price = bar_3["close"]
                    stop_loss = entry_price * (1 - self.stop_pct)
                    take_profit = entry_price * (1 + self.target_pct)

                    # Score = volume acceleration * range expansion
                    vol_accel = bar_3["volume"] / bar_1["volume"] if bar_1["volume"] > 0 else 1
                    range_expand = (bar_3["high"] - bar_3["low"]) / entry_price
                    score = vol_accel * range_expand

                    candidates.append({
                        "score": score,
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=tradeable_list[i - 1][0],
                            metadata={
                                "strategy": self.name,
                                "pattern": "bullish_ignition",
                                "vol_acceleration": round(vol_accel, 2),
                                "bars_range_pct": round(range_expand * 100, 3),
                            },
                        ),
                    })
                    signal_found = True
                    continue

                # ── Verifier pattern bearish ──
                bearish_bars = (
                    bar_1["close"] < bar_1["open"]
                    and bar_2["close"] < bar_2["open"]
                    and bar_3["close"] < bar_3["open"]
                )

                bearish_breakout = (
                    bar_2["close"] < bar_1["low"]
                    and bar_3["close"] < bar_2["low"]
                )

                bearish_volume = (
                    bar_2["volume"] > bar_1["volume"]
                    and bar_3["volume"] > bar_2["volume"]
                    and bar_1["volume"] > 0
                )

                if bearish_bars and bearish_breakout and bearish_volume:
                    entry_price = bar_3["close"]
                    stop_loss = entry_price * (1 + self.stop_pct)
                    take_profit = entry_price * (1 - self.target_pct)

                    vol_accel = bar_3["volume"] / bar_1["volume"] if bar_1["volume"] > 0 else 1
                    range_expand = (bar_3["high"] - bar_3["low"]) / entry_price
                    score = vol_accel * range_expand

                    candidates.append({
                        "score": score,
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=tradeable_list[i - 1][0],
                            metadata={
                                "strategy": self.name,
                                "pattern": "bearish_ignition",
                                "vol_acceleration": round(vol_accel, 2),
                                "bars_range_pct": round(range_expand * 100, 3),
                            },
                        ),
                    })
                    signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
