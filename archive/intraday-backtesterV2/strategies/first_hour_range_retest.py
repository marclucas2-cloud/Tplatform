"""
Strategie : First Hour Range Retest

Edge structurel :
Apres que le range de la premiere heure (9:30-10:30) est etabli,
les retests du high/low avec un volume en baisse indiquent un faux breakout
et signalent un reversal vers le milieu du range.

Regles :
- Calculer le range 9:30-10:30 (high/low)
- Apres 10:30, chercher un retest du high ou low
- Le volume a la barre de retest doit etre < 0.7x le volume moyen des barres du range
- Entry au retest + confirmation de volume en baisse
- Stop : au-dela du range (high + buffer pour short, low - buffer pour long)
- Target : milieu du range
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
VOL_DECLINE_THRESHOLD = 0.7   # Volume < 70% de la moyenne du range
RETEST_TOLERANCE_PCT = 0.002  # Retest = prix within 0.2% du high/low
STOP_BUFFER_PCT = 0.003       # Stop 0.3% au-dela du range


class FirstHourRangeRetestStrategy(BaseStrategy):
    name = "First Hour Range Retest"

    def __init__(
        self,
        vol_decline: float = VOL_DECLINE_THRESHOLD,
        retest_tolerance: float = RETEST_TOLERANCE_PCT,
        stop_buffer: float = STOP_BUFFER_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.vol_decline = vol_decline
        self.retest_tolerance = retest_tolerance
        self.stop_buffer = stop_buffer
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 20:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # ── Calculer le range de la premiere heure (9:30-10:30) ──
            first_hour = df.between_time("09:30", "10:29")
            if len(first_hour) < 6:
                continue

            range_high = first_hour["high"].max()
            range_low = first_hour["low"].min()
            range_mid = (range_high + range_low) / 2
            range_size = range_high - range_low

            # Range trop petit (< 0.3% du prix) = pas de setup
            if range_size / range_mid < 0.003:
                continue

            # Volume moyen de la premiere heure
            avg_range_vol = first_hour["volume"].mean()
            if avg_range_vol <= 0:
                continue

            # ── Chercher retest apres 10:30 ──
            post_range = df.between_time("10:30", "15:30")
            if post_range.empty:
                continue

            signal_found = False

            for ts, bar in post_range.iterrows():
                if signal_found:
                    break

                bar_vol_ratio = bar["volume"] / avg_range_vol if avg_range_vol > 0 else 1.0

                # Retest du HIGH avec volume en baisse → SHORT reversal
                high_dist = abs(bar["high"] - range_high) / range_high
                if high_dist < self.retest_tolerance and bar["close"] < range_high:
                    if bar_vol_ratio < self.vol_decline:
                        entry_price = bar["close"]
                        stop_loss = range_high * (1 + self.stop_buffer)
                        take_profit = range_mid

                        # Verifier R:R minimal
                        risk = stop_loss - entry_price
                        reward = entry_price - take_profit
                        if risk > 0 and reward > 0 and reward / risk > 0.5:
                            candidates.append({
                                "score": (1 - bar_vol_ratio) * range_size / range_mid,
                                "signal": Signal(
                                    action="SHORT",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "range_high": round(range_high, 2),
                                        "range_low": round(range_low, 2),
                                        "vol_ratio_at_retest": round(bar_vol_ratio, 2),
                                        "direction": "RETEST_HIGH",
                                    },
                                ),
                            })
                            signal_found = True

                # Retest du LOW avec volume en baisse → LONG reversal
                low_dist = abs(bar["low"] - range_low) / range_low
                if not signal_found and low_dist < self.retest_tolerance and bar["close"] > range_low:
                    if bar_vol_ratio < self.vol_decline:
                        entry_price = bar["close"]
                        stop_loss = range_low * (1 - self.stop_buffer)
                        take_profit = range_mid

                        risk = entry_price - stop_loss
                        reward = take_profit - entry_price
                        if risk > 0 and reward > 0 and reward / risk > 0.5:
                            candidates.append({
                                "score": (1 - bar_vol_ratio) * range_size / range_mid,
                                "signal": Signal(
                                    action="LONG",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "range_high": round(range_high, 2),
                                        "range_low": round(range_low, 2),
                                        "vol_ratio_at_retest": round(bar_vol_ratio, 2),
                                        "direction": "RETEST_LOW",
                                    },
                                ),
                            })
                            signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
