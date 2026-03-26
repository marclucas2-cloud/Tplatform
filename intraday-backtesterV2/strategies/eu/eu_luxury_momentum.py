"""
EU6 : Luxury Sector Momentum (LVMH, Hermes)

Edge structurel :
Le secteur luxe europeen est fortement correle avec la demande asiatique
(Chine = ~35% du CA luxe mondial). Quand les futures US sont en hausse
overnight ET que le Hang Seng a cloture en hausse > 0.5%, les stocks luxe
EU ouvrent avec un momentum positif exploitable.

Proxy dans le backtest :
- Comme on n'a pas les futures US ni le Hang Seng directement dans IBKR EU,
  on utilise un proxy : si le DAX ouvre en gap > 0.3% (sentiment positif overnight)
  ET que LVMH (MC) ouvre en gap > 0.3% avec volume > 1.3x, c'est un signal LONG.

Regles :
- DAX (via EXS1 ETF) gap > 0.3% (proxy futures US haussiers + Asie positive)
- LVMH gap > 0.3% + volume > 1.3x
- SL = 0.8%. TP = 1.5% ou sortie a 12:00 CET
- Frequence cible : 15-25 trades / 6 mois

Couts : 0.26% round-trip. Edge net cible : > 1.0% par trade.
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── Parameters ──
INDEX_GAP_MIN = 0.003     # DAX/EXS1 gap > 0.3%
STOCK_GAP_MIN = 0.003     # LVMH gap > 0.3%
VOL_MULT = 1.3            # Volume > 1.3x average
SL_PCT = 0.008            # Stop-loss 0.8%
TP_PCT = 0.015            # Take-profit 1.5%

# Luxury tickers (LVMH is the main play)
LUXURY_TICKERS = ["MC"]   # LVMH on SBF
INDEX_PROXY = "EXS1"      # DAX ETF as market proxy


class EULuxuryMomentumStrategy(EUBaseStrategy):
    name = "EU Luxury Sector Momentum"

    def __init__(
        self,
        index_gap_min: float = INDEX_GAP_MIN,
        stock_gap_min: float = STOCK_GAP_MIN,
        vol_mult: float = VOL_MULT,
        sl_pct: float = SL_PCT,
        tp_pct: float = TP_PCT,
    ):
        self.index_gap_min = index_gap_min
        self.stock_gap_min = stock_gap_min
        self.vol_mult = vol_mult
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct

    def get_required_tickers(self) -> list[str]:
        return LUXURY_TICKERS + [INDEX_PROXY]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        signals = []

        # ── Check index proxy gap (DAX ETF) ──
        if INDEX_PROXY not in data:
            return signals

        idx_df = data[INDEX_PROXY]
        idx_today = self._get_today(idx_df, date)
        idx_prev = self._get_prev(idx_df, date)

        if idx_today is None or idx_prev is None:
            return signals

        idx_gap = (idx_today["open"] - idx_prev["close"]) / idx_prev["close"]

        # Index must gap UP > threshold (positive overnight sentiment)
        if idx_gap < self.index_gap_min:
            return signals

        # ── Check luxury stocks ──
        for ticker in LUXURY_TICKERS:
            if ticker not in data:
                continue

            df = data[ticker]
            today = self._get_today(df, date)
            prev = self._get_prev(df, date)

            if today is None or prev is None:
                continue

            # Stock gap
            stock_gap = (today["open"] - prev["close"]) / prev["close"]
            if stock_gap < self.stock_gap_min:
                continue

            # Volume confirmation
            hist = df[df.index < today.name] if hasattr(today, 'name') else df.iloc[:-1]
            if len(hist) < 5:
                continue
            avg_vol = hist["volume"].tail(20).mean()
            if avg_vol <= 0:
                continue

            vol_ratio = today["volume"] / avg_vol
            if vol_ratio < self.vol_mult:
                continue

            # ── Entry at open ──
            entry_price = today["open"]
            entry_ts = today.name if hasattr(today, 'name') else pd.Timestamp(date)
            stop_loss = entry_price * (1 - self.sl_pct)
            take_profit = entry_price * (1 + self.tp_pct)

            signals.append(EUSignal(
                action="LONG",
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "idx_gap_pct": round(idx_gap * 100, 2),
                    "stock_gap_pct": round(stock_gap * 100, 2),
                    "vol_ratio": round(vol_ratio, 2),
                },
            ))

        return signals

    @staticmethod
    def _get_today(df: pd.DataFrame, date) -> pd.Series:
        """Get today's bar."""
        if hasattr(df.index, 'date'):
            today = df[df.index.date == date]
        else:
            today = df[df.index == pd.Timestamp(date)]
        if today.empty:
            return None
        return today.iloc[0]

    @staticmethod
    def _get_prev(df: pd.DataFrame, date) -> pd.Series:
        """Get previous day's last bar."""
        if hasattr(df.index, 'date'):
            prev = df[df.index.date < date]
        else:
            prev = df[df.index < pd.Timestamp(date)]
        if prev.empty:
            return None
        return prev.iloc[-1]
