"""
EU12 : Eurostoxx/SPY Mean Reversion Weekly

Edge structurel :
Quand les marches EU sous-performent significativement les US sur une
semaine (> 2%), il y a un effet de mean-reversion la semaine suivante.
Les flux institutionnels rebalancent vers l'actif sous-performant.

Implementation backtest :
- On mesure la performance hebdomadaire du DAX (via EXS1) vs sa moyenne
  historique (puisqu'on n'a pas SPY dans les donnees EU).
- Si le DAX a perdu > 2% sur la semaine precedente ET que la semaine
  d'avant etait positive (ce n'est pas un crash continu), on achete lundi
  et on vend vendredi.
- Alternative : on utilise la sur-reaction hebdomadaire pure. Si le DAX
  baisse > 2% en une semaine, achat lundi, vente vendredi.

Regles :
- Calcul : DAX weekly return (vendredi close vs vendredi precedent close)
- Si weekly return < -2% : achat lundi, vente vendredi
- SL = -2% depuis l'entree (drawdown max)
- TP = vendredi close
- Frequence cible : 10-15 trades / 6 mois
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── Parameters ──
WEEKLY_DROP_THRESHOLD = -0.02  # Semaine precedente < -2%
SL_PCT = 0.02                  # Stop-loss 2%
MAX_CONSECUTIVE_DROP = 3       # Si 3 semaines de baisse continue, pas de signal

# Target tickers
INDEX_TICKERS = ["EXS1"]       # DAX ETF
DIVERSIFIED_TICKERS = ["SAP", "ASML", "SIE"]  # Top EU stocks for diversification
ALL_TRADE_TICKERS = INDEX_TICKERS + DIVERSIFIED_TICKERS


class EUStoxxSPYReversionStrategy(EUBaseStrategy):
    name = "EU Stoxx Mean Reversion Weekly"

    def __init__(
        self,
        weekly_threshold: float = WEEKLY_DROP_THRESHOLD,
        sl_pct: float = SL_PCT,
    ):
        self.weekly_threshold = weekly_threshold
        self.sl_pct = sl_pct
        self._weekly_returns_cache = {}

    def get_required_tickers(self) -> list[str]:
        return ALL_TRADE_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        signals = []

        # Only trade on Mondays
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        if weekday != 0:  # Only Monday
            return signals

        # ── Check DAX (EXS1) weekly return ──
        ref_ticker = "EXS1"
        if ref_ticker not in data:
            # Try any available ticker
            ref_ticker = next((t for t in ALL_TRADE_TICKERS if t in data), None)
            if ref_ticker is None:
                return signals

        df = data[ref_ticker]

        # Get the full historical data for this ticker
        all_before = df[df.index < pd.Timestamp(date)] if not hasattr(df.index, 'date') else df[df.index.date < date]
        if len(all_before) < 10:
            return signals

        # ── Calculate last week's return ──
        # Find the last 5 trading days
        recent = all_before.tail(5)
        if len(recent) < 3:
            return signals

        week_start_price = recent.iloc[0]["open"]
        week_end_price = recent.iloc[-1]["close"]

        if week_start_price <= 0:
            return signals

        weekly_return = (week_end_price - week_start_price) / week_start_price

        if weekly_return >= self.weekly_threshold:
            return signals  # Not enough of a drop

        # ── Check for crash continuation (3 consecutive down weeks) ──
        if len(all_before) >= 15:
            prev_week = all_before.tail(10).head(5)
            if len(prev_week) >= 3:
                pw_return = (prev_week.iloc[-1]["close"] - prev_week.iloc[0]["open"]) / prev_week.iloc[0]["open"]
                prev_prev_week = all_before.tail(15).head(5)
                if len(prev_prev_week) >= 3:
                    ppw_return = (prev_prev_week.iloc[-1]["close"] - prev_prev_week.iloc[0]["open"]) / prev_prev_week.iloc[0]["open"]
                    if pw_return < -0.01 and ppw_return < -0.01:
                        return signals  # 3 consecutive down weeks = possible crash, skip

        # ── Generate signals for all available tickers ──
        for ticker in ALL_TRADE_TICKERS:
            if ticker not in data:
                continue

            t_df = data[ticker]
            today = self._get_today(t_df, date)
            if today is None:
                continue

            entry_price = today["open"]
            entry_ts = today.name if hasattr(today, 'name') else pd.Timestamp(date)

            stop_loss = entry_price * (1 - self.sl_pct)
            # TP = Friday close (handled by forced EOW exit in the runner)
            # For daily backtest, we hold 5 days
            take_profit = entry_price * (1 + 0.03)  # 3% target (generous for weekly hold)

            signals.append(EUSignal(
                action="LONG",
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "weekly_return_pct": round(weekly_return * 100, 2),
                    "hold_until": "friday_close",
                },
            ))

        return signals

    @staticmethod
    def _get_today(df, date):
        if hasattr(df.index, 'date'):
            today = df[df.index.date == date]
        else:
            today = df[df.index == pd.Timestamp(date)]
        return today.iloc[0] if not today.empty else None
