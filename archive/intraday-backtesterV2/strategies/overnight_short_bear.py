"""
Strategie : Overnight Short Bear

Edge structurel :
Dans un marche baissier (SPY en-dessous de sa SMA 20 barres et journee rouge),
le overnight gap tend a etre negatif. On short SPY a 15:50 pour capturer le
mouvement baissier overnight (close -> open du jour suivant).

Regles :
- SPY doit etre down > 0.3% sur la journee (confirmation bear)
- SPY doit etre en-dessous de sa SMA 20 barres (trend baissier)
- Skip vendredi (weekend risk)
- Skip si ATR 20 barres > 2.5% du prix (trop volatile)
- Short a la derniere barre (15:50-15:55)
- Stop : entry * 1.05 (5% wide — systematique)
- Target : entry * 0.98 (2% down overnight)
- Max 1 trade/jour, uniquement SPY
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal


# ── Parametres ──
ATR_LOOKBACK = 20
ATR_MAX_PCT = 0.025       # ATR > 2.5% du prix = skip
MIN_DAY_DOWN_PCT = 0.003  # SPY doit etre down > 0.3%
SMA_LOOKBACK = 20         # SMA 20 barres sur 5M
STOP_PCT = 0.05           # Stop large (5%)
TP_PCT = 0.02             # Target 2% down
MAX_TRADES_PER_DAY = 1


class OvernightShortBearStrategy(BaseStrategy):
    name = "Overnight Short Bear"

    def __init__(
        self,
        atr_lookback: int = ATR_LOOKBACK,
        atr_max_pct: float = ATR_MAX_PCT,
        min_day_down_pct: float = MIN_DAY_DOWN_PCT,
    ):
        self.atr_lookback = atr_lookback
        self.atr_max_pct = atr_max_pct
        self.min_day_down_pct = min_day_down_pct

    def get_required_tickers(self) -> list[str]:
        return ["SPY"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── Skip vendredi (weekend risk) ──
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        if weekday == 4:  # Vendredi
            return signals

        # ── SPY requis ──
        if "SPY" not in data:
            return signals

        df = data["SPY"]
        if len(df) < self.atr_lookback + 5:
            return signals

        # ── Performance journaliere ──
        open_price = df.iloc[0]["open"]
        if open_price <= 0:
            return signals

        # ── ATR 20 barres (True Range proxy) ──
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_20 = tr.rolling(self.atr_lookback, min_periods=self.atr_lookback).mean()

        # ── SMA 20 barres ──
        sma_20 = close.rolling(SMA_LOOKBACK, min_periods=SMA_LOOKBACK).mean()

        # ── Chercher la derniere barre avant 15:55 ──
        late_bars = df.between_time("15:45", "15:54")
        if late_bars.empty:
            return signals

        signal_found = False

        for ts, bar in late_bars.iterrows():
            if signal_found:
                break

            idx = df.index.get_loc(ts)
            if idx < self.atr_lookback:
                continue

            price = bar["close"]
            if price <= 0:
                continue

            # ── Filtre : SPY doit etre down > 0.3% sur la journee ──
            day_return = (price - open_price) / open_price
            if day_return >= -self.min_day_down_pct:
                continue  # Pas assez baissier

            # ── Filtre : prix sous SMA 20 barres ──
            sma_val = sma_20.iloc[idx]
            if pd.isna(sma_val) or price >= sma_val:
                continue  # Pas en trend baissier

            # ── Filtre volatilite ──
            atr_val = atr_20.iloc[idx]
            if pd.isna(atr_val):
                continue
            atr_pct = atr_val / price
            if atr_pct > self.atr_max_pct:
                continue  # Trop volatile

            # ── Signal SHORT : vendre a la derniere barre ──
            entry_price = price
            stop_loss = entry_price * (1 + STOP_PCT)    # 5% au-dessus
            take_profit = entry_price * (1 - TP_PCT)    # 2% en-dessous

            signals.append(Signal(
                action="SHORT",
                ticker="SPY",
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "day_return_pct": round(day_return * 100, 3),
                    "atr_pct": round(atr_pct * 100, 3),
                    "sma_20": round(sma_val, 2),
                    "weekday": weekday,
                    "entry_type": "overnight_short_bear",
                },
            ))
            signal_found = True

        return signals
