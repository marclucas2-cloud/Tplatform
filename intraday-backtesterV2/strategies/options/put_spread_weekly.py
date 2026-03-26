"""
OPT-1 : Weekly Put Credit Spread SPY (Proxy)

Edge structurel :
La vente de put spreads sur SPY est profitable a long terme car la
volatilite implicite est systematiquement > volatilite realisee (VRP).
Les vendredis, on vend un put spread 10-delta, 5 points de large, 7 DTE.

Proxy backtest (pas d'options reelles) :
- Chaque vendredi : verifier si SPY baisse de > 2% dans les 7 jours suivants
- Si SPY ne baisse PAS de > 2% : WIN (premium recu estimee ~$50)
- Si SPY baisse de > 2% : LOSS (max loss = $450)
- Win rate typique ~85-90%, mais les pertes sont plus grosses que les gains

Donnees : yfinance SPY daily 5 ans
"""
import pandas as pd
import numpy as np


# Parameters
PREMIUM_RECEIVED = 50      # ~$50 credit recue pour un 5pt put spread 10-delta
MAX_LOSS = 450             # $500 spread width - $50 premium = $450 max loss
LOSS_THRESHOLD_PCT = -0.02 # -2% SPY = put spread ITM (approximate)
LOOKFORWARD_DAYS = 7       # 7 DTE (calendar days -> ~5 trading days)
INITIAL_CAPITAL = 100_000


class PutSpreadWeeklyStrategy:
    """
    Standalone daily strategy — does NOT inherit BaseStrategy.
    Uses its own backtest logic (simpler: one check per week).
    """
    name = "Weekly Put Credit Spread SPY (Proxy)"

    def backtest(self, spy_df: pd.DataFrame) -> pd.DataFrame:
        """
        Run the proxy backtest on SPY daily data.

        spy_df: DataFrame with columns [open, high, low, close, volume]
                and a DatetimeIndex.

        Returns: DataFrame of trades with pnl, date, etc.
        """
        if spy_df.empty:
            return pd.DataFrame()

        # Ensure index is datetime
        spy_df = spy_df.copy()
        if not isinstance(spy_df.index, pd.DatetimeIndex):
            spy_df.index = pd.to_datetime(spy_df.index)

        trades = []
        closes = spy_df["close"]

        for i in range(len(spy_df)):
            row = spy_df.iloc[i]
            idx = spy_df.index[i]
            dt = idx.date() if hasattr(idx, 'date') else pd.Timestamp(idx).date()

            # Only trade on Fridays
            if idx.weekday() != 4:
                continue

            entry_price = row["close"]

            # Look forward 7 calendar days (~5 trading days)
            future_end = idx + pd.Timedelta(days=LOOKFORWARD_DAYS)
            future_bars = spy_df[(spy_df.index > idx) & (spy_df.index <= future_end)]

            if future_bars.empty:
                continue  # Not enough forward data

            # Check if SPY drops > 2% at any point in the next 7 days
            min_close = future_bars["low"].min()  # Use low for worst case
            max_decline = (min_close - entry_price) / entry_price

            if max_decline <= LOSS_THRESHOLD_PCT:
                # PUT spread went ITM — loss
                pnl = -MAX_LOSS
                exit_reason = "itm_loss"
            else:
                # PUT spread expired OTM — keep premium
                pnl = PREMIUM_RECEIVED
                exit_reason = "otm_win"

            # Commission proxy: ~$1.30 per contract (open + close)
            commission = 2.60  # 2 legs x open/close

            trades.append({
                "ticker": "SPY",
                "date": dt,
                "direction": "SHORT_PUT_SPREAD",
                "entry_price": round(entry_price, 2),
                "exit_price": round(future_bars.iloc[-1]["close"], 2),
                "shares": 1,  # 1 contract
                "pnl": pnl,
                "commission": commission,
                "net_pnl": round(pnl - commission, 2),
                "entry_time": idx,
                "exit_time": future_bars.index[-1],
                "exit_reason": exit_reason,
                "strategy": self.name,
                "max_decline_pct": round(max_decline * 100, 2),
            })

        return pd.DataFrame(trades)
