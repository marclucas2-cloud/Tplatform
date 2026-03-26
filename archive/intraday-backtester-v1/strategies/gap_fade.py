"""
Stratégie 3 : Gap Fade (Contrarian)
Les gaps d'ouverture excessifs tendent à se refermer partiellement.

Règles :
- Gap up > 1.5% + 3 premières bougies 5-min baissières → SHORT
- Gap down > 1.5% + 3 premières bougies 5-min haussières → LONG
- Target : 50% de la fermeture du gap
- Stop-loss : nouveau high/low post-ouverture
- Max durée : 2 heures
"""
import pandas as pd
from datetime import time as dt_time, timedelta
from backtest_engine import BaseStrategy, Signal
import config


class GapFadeStrategy(BaseStrategy):
    name = "Gap Fade"

    def __init__(self, min_gap_pct: float = 1.5, gap_close_target: float = 0.5):
        self.min_gap_pct = min_gap_pct
        self.gap_close_target = gap_close_target
        self._prev_closes = {}  # ticker -> previous day close

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK or len(df) < 20:
                continue

            today_open = df.iloc[0]["open"]

            # Calculer le gap
            prev_close = self._prev_closes.get(ticker)
            self._prev_closes[ticker] = df.iloc[-1]["close"]  # Update pour demain

            if prev_close is None:
                continue

            gap_pct = ((today_open - prev_close) / prev_close) * 100

            if abs(gap_pct) < self.min_gap_pct:
                continue

            # Vérifier les 3 premières bougies 5-min (9:30-9:45)
            first_bars = df.between_time("09:30", "09:44")
            if len(first_bars) < 3:
                # Resample en 5-min si on a du 1-min
                first_bars_5m = df.between_time("09:30", "09:44").resample("5min").agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"
                }).dropna()
                if len(first_bars_5m) < 3:
                    continue
                first_bars = first_bars_5m

            # Vérifier direction des 3 premières bougies
            closes = first_bars["close"].values[:3]
            opens = first_bars["open"].values[:3]

            if gap_pct > self.min_gap_pct:
                # Gap UP — on cherche 3 bougies baissières pour fader
                bearish_count = sum(1 for c, o in zip(closes, opens) if c < o)
                if bearish_count >= 2:  # Au moins 2 sur 3 baissières
                    entry_price = first_bars.iloc[-1]["close"]
                    post_open_high = first_bars["high"].max()
                    gap_size = today_open - prev_close
                    target = today_open - gap_size * self.gap_close_target

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=post_open_high * 1.001,  # Légèrement au-dessus
                        take_profit=target,
                        timestamp=first_bars.index[-1],
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct, 2),
                            "gap_direction": "UP",
                        },
                    ))

            elif gap_pct < -self.min_gap_pct:
                # Gap DOWN — on cherche 3 bougies haussières pour fader
                bullish_count = sum(1 for c, o in zip(closes, opens) if c > o)
                if bullish_count >= 2:
                    entry_price = first_bars.iloc[-1]["close"]
                    post_open_low = first_bars["low"].min()
                    gap_size = prev_close - today_open
                    target = today_open + gap_size * self.gap_close_target

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=post_open_low * 0.999,
                        take_profit=target,
                        timestamp=first_bars.index[-1],
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct, 2),
                            "gap_direction": "DOWN",
                        },
                    ))

        return signals
