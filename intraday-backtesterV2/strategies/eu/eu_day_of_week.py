"""
EU3 : Day-of-Week EU (DAX/CAC)

Edge structurel :
Anomalie calendaire documentee academiquement sur les marches EU :
- Lundi : biais negatif (weekend risk premium, Monday effect)
- Vendredi : biais positif (position squaring avant le weekend)

Cette anomalie est plus prononcee sur le DAX que sur le CAC.

ATTENTION: TP 0.3% - 0.13% couts x2 = 0.04% net par trade. Tres serre !
On elargit les seuils pour ne prendre que les setups les plus clairs.

Regles :
- Lundi : SHORT si open < prev close (deja en baisse)
  + volume normal (pas de gap excessif)
- Vendredi : LONG si open > prev close
- SL = 0.5%. TP = 0.4% (ajuste pour couvrir les couts)
- Filtre : pas de signal si gap > 0.8% (event override)
- Frequence cible : 30-50 trades / 6 mois
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── Parameters ──
SL_PCT = 0.005             # Stop-loss 0.5%
TP_PCT = 0.004             # Take-profit 0.4% (ajuste pour couts EU)
GAP_MAX_PCT = 0.008        # Gap max 0.8% (au-dela = event-driven, pas seasonal)
MIN_CHANGE_CONFIRM = 0.001 # Confirmation minimale direction (0.1%)

# Target tickers
EU_INDEX_TICKERS = ["EXS1"]  # DAX ETF
EU_STOCK_TICKERS = ["SAP", "SIE", "ALV"]  # Top DAX stocks
ALL_TICKERS = EU_INDEX_TICKERS + EU_STOCK_TICKERS


class EUDayOfWeekStrategy(EUBaseStrategy):
    name = "EU Day-of-Week Seasonal"

    def __init__(
        self,
        sl_pct: float = SL_PCT,
        tp_pct: float = TP_PCT,
        gap_max_pct: float = GAP_MAX_PCT,
    ):
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.gap_max_pct = gap_max_pct

    def get_required_tickers(self) -> list[str]:
        return ALL_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        signals = []

        # ── Determine weekday ──
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        is_monday = weekday == 0
        is_friday = weekday == 4

        if not is_monday and not is_friday:
            return signals

        traded = set()

        for ticker in ALL_TICKERS:
            if ticker not in data or ticker in traded:
                continue

            df = data[ticker]
            today = self._get_today(df, date)
            prev = self._get_prev(df, date)

            if today is None or prev is None:
                continue
            if prev["close"] <= 0:
                continue

            # ── Gap filter ──
            gap = (today["open"] - prev["close"]) / prev["close"]
            if abs(gap) > self.gap_max_pct:
                continue  # Event-driven day, skip seasonal

            entry_price = today["open"]
            entry_ts = today.name if hasattr(today, 'name') else pd.Timestamp(date)

            # ── Volatility filter: skip if recent vol too high ──
            hist = df[df.index < today.name] if hasattr(today, 'name') else df.iloc[:-1]
            if len(hist) >= 10:
                recent_returns = hist["close"].pct_change().tail(10).dropna()
                if len(recent_returns) > 0 and recent_returns.std() > 0.02:
                    continue  # High vol regime, seasonal unreliable

            if is_monday:
                # Monday SHORT — but only if open is already below prev close
                if gap > MIN_CHANGE_CONFIRM:
                    continue  # Gapping up on Monday = not a typical Monday
                stop_loss = entry_price * (1 + self.sl_pct)
                take_profit = entry_price * (1 - self.tp_pct)
                action = "SHORT"
                reason = "monday_short"

            else:  # Friday
                # Friday LONG — but only if open is above prev close
                if gap < -MIN_CHANGE_CONFIRM:
                    continue  # Gapping down on Friday = not a typical Friday
                stop_loss = entry_price * (1 - self.sl_pct)
                take_profit = entry_price * (1 + self.tp_pct)
                action = "LONG"
                reason = "friday_long"

            signals.append(EUSignal(
                action=action,
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "reason": reason,
                    "weekday": weekday,
                    "gap_pct": round(gap * 100, 2),
                },
            ))
            traded.add(ticker)

        return signals

    @staticmethod
    def _get_today(df, date):
        if hasattr(df.index, 'date'):
            today = df[df.index.date == date]
        else:
            today = df[df.index == pd.Timestamp(date)]
        return today.iloc[0] if not today.empty else None

    @staticmethod
    def _get_prev(df, date):
        if hasattr(df.index, 'date'):
            prev = df[df.index.date < date]
        else:
            prev = df[df.index < pd.Timestamp(date)]
        return prev.iloc[-1] if not prev.empty else None
