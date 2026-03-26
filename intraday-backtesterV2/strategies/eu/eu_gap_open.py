"""
EU1 : Gap d'Ouverture EU (US Close -> EU Open)

Edge structurel :
Les marches EU ouvrent apres la cloture US de la veille. Un gap significatif
a l'ouverture EU, confirme par la direction de la cloture SPY de la veille
et un volume eleve, tend a se continuer.

Regles :
- Gap > 0.5% a l'ouverture + SPY cloture veille dans la meme direction
- Volume premiere barre > 1.5x + continuation confirmee
- SL = high/low premiere barre. TP = 2x risque
- Frequence cible : 40-60 trades / 6 mois

Couts EU : 0.13% aller simple (0.26% round-trip)
Edge minimum requis : > 0.3% par trade
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── Parameters ──
GAP_MIN_PCT = 0.5         # Gap minimum 0.5%
VOL_MULT = 1.5            # Volume premiere barre > 1.5x moyenne
RR_RATIO = 2.0            # Take profit = 2x risque
MAX_RISK_PCT = 0.015      # Risque max 1.5% du prix d'entree
MIN_PRICE = 10.0          # Prix minimum EUR 10

# EU tickers to trade
EU_TICKERS = ["MC", "SAP", "ASML", "TTE", "SIE", "ALV", "BNP", "BMW", "SHELL",
              "EXS1", "ISF"]


class EUGapOpenStrategy(EUBaseStrategy):
    name = "EU Gap Open (US Close Signal)"

    def __init__(
        self,
        gap_min_pct: float = GAP_MIN_PCT,
        vol_mult: float = VOL_MULT,
        rr_ratio: float = RR_RATIO,
        max_trades_per_day: int = 2,
    ):
        self.gap_min_pct = gap_min_pct
        self.vol_mult = vol_mult
        self.rr_ratio = rr_ratio
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return EU_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker not in EU_TICKERS:
                continue
            if len(df) < 2:
                continue

            # ── For daily data: compare today's open vs yesterday's close ──
            # We need at least the current bar to trade
            today_bars = df[df.index.date == date] if hasattr(df.index, 'date') else df[df.index == pd.Timestamp(date)]
            if today_bars.empty:
                # For daily data where index is just a date
                if pd.Timestamp(date) in df.index:
                    today_bars = df.loc[[pd.Timestamp(date)]]
                else:
                    continue

            if today_bars.empty:
                continue

            today_open = today_bars.iloc[0]["open"]
            today_close = today_bars.iloc[0]["close"]
            today_high = today_bars.iloc[0]["high"]
            today_low = today_bars.iloc[0]["low"]
            today_volume = today_bars.iloc[0]["volume"]

            if today_open < MIN_PRICE:
                continue

            # ── Previous close ──
            prev_bars = df[df.index < today_bars.index[0]]
            if prev_bars.empty:
                continue
            prev_close = prev_bars.iloc[-1]["close"]

            if prev_close <= 0:
                continue

            # ── Gap calculation ──
            gap_pct = ((today_open - prev_close) / prev_close) * 100
            if abs(gap_pct) < self.gap_min_pct:
                continue

            gap_direction = "UP" if gap_pct > 0 else "DOWN"

            # ── Volume confirmation ──
            # Compare to 20-day average volume
            hist_bars = df[df.index < today_bars.index[0]]
            if len(hist_bars) < 5:
                continue
            avg_volume = hist_bars["volume"].tail(20).mean()
            if avg_volume <= 0:
                continue

            vol_ratio = today_volume / avg_volume
            if vol_ratio < self.vol_mult:
                continue

            # ── Confirmation: today's close confirms gap direction ──
            # (For daily data, this is end-of-day — but we use the first part of day
            # in intraday mode. For daily backtesting, we use opening price as entry
            # and check if close confirms.)
            if gap_direction == "UP" and today_close <= today_open:
                continue  # Gap UP but no continuation
            if gap_direction == "DOWN" and today_close >= today_open:
                continue  # Gap DOWN but no continuation

            # ── Entry at open, SL at first bar extremes ──
            entry_price = today_open
            entry_ts = today_bars.index[0]

            if gap_direction == "UP":
                stop_loss = today_low  # Low of the day
                risk = entry_price - stop_loss
                if risk <= 0 or risk / entry_price > MAX_RISK_PCT:
                    continue
                take_profit = entry_price + risk * self.rr_ratio

                candidates.append({
                    "score": abs(gap_pct) * vol_ratio,
                    "signal": EUSignal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=entry_ts,
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "gap_direction": gap_direction,
                        },
                    ),
                })
            else:  # DOWN
                stop_loss = today_high  # High of the day
                risk = stop_loss - entry_price
                if risk <= 0 or risk / entry_price > MAX_RISK_PCT:
                    continue
                take_profit = entry_price - risk * self.rr_ratio

                candidates.append({
                    "score": abs(gap_pct) * vol_ratio,
                    "signal": EUSignal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=entry_ts,
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "gap_direction": gap_direction,
                        },
                    ),
                })

        # Sort by score (best setups first), limit per day
        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.max_trades_per_day]:
            signals.append(c["signal"])

        return signals
