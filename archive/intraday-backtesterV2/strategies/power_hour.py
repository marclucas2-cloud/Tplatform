"""
Stratégie 5 : Power Hour Momentum (15:00-16:00 ET)
La dernière heure concentre du flow institutionnel directionnel.

Règles :
- À 15:00, identifie les actions en hausse/baisse > 2% sur la journée
- Breakout du high/low de 14:30-15:00 → momentum continuation
- Exit : Market On Close (15:59)
- Max 5 positions simultanées
"""
import pandas as pd
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
import config


class PowerHourStrategy(BaseStrategy):
    name = "Power Hour Momentum"

    def __init__(self, min_day_move_pct: float = 2.0, stop_pct: float = 0.005):
        self.min_day_move = min_day_move_pct
        self.stop_pct = stop_pct

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker in [config.BENCHMARK, "QQQ"]:
                continue

            # Performance du jour à 15:00
            morning = df.between_time("09:30", "09:31")
            afternoon = df.between_time("14:55", "15:05")

            if morning.empty or afternoon.empty:
                continue

            day_open = morning.iloc[0]["open"]
            price_at_3pm = afternoon.iloc[0]["close"]
            day_pct = ((price_at_3pm - day_open) / day_open) * 100

            if abs(day_pct) < self.min_day_move:
                continue

            # Range de consolidation 14:30-15:00
            consol = df.between_time("14:30", "14:59")
            if consol.empty:
                continue

            consol_high = consol["high"].max()
            consol_low = consol["low"].min()

            # Volume croissant ?
            vol_first_half = df.between_time("09:30", "12:30")["volume"].sum() if not df.between_time("09:30", "12:30").empty else 1
            vol_afternoon = df.between_time("12:30", "15:00")["volume"].sum() if not df.between_time("12:30", "15:00").empty else 0
            vol_increasing = vol_afternoon > vol_first_half * 0.4  # Au moins 40% du matin

            candidates.append({
                "ticker": ticker,
                "day_pct": day_pct,
                "consol_high": consol_high,
                "consol_low": consol_low,
                "price_at_3pm": price_at_3pm,
                "vol_increasing": vol_increasing,
            })

        # Trier par force du mouvement, prendre les top 5
        candidates.sort(key=lambda x: abs(x["day_pct"]), reverse=True)
        candidates = candidates[:config.MAX_SIMULTANEOUS]

        for c in candidates:
            # Scanner les barres 15:00-15:55 pour breakout
            ticker = c["ticker"]
            if ticker not in data:
                continue

            power_hour = data[ticker].between_time("15:00", "15:55")
            if power_hour.empty:
                continue

            for ts, bar in power_hour.iterrows():
                if c["day_pct"] > 0:
                    # Momentum haussier → breakout au-dessus du range
                    if bar["close"] > c["consol_high"]:
                        signals.append(Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 - self.stop_pct),
                            take_profit=bar["close"] * (1 + self.stop_pct * 2),  # EOD close anyway
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "day_move_pct": round(c["day_pct"], 2),
                                "vol_increasing": c["vol_increasing"],
                            },
                        ))
                        break
                else:
                    # Momentum baissier → breakdown sous le range
                    if bar["close"] < c["consol_low"]:
                        signals.append(Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 + self.stop_pct),
                            take_profit=bar["close"] * (1 - self.stop_pct * 2),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "day_move_pct": round(c["day_pct"], 2),
                                "vol_increasing": c["vol_increasing"],
                            },
                        ))
                        break

        return signals
