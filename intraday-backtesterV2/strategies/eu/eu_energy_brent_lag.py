"""
EU10 : Energy EU — Brent Lag

Edge structurel :
Quand le Brent (petrole) monte significativement mais que les majors
petrolieres europeennes (TotalEnergies, Shell) n'ont pas encore suivi,
il y a un rattrapage previsible.

Proxy dans le backtest :
- On n'a pas le Brent directement, mais on peut utiliser le spread
  entre TTE et SHEL comme indicateur de divergence.
- Signal : si une des deux monte > 0.5% a l'ouverture mais l'autre est
  plate ou en retard (< 0.2%), achat du retardataire.
- Alternative : si les deux gappent dans la meme direction > 0.5% avec
  volume eleve, c'est une continuation energy.

Regles :
- Lead stock gap > 0.5%, lag stock gap < 0.2%
- Achat du lag stock
- SL = 0.8%. TP = 1.2%
- Frequence cible : 20-35 trades / 6 mois
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── Parameters ──
LEAD_GAP_MIN = 0.005      # Leader gap > 0.5%
LAG_GAP_MAX = 0.002       # Lagger gap < 0.2%
SL_PCT = 0.008            # Stop-loss 0.8%
TP_PCT = 0.012            # Take-profit 1.2%
MIN_VOLUME_MULT = 1.0     # Volume au moins egal a la moyenne

# Energy pairs
ENERGY_TICKERS = ["TTE", "SHELL"]


class EUEnergyBrentLagStrategy(EUBaseStrategy):
    name = "EU Energy Brent Lag"

    def __init__(
        self,
        lead_gap_min: float = LEAD_GAP_MIN,
        lag_gap_max: float = LAG_GAP_MAX,
        sl_pct: float = SL_PCT,
        tp_pct: float = TP_PCT,
    ):
        self.lead_gap_min = lead_gap_min
        self.lag_gap_max = lag_gap_max
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct

    def get_required_tickers(self) -> list[str]:
        return ENERGY_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        signals = []

        # Need both energy stocks
        available = [t for t in ENERGY_TICKERS if t in data]
        if len(available) < 2:
            return signals

        # Compute gaps for each
        gaps = {}
        bars = {}
        for ticker in available:
            df = data[ticker]
            today = self._get_today(df, date)
            prev = self._get_prev(df, date)

            if today is None or prev is None:
                continue
            if prev["close"] <= 0:
                continue

            gap = (today["open"] - prev["close"]) / prev["close"]
            gaps[ticker] = gap
            bars[ticker] = {"today": today, "prev": prev, "df": df}

        if len(gaps) < 2:
            return signals

        # ── Identify leader and lagger ──
        sorted_by_gap = sorted(gaps.items(), key=lambda x: abs(x[1]), reverse=True)
        leader_ticker, leader_gap = sorted_by_gap[0]
        lagger_ticker, lagger_gap = sorted_by_gap[1]

        # Leader must gap significantly
        if abs(leader_gap) < self.lead_gap_min:
            return signals

        # Lagger must be lagging (small gap)
        if abs(lagger_gap) > self.lag_gap_max:
            return signals

        # Both must gap in same direction (or lagger flat)
        if leader_gap > 0 and lagger_gap < -self.lag_gap_max:
            return signals  # Opposite directions
        if leader_gap < 0 and lagger_gap > self.lag_gap_max:
            return signals

        # ── Volume check for leader ──
        leader_data = bars[leader_ticker]
        hist = leader_data["df"]
        prev_bars = hist[hist.index < leader_data["today"].name] if hasattr(leader_data["today"], 'name') else hist.iloc[:-1]
        if len(prev_bars) < 5:
            return signals
        avg_vol = prev_bars["volume"].tail(20).mean()
        if avg_vol > 0 and leader_data["today"]["volume"] < avg_vol * MIN_VOLUME_MULT:
            return signals

        # ── Trade the lagger in the same direction as the leader ──
        lagger_data = bars[lagger_ticker]
        entry_price = lagger_data["today"]["open"]
        entry_ts = lagger_data["today"].name if hasattr(lagger_data["today"], 'name') else pd.Timestamp(date)

        if leader_gap > 0:
            # Leader gapped UP, lagger is behind -> LONG lagger
            stop_loss = entry_price * (1 - self.sl_pct)
            take_profit = entry_price * (1 + self.tp_pct)
            action = "LONG"
        else:
            # Leader gapped DOWN, lagger is behind -> SHORT lagger
            stop_loss = entry_price * (1 + self.sl_pct)
            take_profit = entry_price * (1 - self.tp_pct)
            action = "SHORT"

        signals.append(EUSignal(
            action=action,
            ticker=lagger_ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=entry_ts,
            metadata={
                "strategy": self.name,
                "leader": leader_ticker,
                "leader_gap_pct": round(leader_gap * 100, 2),
                "lagger": lagger_ticker,
                "lagger_gap_pct": round(lagger_gap * 100, 2),
            },
        ))

        return signals

    @staticmethod
    def _get_today(df: pd.DataFrame, date) -> pd.Series:
        if hasattr(df.index, 'date'):
            today = df[df.index.date == date]
        else:
            today = df[df.index == pd.Timestamp(date)]
        return today.iloc[0] if not today.empty else None

    @staticmethod
    def _get_prev(df: pd.DataFrame, date) -> pd.Series:
        if hasattr(df.index, 'date'):
            prev = df[df.index.date < date]
        else:
            prev = df[df.index < pd.Timestamp(date)]
        return prev.iloc[-1] if not prev.empty else None
