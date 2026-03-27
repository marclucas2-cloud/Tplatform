"""
STRAT-005 : FOMC Reaction — Next-Day Continuation

Edge structurel :
La reaction du marche le jour de l'annonce FOMC predit souvent la
direction du lendemain. Les recherches academiques montrent qu'un
"FOMC drift" existe : une reaction forte le jour J est suivie d'une
continuation le jour J+1, alimentee par le repositionnement
institutionnel post-annonce.

Regles :
- Identifier les ~40 jours FOMC (8/an x 5 ans) sur SPY
- Calculer le return open-to-close du jour FOMC (J+0)
- Si return J+0 > +0.3% (bullish), LONG SPY le jour J+1
- Si return J+0 < -0.3% (bearish), SHORT SPY le jour J+1
- Holding : open-to-close J+1
- Couts Alpaca standard : $0.005/share + 0.02% slippage

Donnees : yfinance SPY daily 5 ans
"""
import pandas as pd
import numpy as np

INITIAL_CAPITAL = 100_000
POSITION_SIZE_PCT = 0.10       # 10% du capital par trade
THRESHOLD_PCT = 0.003          # 0.3% minimum move
COMMISSION_PER_SHARE = 0.005   # $0.005/share
SLIPPAGE_PCT = 0.0002          # 0.02%

# Calendrier FOMC 2021-2026 (dates des decisions)
FOMC_DATES = [
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
    # 2026
    "2026-01-28", "2026-03-18",
]


class FOMCReactionStrategy:
    """
    Standalone daily strategy for FOMC day reaction -> next-day continuation.
    Does NOT inherit BaseStrategy (daily yfinance data, event-driven).
    """
    name = "FOMC Reaction"

    def __init__(self, threshold_pct: float = THRESHOLD_PCT):
        self.threshold_pct = threshold_pct
        self.fomc_set = set(pd.to_datetime(d).date() for d in FOMC_DATES)

    def backtest(self, spy_df: pd.DataFrame) -> pd.DataFrame:
        """
        Run the FOMC reaction backtest on SPY daily data.

        spy_df: DataFrame with columns [Open, High, Low, Close, Volume]
                and a DatetimeIndex (from yfinance).

        Returns: DataFrame of trades.
        """
        if spy_df.empty or len(spy_df) < 30:
            return pd.DataFrame()

        df = spy_df.copy()

        # Normaliser les colonnes (yfinance utilise majuscules)
        col_map = {}
        for c in df.columns:
            col_map[c] = c.lower()
        df = df.rename(columns=col_map)

        if "close" not in df.columns or "open" not in df.columns:
            return pd.DataFrame()

        # S'assurer que l'index est datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # ── Identifier les jours FOMC dans les donnees ──
        df["date"] = df.index.date
        trading_dates = sorted(df["date"].unique())
        date_to_idx = {d: i for i, d in enumerate(trading_dates)}

        trades = []
        capital = INITIAL_CAPITAL

        fomc_found = 0
        signals_generated = 0

        for fomc_date in sorted(self.fomc_set):
            if fomc_date not in date_to_idx:
                continue

            fomc_found += 1
            fomc_idx = date_to_idx[fomc_date]

            # Besoin du jour suivant
            if fomc_idx + 1 >= len(trading_dates):
                continue

            next_date = trading_dates[fomc_idx + 1]

            # Donnees jour FOMC
            fomc_day = df[df["date"] == fomc_date]
            if fomc_day.empty:
                continue

            day_open = fomc_day.iloc[0]["open"]
            day_close = fomc_day.iloc[-1]["close"]
            fomc_return = (day_close - day_open) / day_open

            # Filter: mouvement suffisant
            if abs(fomc_return) < self.threshold_pct:
                continue

            signals_generated += 1

            # Donnees jour suivant
            next_day = df[df["date"] == next_date]
            if next_day.empty:
                continue

            next_open = next_day.iloc[0]["open"]
            next_close = next_day.iloc[-1]["close"]

            # Direction
            if fomc_return > 0:
                direction = "LONG"
            else:
                direction = "SHORT"

            # Position sizing
            allocated = capital * POSITION_SIZE_PCT
            shares = int(allocated / next_open)
            if shares < 1:
                continue

            # Slippage
            if direction == "LONG":
                actual_entry = next_open * (1 + SLIPPAGE_PCT)
                actual_exit = next_close * (1 - SLIPPAGE_PCT)
                pnl = (actual_exit - actual_entry) * shares
            else:
                actual_entry = next_open * (1 - SLIPPAGE_PCT)
                actual_exit = next_close * (1 + SLIPPAGE_PCT)
                pnl = (actual_entry - actual_exit) * shares

            commission = shares * COMMISSION_PER_SHARE * 2  # entry + exit
            net_pnl = pnl - commission

            trades.append({
                "ticker": "SPY",
                "date": next_date,
                "direction": direction,
                "entry_price": round(actual_entry, 4),
                "exit_price": round(actual_exit, 4),
                "shares": shares,
                "pnl": round(pnl, 2),
                "commission": round(commission, 2),
                "net_pnl": round(net_pnl, 2),
                "entry_time": pd.Timestamp(next_date),
                "exit_time": pd.Timestamp(next_date),
                "exit_reason": "eod_close",
                "strategy": self.name,
                "fomc_date": str(fomc_date),
                "fomc_return_pct": round(fomc_return * 100, 3),
            })
            capital += net_pnl

        print(f"  [FOMC Reaction] FOMC dates found in data: {fomc_found}")
        print(f"  [FOMC Reaction] Signals generated (|return| > {self.threshold_pct*100:.1f}%): {signals_generated}")
        print(f"  [FOMC Reaction] Trades executed: {len(trades)}")

        return pd.DataFrame(trades)
