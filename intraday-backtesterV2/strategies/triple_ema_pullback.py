"""
Strategie : Triple EMA Pullback

Edge structurel :
Quand les EMA 8/13/21 sont alignees (toutes ascendantes ou descendantes),
le trend est fort. Un pullback vers l'EMA 8 offre une re-entree dans le trend
a moindre risque. Stop sous l'EMA 21. Target 1.5%.

Iteration barre par barre.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class TripleEMAPullbackStrategy(BaseStrategy):
    name = "Triple EMA Pullback"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    EMA_FAST = 8
    EMA_MID = 13
    EMA_SLOW = 21
    TARGET_PCT = 0.015          # 1.5% target
    STOP_BUFFER_PCT = 0.002     # Buffer au-dela de EMA 21
    PULLBACK_TOLERANCE = 0.001  # Prix doit etre a 0.1% de EMA 8
    VOL_RATIO_MIN = 1.0
    MIN_EMA_SPREAD = 0.001      # EMAs doivent etre espacees de >0.1%

    # Top liquid tickers pour le live
    LIVE_TICKERS = [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
        "NFLX", "AVGO", "CRM", "ORCL", "QCOM", "INTC", "BA",
        "JPM", "GS", "MS", "BAC", "C",
        "XOM", "CVX", "COP", "OXY",
        "SPY", "QQQ", "IWM", "DIA",
        "COIN", "MARA", "MSTR",
    ]

    def get_required_tickers(self) -> list[str]:
        return self.LIVE_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < self.EMA_SLOW + 10:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()

            # Calculer les 3 EMAs
            df["ema_fast"] = df["close"].ewm(span=self.EMA_FAST, adjust=False).mean()
            df["ema_mid"] = df["close"].ewm(span=self.EMA_MID, adjust=False).mean()
            df["ema_slow"] = df["close"].ewm(span=self.EMA_SLOW, adjust=False).mean()
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre (10:00-15:30)
            tradeable = df.between_time("10:00", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if pd.isna(bar["ema_slow"]):
                    continue

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                ema_f = bar["ema_fast"]
                ema_m = bar["ema_mid"]
                ema_s = bar["ema_slow"]
                entry_price = bar["close"]

                # Verifier le spread minimum entre les EMAs
                ema_spread = abs(ema_f - ema_s) / ema_s
                if ema_spread < self.MIN_EMA_SPREAD:
                    continue

                # === BULLISH : EMAs alignees haussier (fast > mid > slow) ===
                if ema_f > ema_m > ema_s:
                    # Verifier que les EMAs sont ascendantes
                    idx = df.index.get_loc(ts)
                    if idx < 3:
                        continue
                    prev_ema_f = df["ema_fast"].iloc[idx - 2]
                    prev_ema_s = df["ema_slow"].iloc[idx - 2]
                    if pd.isna(prev_ema_f) or pd.isna(prev_ema_s):
                        continue
                    if ema_f <= prev_ema_f or ema_s <= prev_ema_s:
                        continue  # EMAs ne sont pas ascendantes

                    # Pullback : prix touche l'EMA 8 (ou juste en dessous)
                    pullback_zone_upper = ema_f * (1 + self.PULLBACK_TOLERANCE)
                    pullback_zone_lower = ema_f * (1 - 0.005)  # Max 0.5% sous EMA 8

                    if pullback_zone_lower <= entry_price <= pullback_zone_upper:
                        stop_loss = ema_s * (1 - self.STOP_BUFFER_PCT)
                        take_profit = entry_price * (1 + self.TARGET_PCT)

                        risk = (entry_price - stop_loss) / entry_price
                        if risk > 0.03 or risk <= 0:
                            continue

                        candidates.append({
                            "score": ema_spread,
                            "signal": Signal(
                                action="LONG",
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                timestamp=ts,
                                metadata={
                                    "strategy": self.name,
                                    "direction": "bullish",
                                    "ema_spread_pct": round(ema_spread * 100, 2),
                                    "vol_ratio": round(bar["vol_ratio"], 1),
                                },
                            ),
                        })
                        signal_found = True
                        continue

                # === BEARISH : EMAs alignees baissier (fast < mid < slow) ===
                if ema_f < ema_m < ema_s:
                    idx = df.index.get_loc(ts)
                    if idx < 3:
                        continue
                    prev_ema_f = df["ema_fast"].iloc[idx - 2]
                    prev_ema_s = df["ema_slow"].iloc[idx - 2]
                    if pd.isna(prev_ema_f) or pd.isna(prev_ema_s):
                        continue
                    if ema_f >= prev_ema_f or ema_s >= prev_ema_s:
                        continue  # EMAs ne sont pas descendantes

                    # Pullback : prix remonte vers l'EMA 8
                    pullback_zone_lower = ema_f * (1 - self.PULLBACK_TOLERANCE)
                    pullback_zone_upper = ema_f * (1 + 0.005)

                    if pullback_zone_lower <= entry_price <= pullback_zone_upper:
                        stop_loss = ema_s * (1 + self.STOP_BUFFER_PCT)
                        take_profit = entry_price * (1 - self.TARGET_PCT)

                        risk = (stop_loss - entry_price) / entry_price
                        if risk > 0.03 or risk <= 0:
                            continue

                        candidates.append({
                            "score": ema_spread,
                            "signal": Signal(
                                action="SHORT",
                                ticker=ticker,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                timestamp=ts,
                                metadata={
                                    "strategy": self.name,
                                    "direction": "bearish",
                                    "ema_spread_pct": round(ema_spread * 100, 2),
                                    "vol_ratio": round(bar["vol_ratio"], 1),
                                },
                            ),
                        })
                        signal_found = True

        # Trier par spread EMA (plus grand spread = trend plus fort)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
