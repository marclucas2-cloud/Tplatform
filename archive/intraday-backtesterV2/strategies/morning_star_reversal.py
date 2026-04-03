"""
Strategie : Morning Star / Evening Star Reversal

Edge structurel :
Le pattern 3-barre "morning star" (grosse bougie baissiere + doji/spinning top
+ grosse bougie haussiere) est un signal de retournement classique.
L'inverse (evening star) est un signal baissier.

Regles :
- Pattern morning star :
  1. Barre 1 : grosse bougie baissiere (body > 60% du range, close < open)
  2. Barre 2 : petit body (< 30% du range = doji/spinning top)
  3. Barre 3 : grosse bougie haussiere (body > 60% du range, close > open)
  et close de barre 3 > midpoint de barre 1
- Evening star = inverse
- Stop : sous le low du pattern (morning star) / au-dessus du high (evening star)
- Target : 1.5x le risk
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
BIG_BODY_PCT = 0.60     # Body > 60% du range = grosse bougie
SMALL_BODY_PCT = 0.30   # Body < 30% du range = doji/spinning top
RR_RATIO = 1.5          # Risk:Reward 1:1.5
STOP_BUFFER_PCT = 0.002 # 0.2% buffer au-dela du pattern


class MorningStarReversalStrategy(BaseStrategy):
    name = "Morning Star Reversal"

    def __init__(
        self,
        big_body_pct: float = BIG_BODY_PCT,
        small_body_pct: float = SMALL_BODY_PCT,
        rr_ratio: float = RR_RATIO,
        stop_buffer: float = STOP_BUFFER_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.big_body_pct = big_body_pct
        self.small_body_pct = small_body_pct
        self.rr_ratio = rr_ratio
        self.stop_buffer = stop_buffer
        self.max_trades_per_day = max_trades_per_day

    def _bar_body_pct(self, bar: pd.Series) -> float:
        """Pourcentage du body par rapport au range de la barre."""
        bar_range = bar["high"] - bar["low"]
        if bar_range <= 0:
            return 0.0
        body = abs(bar["close"] - bar["open"])
        return body / bar_range

    def _is_bullish(self, bar: pd.Series) -> bool:
        return bar["close"] > bar["open"]

    def _is_bearish(self, bar: pd.Series) -> bool:
        return bar["close"] < bar["open"]

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

            # Scanner entre 10:00 et 15:30 (besoin de 3 barres d'historique)
            tradeable = df.between_time("10:00", "15:30")
            if len(tradeable) < 3:
                continue

            signal_found = False

            # Iterer barre par barre — on regarde les 3 dernieres barres
            tradeable_list = list(tradeable.iterrows())

            for i in range(2, len(tradeable_list)):
                if signal_found:
                    break

                ts_1, bar_1 = tradeable_list[i - 2]
                ts_2, bar_2 = tradeable_list[i - 1]
                ts_3, bar_3 = tradeable_list[i]

                body_pct_1 = self._bar_body_pct(bar_1)
                body_pct_2 = self._bar_body_pct(bar_2)
                body_pct_3 = self._bar_body_pct(bar_3)

                # ── Morning Star (bullish reversal) ──
                if (self._is_bearish(bar_1) and body_pct_1 > self.big_body_pct
                        and body_pct_2 < self.small_body_pct
                        and self._is_bullish(bar_3) and body_pct_3 > self.big_body_pct):
                    # Confirmation : close de barre 3 depasse le midpoint de barre 1
                    mid_bar1 = (bar_1["open"] + bar_1["close"]) / 2
                    if bar_3["close"] > mid_bar1:
                        pattern_low = min(bar_1["low"], bar_2["low"], bar_3["low"])
                        entry_price = bar_3["close"]
                        stop_loss = pattern_low * (1 - self.stop_buffer)

                        risk = entry_price - stop_loss
                        if risk <= 0 or risk / entry_price > 0.03:
                            continue

                        take_profit = entry_price + risk * self.rr_ratio

                        pattern_range = max(bar_1["high"], bar_2["high"], bar_3["high"]) - pattern_low
                        score = body_pct_3 * pattern_range / entry_price

                        candidates.append({
                            "score": score,
                            "signal": Signal(
                                action="LONG",
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                timestamp=ts_3,
                                metadata={
                                    "strategy": self.name,
                                    "pattern": "morning_star",
                                    "body_pct_1": round(body_pct_1, 2),
                                    "body_pct_2": round(body_pct_2, 2),
                                    "body_pct_3": round(body_pct_3, 2),
                                },
                            ),
                        })
                        signal_found = True

                # ── Evening Star (bearish reversal) ──
                elif (self._is_bullish(bar_1) and body_pct_1 > self.big_body_pct
                      and body_pct_2 < self.small_body_pct
                      and self._is_bearish(bar_3) and body_pct_3 > self.big_body_pct):
                    mid_bar1 = (bar_1["open"] + bar_1["close"]) / 2
                    if bar_3["close"] < mid_bar1:
                        pattern_high = max(bar_1["high"], bar_2["high"], bar_3["high"])
                        entry_price = bar_3["close"]
                        stop_loss = pattern_high * (1 + self.stop_buffer)

                        risk = stop_loss - entry_price
                        if risk <= 0 or risk / entry_price > 0.03:
                            continue

                        take_profit = entry_price - risk * self.rr_ratio

                        pattern_range = pattern_high - min(bar_1["low"], bar_2["low"], bar_3["low"])
                        score = body_pct_3 * pattern_range / entry_price

                        candidates.append({
                            "score": score,
                            "signal": Signal(
                                action="SHORT",
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                timestamp=ts_3,
                                metadata={
                                    "strategy": self.name,
                                    "pattern": "evening_star",
                                    "body_pct_1": round(body_pct_1, 2),
                                    "body_pct_2": round(body_pct_2, 2),
                                    "body_pct_3": round(body_pct_3, 2),
                                },
                            ),
                        })
                        signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
