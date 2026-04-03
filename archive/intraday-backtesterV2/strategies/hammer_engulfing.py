"""
Strategie : Hammer & Engulfing Reversal

Edge structurel :
Patterns de chandeliers classiques avec confirmation contextuelle.
- Hammer : longue meche basse (>2x le body), body en haut de la bougie, apres 3+ bougies rouges
- Engulfing : bougie qui englobe entierement la precedente, signal de retournement
Ces patterns sont plus fiables apres un mouvement directionnel de 3+ barres.

Iteration barre par barre. Stop sous/au-dessus du pattern, target 1.2%.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class HammerEngulfingStrategy(BaseStrategy):
    name = "Hammer Engulfing Reversal"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    TARGET_PCT = 0.012          # 1.2% target
    STOP_BUFFER_PCT = 0.002     # Buffer additionnel sur le stop
    MIN_CONSECUTIVE = 3         # 3+ barres dans une direction pour le contexte
    VOL_RATIO_MIN = 1.0         # Volume minimum

    @staticmethod
    def _is_hammer(bar, prev_bars: pd.DataFrame, direction: str = "bullish") -> bool:
        """Detecte un hammer bullish apres downtrend."""
        body = abs(bar["close"] - bar["open"])
        full_range = bar["high"] - bar["low"]

        if full_range <= 0 or body <= 0:
            return False

        if direction == "bullish":
            # Hammer bullish : meche basse longue, body en haut
            lower_wick = min(bar["open"], bar["close"]) - bar["low"]
            upper_wick = bar["high"] - max(bar["open"], bar["close"])
            # Meche basse > 2x body, meche haute < body
            return lower_wick > 2 * body and upper_wick < body
        else:
            # Inverted hammer / shooting star : meche haute longue
            upper_wick = bar["high"] - max(bar["open"], bar["close"])
            lower_wick = min(bar["open"], bar["close"]) - bar["low"]
            return upper_wick > 2 * body and lower_wick < body

    @staticmethod
    def _is_engulfing(current_bar, prev_bar, direction: str = "bullish") -> bool:
        """Detecte un pattern engulfing."""
        if direction == "bullish":
            # Bullish engulfing : bougie verte qui englobe la rouge precedente
            prev_red = prev_bar["close"] < prev_bar["open"]
            curr_green = current_bar["close"] > current_bar["open"]
            engulfs = (current_bar["open"] <= prev_bar["close"] and
                       current_bar["close"] >= prev_bar["open"])
            return prev_red and curr_green and engulfs
        else:
            # Bearish engulfing : bougie rouge qui englobe la verte precedente
            prev_green = prev_bar["close"] > prev_bar["open"]
            curr_red = current_bar["close"] < current_bar["open"]
            engulfs = (current_bar["open"] >= prev_bar["close"] and
                       current_bar["close"] <= prev_bar["open"])
            return prev_green and curr_red and engulfs

    @staticmethod
    def _count_consecutive(df_slice: pd.DataFrame, direction: str) -> int:
        """Compte le nombre de barres consecutives dans une direction."""
        count = 0
        for i in range(len(df_slice) - 1, -1, -1):
            bar = df_slice.iloc[i]
            if direction == "down" and bar["close"] < bar["open"]:
                count += 1
            elif direction == "up" and bar["close"] > bar["open"]:
                count += 1
            else:
                break
        return count

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < 15:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre (10:00-15:30)
            tradeable = df.between_time("10:00", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.MIN_CONSECUTIVE + 2:
                    continue

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                entry_price = bar["close"]
                prev_bar = df.iloc[idx - 1]
                context_bars = df.iloc[max(0, idx - self.MIN_CONSECUTIVE - 1):idx]

                # === BULLISH SETUPS (apres downtrend) ===
                consecutive_down = self._count_consecutive(context_bars, "down")
                if consecutive_down >= self.MIN_CONSECUTIVE:
                    # Check hammer bullish
                    is_hammer = self._is_hammer(bar, context_bars, "bullish")
                    # Check bullish engulfing
                    is_engulfing = self._is_engulfing(bar, prev_bar, "bullish")

                    if is_hammer or is_engulfing:
                        pattern_low = min(bar["low"], prev_bar["low"])
                        stop_loss = pattern_low * (1 - self.STOP_BUFFER_PCT)
                        take_profit = entry_price * (1 + self.TARGET_PCT)

                        # Verifier que le risk est raisonnable (<3%)
                        risk = (entry_price - stop_loss) / entry_price
                        if risk > 0.03 or risk <= 0:
                            continue

                        pattern_type = "hammer" if is_hammer else "engulfing"
                        candidates.append({
                            "score": consecutive_down + (1 if is_hammer and is_engulfing else 0),
                            "signal": Signal(
                                action="LONG",
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                timestamp=ts,
                                metadata={
                                    "strategy": self.name,
                                    "pattern": pattern_type,
                                    "consecutive_bars": consecutive_down,
                                    "vol_ratio": round(bar["vol_ratio"], 1),
                                },
                            ),
                        })
                        signal_found = True
                        continue

                # === BEARISH SETUPS (apres uptrend) ===
                consecutive_up = self._count_consecutive(context_bars, "up")
                if consecutive_up >= self.MIN_CONSECUTIVE:
                    # Check shooting star (inverted hammer bearish)
                    is_shooting = self._is_hammer(bar, context_bars, "bearish")
                    # Check bearish engulfing
                    is_engulfing = self._is_engulfing(bar, prev_bar, "bearish")

                    if is_shooting or is_engulfing:
                        pattern_high = max(bar["high"], prev_bar["high"])
                        stop_loss = pattern_high * (1 + self.STOP_BUFFER_PCT)
                        take_profit = entry_price * (1 - self.TARGET_PCT)

                        risk = (stop_loss - entry_price) / entry_price
                        if risk > 0.03 or risk <= 0:
                            continue

                        pattern_type = "shooting_star" if is_shooting else "engulfing"
                        candidates.append({
                            "score": consecutive_up + (1 if is_shooting and is_engulfing else 0),
                            "signal": Signal(
                                action="SHORT",
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                timestamp=ts,
                                metadata={
                                    "strategy": self.name,
                                    "pattern": pattern_type,
                                    "consecutive_bars": consecutive_up,
                                    "vol_ratio": round(bar["vol_ratio"], 1),
                                },
                            ),
                        })
                        signal_found = True

        # Trier par score (plus de barres consecutives = meilleur contexte)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
