"""
EU13 : EU Close -> US Open Signal (trade sur SPY via Alpaca !)

Edge structurel :
Quand le DAX cloture en forte hausse (> 1%) a 17:30 CET mais que SPY
est encore flat a 11:30 ET (meme heure), il y a un momentum transatlantique
qui n'est pas encore price-in par le marche US.

Implementation backtest :
- On utilise les donnees daily du DAX (via EXS1) et SPY.
- Signal : DAX close hausse > 1% sur la journee, SPY variation < 0.3% a ce moment.
- En live, on acheterait SPY de 12:00 a 15:55 ET via Alpaca.
- Pour le backtest, on simule en regardant le rendement SPY de l'open au close.

Regles :
- DAX journee hausse > 1% (close vs prev close)
- SPY variation < 0.3% entre son open et 12:00 ET
- Achat SPY. SL = 0.4%. TP = 0.6%
- Frequence cible : 15-25 trades / 6 mois

NOTE: En production, ce trade serait execute via Alpaca (SPY US).
Le backtest utilise les donnees daily pour simuler.
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── Parameters ──
DAX_MIN_CHANGE = 0.01     # DAX journee > 1%
SPY_MAX_CHANGE = 0.003    # SPY variation < 0.3% (flat)
SL_PCT = 0.004            # Stop-loss 0.4%
TP_PCT = 0.006            # Take-profit 0.6%

# Required tickers
INDEX_TICKER = "EXS1"     # DAX ETF as proxy
# SPY would be traded via Alpaca in live — in backtest, simulated here


class EUCloseUSOpenStrategy(EUBaseStrategy):
    name = "EU Close US Open Signal"

    def __init__(
        self,
        dax_min_change: float = DAX_MIN_CHANGE,
        spy_max_change: float = SPY_MAX_CHANGE,
        sl_pct: float = SL_PCT,
        tp_pct: float = TP_PCT,
    ):
        self.dax_min_change = dax_min_change
        self.spy_max_change = spy_max_change
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct

    def get_required_tickers(self) -> list[str]:
        return [INDEX_TICKER]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        signals = []

        if INDEX_TICKER not in data:
            return signals

        df = data[INDEX_TICKER]
        today = self._get_today(df, date)
        prev = self._get_prev(df, date)

        if today is None or prev is None:
            return signals
        if prev["close"] <= 0:
            return signals

        # ── DAX daily change ──
        dax_change = (today["close"] - prev["close"]) / prev["close"]

        # DAX must have a strong positive day (> 1%)
        if dax_change < self.dax_min_change:
            return signals

        # ── Simulate SPY flat condition ──
        # In the backtest, we can't directly check SPY intraday from EU data.
        # We use DAX intraday behavior as a proxy:
        # If DAX gained > 1% AND the move happened gradually (not just at open),
        # it's more likely SPY hasn't fully reacted.
        # Proxy: check if open-to-close accounts for most of the move.
        open_to_close = (today["close"] - today["open"]) / today["open"]
        if open_to_close < self.dax_min_change * 0.5:
            # The move was mostly gap (already priced in overnight) — less signal
            return signals

        # ── Entry signal on the DAX ETF itself (proxy for SPY) ──
        # In live: we'd buy SPY via Alpaca at 12:00 ET
        # In backtest: we buy EXS1 at the close (captures similar momentum)
        entry_price = today["close"]
        entry_ts = today.name if hasattr(today, 'name') else pd.Timestamp(date)

        # For the backtest, we can't hold overnight (intraday only)
        # So we use a tighter TP/SL and simulate same-day execution
        stop_loss = entry_price * (1 - self.sl_pct)
        take_profit = entry_price * (1 + self.tp_pct)

        # Alternative: simulate the NEXT DAY's return on DAX
        # (which is a better proxy for "US afternoon + next day EU open")
        next_day = self._get_next_day(df, date)
        if next_day is not None:
            # Use next day's open as entry (momentum continuation)
            entry_price = next_day["open"]
            entry_ts = next_day.name if hasattr(next_day, 'name') else pd.Timestamp(date)
            stop_loss = entry_price * (1 - self.sl_pct)
            take_profit = entry_price * (1 + self.tp_pct)
        else:
            return signals  # No next day data

        signals.append(EUSignal(
            action="LONG",
            ticker=INDEX_TICKER,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=entry_ts,
            metadata={
                "strategy": self.name,
                "dax_change_pct": round(dax_change * 100, 2),
                "open_to_close_pct": round(open_to_close * 100, 2),
                "note": "In live: trade SPY via Alpaca 12:00-15:55 ET",
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

    @staticmethod
    def _get_prev(df, date):
        if hasattr(df.index, 'date'):
            prev = df[df.index.date < date]
        else:
            prev = df[df.index < pd.Timestamp(date)]
        return prev.iloc[-1] if not prev.empty else None

    @staticmethod
    def _get_next_day(df, date):
        if hasattr(df.index, 'date'):
            nxt = df[df.index.date > date]
        else:
            nxt = df[df.index > pd.Timestamp(date)]
        return nxt.iloc[0] if not nxt.empty else None
