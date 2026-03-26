"""
Signal Confluence — Multi-Indicator Agreement

Edge : Quand 2+ indicateurs independants convergent sur la meme direction
pour un ticker, le signal est significativement plus fiable.
On check 4 indicateurs :
  - RSI(14) < 30 ou > 70 (extreme)
  - Prix sous/au-dessus du VWAP
  - Prix sous/au-dessus des BB lower/upper (20, 2.0)
  - Volume > 2x la moyenne
Si 3+ indicateurs s'alignent dans la meme direction → signal fort.

Stop 0.7%, target 1.4% (R:R = 2:1). Max 3 trades/jour.
Iteration barre par barre 10:00-15:00. Min prix $15.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, vwap, bollinger_bands, volume_ratio


class SignalConfluenceStrategy(BaseStrategy):
    name = "Signal Confluence"

    RSI_PERIOD = 14
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    BB_PERIOD = 20
    BB_STD = 2.0
    VOL_THRESHOLD = 2.0        # Volume > 2x moyenne
    MIN_CONFLUENCE = 3          # 3+ indicateurs doivent s'aligner
    STOP_PCT = 0.007           # 0.7%
    TARGET_PCT = 0.014         # 1.4%
    MIN_PRICE = 15.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < 30:
                continue
            if df["close"].iloc[0] < self.MIN_PRICE:
                continue

            df = df.copy()

            # Calculer les indicateurs
            df["rsi"] = rsi(df["close"], self.RSI_PERIOD)
            bb_upper, bb_middle, bb_lower = bollinger_bands(df["close"], self.BB_PERIOD, self.BB_STD)
            df["bb_upper"] = bb_upper
            df["bb_lower"] = bb_lower
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            vwap_vals = vwap(df)
            df["vwap"] = vwap_vals

            signal_found = False

            # Iterer barre par barre 10:00-15:00
            tradeable = df.between_time("10:00", "15:00")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                # Skip si indicateurs pas prets
                if pd.isna(bar["rsi"]) or pd.isna(bar["bb_upper"]) or pd.isna(bar["bb_lower"]):
                    continue
                if pd.isna(bar["vwap"]) or pd.isna(bar["vol_ratio"]):
                    continue

                price = bar["close"]

                # ── Compter les indicateurs LONG (bearish reversal) ──
                long_count = 0
                long_reasons = []

                # 1) RSI oversold
                if bar["rsi"] < self.RSI_OVERSOLD:
                    long_count += 1
                    long_reasons.append(f"RSI={bar['rsi']:.1f}")

                # 2) Prix sous le VWAP
                if price < bar["vwap"]:
                    long_count += 1
                    long_reasons.append("below_VWAP")

                # 3) Prix sous la BB lower
                if price <= bar["bb_lower"]:
                    long_count += 1
                    long_reasons.append("below_BB_lower")

                # 4) Volume > 2x (confirmation du mouvement)
                if bar["vol_ratio"] > self.VOL_THRESHOLD:
                    long_count += 1
                    long_reasons.append(f"vol={bar['vol_ratio']:.1f}x")

                # ── Compter les indicateurs SHORT (bullish exhaustion) ──
                short_count = 0
                short_reasons = []

                # 1) RSI overbought
                if bar["rsi"] > self.RSI_OVERBOUGHT:
                    short_count += 1
                    short_reasons.append(f"RSI={bar['rsi']:.1f}")

                # 2) Prix au-dessus du VWAP
                if price > bar["vwap"]:
                    short_count += 1
                    short_reasons.append("above_VWAP")

                # 3) Prix au-dessus de la BB upper
                if price >= bar["bb_upper"]:
                    short_count += 1
                    short_reasons.append("above_BB_upper")

                # 4) Volume > 2x
                if bar["vol_ratio"] > self.VOL_THRESHOLD:
                    short_count += 1
                    short_reasons.append(f"vol={bar['vol_ratio']:.1f}x")

                # ── Generer le signal si confluence suffisante ──
                if long_count >= self.MIN_CONFLUENCE:
                    stop = price * (1 - self.STOP_PCT)
                    target = price * (1 + self.TARGET_PCT)
                    candidates.append({
                        "score": long_count,
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=price,
                            stop_loss=stop,
                            take_profit=target,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "confluence": long_count,
                                "reasons": ", ".join(long_reasons),
                                "rsi": round(bar["rsi"], 1),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    trades_today += 1
                    signal_found = True

                elif short_count >= self.MIN_CONFLUENCE:
                    stop = price * (1 + self.STOP_PCT)
                    target = price * (1 - self.TARGET_PCT)
                    candidates.append({
                        "score": short_count,
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=price,
                            stop_loss=stop,
                            take_profit=target,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "confluence": short_count,
                                "reasons": ", ".join(short_reasons),
                                "rsi": round(bar["rsi"], 1),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    trades_today += 1
                    signal_found = True

        # Trier par score de confluence et retourner les meilleurs
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
