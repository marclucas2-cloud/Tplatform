"""
SHORT-5 : Cross-Asset Risk-Off Confirmation

Edge : Quand GLD ET TLT montent simultanement de > 0.3% avant 11:00 ET,
c'est un double signal risk-off tres fiable. Les investisseurs fuient vers
les valeurs refuges (or + obligations) en meme temps.
On short le high-beta le plus faible l'apres-midi (12:00-15:00).

Regles :
- GLD ET TLT > +0.3% depuis l'open avant 11:00 ET
- Shorter le high-beta qui decline le plus (TSLA, NVDA, AMD, COIN, MARA)
- Entry entre 12:00 et 15:00 ET
- SL = 1.0%. TP = 2.0% ou 15:00 close.
- Si GLD et TLT pas dans la meme direction risk-off = skip
- Max 1 position par jour
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# Signal tickers (regime detection) vs trade targets
SIGNAL_TICKERS = ["GLD", "TLT"]
TRADE_TARGETS = ["TSLA", "NVDA", "AMD", "COIN", "MARA"]

# Thresholds
RISKOFF_MIN_MOVE = 0.003      # GLD and TLT both > +0.3%
STOP_PCT = 0.010              # 1.0%
TARGET_PCT = 0.020            # 2.0%
SIGNAL_WINDOW_START = dt_time(9, 35)
SIGNAL_WINDOW_END = dt_time(11, 0)
ENTRY_WINDOW_START = dt_time(12, 0)
ENTRY_WINDOW_END = dt_time(15, 0)
MAX_TRADES_PER_DAY = 1


class CrossAssetRiskOffShortStrategy(BaseStrategy):
    name = "Cross-Asset Risk-Off Short"

    def get_required_tickers(self) -> list[str]:
        return SIGNAL_TICKERS + TRADE_TARGETS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        # Need both GLD and TLT for double confirmation
        if "GLD" not in data or "TLT" not in data:
            return []

        gld_df = data["GLD"]
        tlt_df = data["TLT"]

        if len(gld_df) < 10 or len(tlt_df) < 10:
            return []

        # Open prices for the day
        gld_open = gld_df.iloc[0]["open"]
        tlt_open = tlt_df.iloc[0]["open"]

        if gld_open <= 0 or tlt_open <= 0:
            return []

        # ── Phase 1: Detect risk-off regime before 11:00 ET ──
        riskoff_confirmed = False

        gld_signal_bars = gld_df.between_time(
            SIGNAL_WINDOW_START.strftime("%H:%M"),
            SIGNAL_WINDOW_END.strftime("%H:%M"),
        )

        for ts, gld_bar in gld_signal_bars.iterrows():
            gld_move = (gld_bar["close"] - gld_open) / gld_open

            # Find TLT bar at same timestamp
            tlt_at_ts = tlt_df[tlt_df.index <= ts]
            if tlt_at_ts.empty:
                continue
            tlt_bar = tlt_at_ts.iloc[-1]
            tlt_move = (tlt_bar["close"] - tlt_open) / tlt_open

            # Double confirmation: BOTH GLD and TLT up > threshold
            if gld_move >= RISKOFF_MIN_MOVE and tlt_move >= RISKOFF_MIN_MOVE:
                riskoff_confirmed = True
                break

        if not riskoff_confirmed:
            return []

        # ── Phase 2: Find best short target in the afternoon (12:00-15:00) ──
        signals = []
        best_target = None
        best_decline = 0.0

        for target_ticker in TRADE_TARGETS:
            if target_ticker not in data:
                continue

            target_df = data[target_ticker]
            if len(target_df) < 20:
                continue

            target_open = target_df.iloc[0]["open"]
            if target_open <= 0:
                continue

            # Look at afternoon bars
            afternoon_bars = target_df.between_time(
                ENTRY_WINDOW_START.strftime("%H:%M"),
                ENTRY_WINDOW_END.strftime("%H:%M"),
            )
            if afternoon_bars.empty:
                continue

            # Use the first afternoon bar for entry
            first_bar = afternoon_bars.iloc[0]
            target_move = (first_bar["close"] - target_open) / target_open

            # Target must be declining (confirming risk-off)
            if target_move >= 0:
                continue

            decline = abs(target_move)
            if decline > best_decline:
                best_decline = decline
                best_target = {
                    "ticker": target_ticker,
                    "price": first_bar["close"],
                    "move_pct": target_move,
                    "timestamp": afternoon_bars.index[0],
                }

        if best_target is not None:
            entry_price = best_target["price"]
            stop = entry_price * (1 + STOP_PCT)
            target = entry_price * (1 - TARGET_PCT)

            signals.append(Signal(
                action="SHORT",
                ticker=best_target["ticker"],
                entry_price=entry_price,
                stop_loss=stop,
                take_profit=target,
                timestamp=best_target["timestamp"],
                metadata={
                    "strategy": self.name,
                    "gld_riskoff": True,
                    "tlt_riskoff": True,
                    "target_move_pct": round(best_target["move_pct"] * 100, 2),
                    "regime": "double-riskoff",
                },
            ))

        return signals[:MAX_TRADES_PER_DAY]
