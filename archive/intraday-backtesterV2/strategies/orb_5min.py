"""
Stratégie 1 : Opening Range Breakout (ORB) 5 minutes
Basée sur le paper Zarattini, Barbon & Aziz (2024)

Règles :
- Calcule high/low des 5 premières minutes (9:30-9:35 ET)
- LONG si breakout au-dessus du high + volume > 1.5x moyenne
- SHORT si breakdown sous le low + volume > 1.5x moyenne
- Stop-loss : extrémité opposée du range
- Take-profit : 2x le risque (R:R 1:2)
- Filtre "Stock in Play" : gap > 2% ou pre-market volume élevé
"""
import pandas as pd
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import orb_range, gap_pct, volume_ratio
import config


class ORB5MinStrategy(BaseStrategy):
    name = "ORB 5-Min Breakout"

    def __init__(self, rr_ratio: float = 2.0, gap_threshold: float = 2.0, vol_multiplier: float = 1.5):
        self.rr_ratio = rr_ratio
        self.gap_threshold = gap_threshold
        self.vol_multiplier = vol_multiplier

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            # ── Calculer le range des 5 premières minutes ──
            orb_bars = df.between_time("09:30", "09:34")
            if len(orb_bars) < 1:
                continue

            orb_high = orb_bars["high"].max()
            orb_low = orb_bars["low"].min()
            orb_vol = orb_bars["volume"].sum()
            orb_range_size = orb_high - orb_low

            if orb_range_size <= 0:
                continue

            # ── Filtre "Stock in Play" : gap d'ouverture ──
            day_open = df.iloc[0]["open"]
            # Comparer avec la veille — on utilise un proxy simple
            # (dans un vrai setup, on utiliserait les daily bars)
            prev_close = df.iloc[0].get("vwap", day_open)  # Fallback
            # Pour l'instant, on skip le filtre gap si pas de données daily
            # Ce filtre sera renforcé avec get_daily_bars()

            # ── Scanner les barres après le range pour breakout ──
            post_orb = df.between_time("09:35", "15:55")

            for ts, bar in post_orb.iterrows():
                avg_vol = df.loc[:ts, "volume"].rolling(20, min_periods=5).mean().iloc[-1] if len(df.loc[:ts]) > 5 else orb_vol

                # LONG breakout
                if bar["close"] > orb_high and bar["volume"] > avg_vol * self.vol_multiplier:
                    risk = orb_high - orb_low
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=orb_high,
                        stop_loss=orb_low,
                        take_profit=orb_high + risk * self.rr_ratio,
                        timestamp=ts,
                        metadata={"strategy": self.name, "orb_range": round(orb_range_size, 2)},
                    ))
                    break  # Un seul signal par ticker par jour

                # SHORT breakdown
                if bar["close"] < orb_low and bar["volume"] > avg_vol * self.vol_multiplier:
                    risk = orb_high - orb_low
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=orb_low,
                        stop_loss=orb_high,
                        take_profit=orb_low - risk * self.rr_ratio,
                        timestamp=ts,
                        metadata={"strategy": self.name, "orb_range": round(orb_range_size, 2)},
                    ))
                    break

        return signals
