"""
Strategie : VWAP Trend Day

Edge structurel :
Les "trend days" sont des jours ou le prix reste d'un seul cote du VWAP
presque toute la journee (~20% des jours). Sur ces journees, les pullbacks
vers le VWAP offrent des entries a haute probabilite dans la direction du trend.

Regles :
- Apres 11:00 ET, verifier que le prix est reste du meme cote du VWAP
  depuis 9:45 (au moins 80% des barres)
- Entry quand le prix pullback a moins de 0.2% du VWAP
- Trade dans la direction du trend (LONG si prix au-dessus, SHORT si en-dessous)
- Stop : VWAP cross de 0.3% de l'autre cote
- Target : 0.8% dans la direction du trend
- Max 3 trades/jour, prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap
import config


LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
}

MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
TREND_CONSISTENCY_PCT = 0.80   # 80% des barres du meme cote
PULLBACK_DISTANCE_PCT = 0.002  # Prix within 0.2% du VWAP
STOP_CROSS_PCT = 0.003         # Stop si prix cross VWAP de 0.3%
TARGET_PCT = 0.008             # Target 0.8%


class VWAPTrendDayStrategy(BaseStrategy):
    name = "VWAP Trend Day"

    def __init__(
        self,
        trend_consistency: float = TREND_CONSISTENCY_PCT,
        pullback_distance: float = PULLBACK_DISTANCE_PCT,
        stop_cross_pct: float = STOP_CROSS_PCT,
        target_pct: float = TARGET_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.trend_consistency = trend_consistency
        self.pullback_distance = pullback_distance
        self.stop_cross_pct = stop_cross_pct
        self.target_pct = target_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 30:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # Calculer VWAP
            df = df.copy()
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            cum_tp_vol = (typical_price * df["volume"]).cumsum()
            cum_vol = df["volume"].cumsum()
            df["vwap_calc"] = cum_tp_vol / cum_vol.replace(0, np.nan)

            # ── Verifier la consistance du trend (9:45-11:00) ──
            morning_bars = df.between_time("09:45", "10:59")
            if len(morning_bars) < 10:
                continue

            above_vwap = 0
            below_vwap = 0
            for _, bar in morning_bars.iterrows():
                v = bar.get("vwap_calc", np.nan)
                if pd.isna(v) or v <= 0:
                    continue
                if bar["close"] > v:
                    above_vwap += 1
                elif bar["close"] < v:
                    below_vwap += 1

            total_bars = above_vwap + below_vwap
            if total_bars < 8:
                continue

            # Determiner si c'est un trend day
            if above_vwap / total_bars >= self.trend_consistency:
                trend_direction = "LONG"
            elif below_vwap / total_bars >= self.trend_consistency:
                trend_direction = "SHORT"
            else:
                continue  # Pas un trend day

            # ── Chercher des pullbacks vers VWAP apres 11:00 ──
            afternoon_bars = df.between_time("11:00", "15:30")
            if afternoon_bars.empty:
                continue

            signal_found = False

            for ts, bar in afternoon_bars.iterrows():
                if signal_found:
                    break

                v = bar.get("vwap_calc", np.nan)
                if pd.isna(v) or v <= 0:
                    continue

                distance_to_vwap = (bar["close"] - v) / v

                # Pullback : prix se rapproche du VWAP mais reste du bon cote
                if trend_direction == "LONG":
                    # Prix doit etre au-dessus du VWAP mais proche (pullback)
                    if 0 < distance_to_vwap < self.pullback_distance:
                        entry_price = bar["close"]
                        stop_loss = v * (1 - self.stop_cross_pct)
                        take_profit = entry_price * (1 + self.target_pct)

                        risk = entry_price - stop_loss
                        reward = take_profit - entry_price
                        if risk > 0 and reward > 0:
                            candidates.append({
                                "score": above_vwap / total_bars,
                                "signal": Signal(
                                    action="LONG",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "trend_consistency": round(above_vwap / total_bars, 2),
                                        "vwap_distance_pct": round(distance_to_vwap * 100, 3),
                                        "trend_direction": trend_direction,
                                    },
                                ),
                            })
                            signal_found = True

                elif trend_direction == "SHORT":
                    # Prix doit etre en-dessous du VWAP mais proche (pullback)
                    if -self.pullback_distance < distance_to_vwap < 0:
                        entry_price = bar["close"]
                        stop_loss = v * (1 + self.stop_cross_pct)
                        take_profit = entry_price * (1 - self.target_pct)

                        risk = stop_loss - entry_price
                        reward = entry_price - take_profit
                        if risk > 0 and reward > 0:
                            candidates.append({
                                "score": below_vwap / total_bars,
                                "signal": Signal(
                                    action="SHORT",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "trend_consistency": round(below_vwap / total_bars, 2),
                                        "vwap_distance_pct": round(distance_to_vwap * 100, 3),
                                        "trend_direction": trend_direction,
                                    },
                                ),
                            })
                            signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
