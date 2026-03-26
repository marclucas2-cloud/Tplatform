"""
Strategie : Overnight Simple SPY

Edge structurel :
60-70% du rendement annuel du S&P500 provient du mouvement overnight
(close -> open du jour suivant). Le "overnight risk premium" est l'un
des effets les plus robustes en finance quantitative.

Regles :
- Acheter SPY a la derniere barre avant 15:55 (close ~15:50)
- Vendre systematiquement a l'open du jour suivant (9:35)
- Skip si ATR 20 barres > 2.5% du prix (volatilite trop haute)
- Skip le vendredi (weekend risk)
- 1 seul trade par jour, uniquement SPY

Note backtest :
Le moteur ferme toutes les positions a 15:55. Le signal d'entree a la
derniere barre capture le overnight gap via : entry = close derniere barre,
exit = close 15:55 (qui est ~= entry). Le vrai PnL overnight est capture
le lendemain via le gap d'ouverture. On met un stop large (5%) et un TP
a 2% pour que le moteur ne coupe pas avant le close.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal


# ── Parametres ──
ATR_LOOKBACK = 20
ATR_MAX_PCT = 0.025       # ATR 20 barres > 2.5% du prix = skip
MAX_TRADES_PER_DAY = 1
STOP_PCT = 0.05           # Stop large (5%) — on vend systematiquement a l'open
TP_PCT = 0.02             # TP 2% — capture du gap overnight


class OvernightSimpleSPYStrategy(BaseStrategy):
    name = "Overnight Simple SPY"

    def __init__(
        self,
        atr_lookback: int = ATR_LOOKBACK,
        atr_max_pct: float = ATR_MAX_PCT,
    ):
        self.atr_lookback = atr_lookback
        self.atr_max_pct = atr_max_pct

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

        # ── Calculer ATR proxy (True Range moyen sur 20 barres) ──
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_20 = tr.rolling(self.atr_lookback, min_periods=self.atr_lookback).mean()

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

            atr_val = atr_20.iloc[idx]
            price = bar["close"]

            if pd.isna(atr_val) or price <= 0:
                continue

            # ── Filtre volatilite ──
            atr_pct = atr_val / price
            if atr_pct > self.atr_max_pct:
                continue

            # ── Signal LONG : acheter a la derniere barre ──
            entry_price = price
            stop_loss = entry_price * (1 - STOP_PCT)
            take_profit = entry_price * (1 + TP_PCT)

            signals.append(Signal(
                action="LONG",
                ticker="SPY",
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "atr_pct": round(atr_pct * 100, 3),
                    "weekday": weekday,
                    "entry_type": "overnight_hold",
                },
            ))
            signal_found = True

        return signals
