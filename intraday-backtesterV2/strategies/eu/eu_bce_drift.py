"""
EU-2 : BCE Rate Decision Drift

Edge structurel :
Les jours de decision BCE (taux directeurs), les banques EU reagissent
de facon previsible :
- BCE hawkish (hausse ou maintien des taux) -> LONG banques EU (marges NII)
- BCE dovish (baisse des taux) -> SHORT banques EU (compression NII)

Le trade est pris le jour meme de l'annonce (13:45 CET typiquement)
et tenu jusqu'a la fermeture.

Backtest proxy : yfinance BNP.PA, GLE.PA, DBK.DE daily 5 ans.
On mesure le return des banques EU les jours BCE vs jours normaux.

Couts EU : 0.10% + 0.03% slippage = 0.13% aller simple
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from eu_backtest_engine import EUBaseStrategy, EUSignal


# ── BCE Rate Decision Dates (2021-2026) ──
# Source: ECB monetary policy calendar
# Format: (date_str, direction) where direction is "hawkish" or "dovish"
BCE_DECISIONS = [
    # 2021 - All hold (dovish stance, PEPP purchases)
    ("2021-01-21", "dovish"), ("2021-03-11", "dovish"),
    ("2021-04-22", "dovish"), ("2021-06-10", "dovish"),
    ("2021-07-22", "dovish"), ("2021-09-09", "dovish"),
    ("2021-10-28", "dovish"), ("2021-12-16", "dovish"),
    # 2022 - Pivot to hawkish, rate hikes begin July
    ("2022-02-03", "hawkish"), ("2022-03-10", "hawkish"),
    ("2022-04-14", "hawkish"), ("2022-06-09", "hawkish"),
    ("2022-07-21", "hawkish"),   # +50bp (first hike)
    ("2022-09-08", "hawkish"),   # +75bp
    ("2022-10-27", "hawkish"),   # +75bp
    ("2022-12-15", "hawkish"),   # +50bp
    # 2023 - Continued hikes, then pause
    ("2023-02-02", "hawkish"),   # +50bp
    ("2023-03-16", "hawkish"),   # +50bp
    ("2023-05-04", "hawkish"),   # +25bp
    ("2023-06-15", "hawkish"),   # +25bp
    ("2023-07-27", "hawkish"),   # +25bp
    ("2023-09-14", "hawkish"),   # +25bp (last hike)
    ("2023-10-26", "hawkish"),   # Hold (hawkish hold)
    ("2023-12-14", "hawkish"),   # Hold
    # 2024 - Rate cuts begin June
    ("2024-01-25", "hawkish"),   # Hold
    ("2024-03-07", "hawkish"),   # Hold
    ("2024-04-11", "hawkish"),   # Hold
    ("2024-06-06", "dovish"),    # -25bp (first cut)
    ("2024-07-18", "dovish"),    # Hold but dovish guidance
    ("2024-09-12", "dovish"),    # -25bp
    ("2024-10-17", "dovish"),    # -25bp
    ("2024-12-12", "dovish"),    # -25bp
    # 2025 - Continued easing
    ("2025-01-30", "dovish"),    # -25bp
    ("2025-03-06", "dovish"),    # -25bp
    ("2025-04-17", "dovish"),    # Hold
    ("2025-06-05", "dovish"),    # -25bp expected
    ("2025-07-24", "dovish"),
    ("2025-09-11", "dovish"),
    ("2025-10-30", "dovish"),
    ("2025-12-18", "dovish"),
    # 2026 - Projected dates
    ("2026-01-22", "dovish"),
    ("2026-03-05", "dovish"),
]

# Bank tickers to trade
BANK_TICKERS = ["BNP", "GLE", "DBK"]

# Parameters
STOP_PCT = 0.02       # 2% stop loss
TARGET_PCT = 0.03     # 3% take profit


class EUBCEDriftStrategy(EUBaseStrategy):
    name = "BCE Rate Decision Drift"

    def __init__(self):
        # Pre-process BCE dates into a lookup dict
        self.bce_dates = {}
        for date_str, direction in BCE_DECISIONS:
            try:
                d = pd.Timestamp(date_str).date()
                self.bce_dates[d] = direction
            except Exception:
                pass

    def get_required_tickers(self) -> list[str]:
        return BANK_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[EUSignal]:
        # Only trade on BCE decision days
        if date not in self.bce_dates:
            return []

        direction = self.bce_dates[date]
        signals = []

        for ticker in BANK_TICKERS:
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 5:
                continue

            # Get today's data
            if hasattr(df.index, 'date'):
                today_bars = df[df.index.date == date]
            else:
                today_bars = df[df.index == pd.Timestamp(date)]

            if today_bars.empty:
                # Try direct index lookup for daily data
                if pd.Timestamp(date) in df.index:
                    today_bars = df.loc[[pd.Timestamp(date)]]
                else:
                    continue

            if today_bars.empty:
                continue

            bar = today_bars.iloc[0]
            entry_price = bar["open"]
            entry_ts = today_bars.index[0]

            if entry_price <= 0:
                continue

            if direction == "hawkish":
                # Hawkish = LONG banks (higher rates = better NII margins)
                stop = entry_price * (1 - STOP_PCT)
                target = entry_price * (1 + TARGET_PCT)
                action = "LONG"
            else:
                # Dovish = SHORT banks (lower rates = NII compression)
                stop = entry_price * (1 + STOP_PCT)
                target = entry_price * (1 - TARGET_PCT)
                action = "SHORT"

            signals.append(EUSignal(
                action=action,
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=stop,
                take_profit=target,
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "bce_direction": direction,
                    "bce_date": str(date),
                },
            ))

        return signals
