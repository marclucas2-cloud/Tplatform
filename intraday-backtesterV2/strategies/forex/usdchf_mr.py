"""
FX-4 : USD/CHF Mean Reversion (Z-Score)

Edge structurel :
USD/CHF est une paire fortement liee aux flux safe-haven. Le franc
suisse etant correle a l'or et aux tensions geopolitiques, il tend a
revenir vers une moyenne structurelle une fois les chocs absorbes.
Le z-score (prix vs SMA 60 jours) detecte les extremes statistiques
qui precedent un retour a la moyenne.

Regles :
- Z-score = (close - SMA(60)) / StdDev(60)
- Long  si z < -2.0   (sous-evalue)
- Short si z > +2.0   (sur-evalue)
- Close si z revient vers 0.5 (ou -0.5)
- Stop  si z atteint ±3.0 (acceleration contre nous)
- Couts FX : 0.005% round-trip

Donnees : yfinance USDCHF=X daily 5 ans
"""
import pandas as pd
import numpy as np

INITIAL_CAPITAL = 100_000
SMA_PERIOD = 60
Z_ENTRY = 2.0                 # entree a z-score ±2
Z_EXIT = 0.5                  # sortie a z-score ±0.5
Z_STOP = 3.0                  # stop a z-score ±3
POSITION_SIZE_PCT = 0.10      # 10% du capital par trade
FX_COST_PCT = 0.00005         # 0.005% round-trip


class USDCHFMeanReversionStrategy:
    """
    Standalone daily strategy for USD/CHF z-score mean reversion.
    Does NOT inherit BaseStrategy (different asset class / daily yfinance data).
    """
    name = "USD/CHF Mean Reversion"

    def backtest(self, usdchf_df: pd.DataFrame) -> pd.DataFrame:
        """
        Run the mean reversion backtest on USD/CHF daily data.

        usdchf_df: DataFrame with columns [open, high, low, close, volume]
                   and a DatetimeIndex.

        Returns: DataFrame of trades.
        """
        if usdchf_df.empty or len(usdchf_df) < SMA_PERIOD + 10:
            return pd.DataFrame()

        df = usdchf_df.copy()

        # Indicateurs
        df["sma"] = df["close"].rolling(SMA_PERIOD).mean()
        df["std"] = df["close"].rolling(SMA_PERIOD).std()
        df["z_score"] = (df["close"] - df["sma"]) / df["std"].replace(0, np.nan)
        df = df.dropna(subset=["z_score"])

        if df.empty:
            return pd.DataFrame()

        # ── Backtest ──
        trades = []
        capital = INITIAL_CAPITAL
        in_position = False
        direction = None  # "LONG" or "SHORT"
        entry_price = 0.0
        entry_time = None

        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            price = row["close"]
            z = row["z_score"]

            if in_position:
                hit_exit = False
                exit_reason = ""

                if direction == "LONG":
                    # Close si z revient vers +0.5 (target atteint)
                    if z >= -Z_EXIT:
                        hit_exit = True
                        exit_reason = "z_target"
                    # Stop si z chute vers -3
                    elif z <= -Z_STOP:
                        hit_exit = True
                        exit_reason = "z_stop"

                elif direction == "SHORT":
                    # Close si z revient vers -0.5
                    if z <= Z_EXIT:
                        hit_exit = True
                        exit_reason = "z_target"
                    # Stop si z monte vers +3
                    elif z >= Z_STOP:
                        hit_exit = True
                        exit_reason = "z_stop"

                if hit_exit:
                    allocated = capital * POSITION_SIZE_PCT
                    cost = allocated * FX_COST_PCT
                    if direction == "LONG":
                        pnl = (price / entry_price - 1) * allocated
                    else:
                        pnl = (entry_price / price - 1) * allocated
                    net_pnl = pnl - cost

                    entry_date = entry_time.date() if hasattr(entry_time, "date") else pd.Timestamp(entry_time).date()
                    trades.append({
                        "ticker": "USDCHF",
                        "date": entry_date,
                        "direction": direction,
                        "entry_price": round(entry_price, 5),
                        "exit_price": round(price, 5),
                        "shares": 1,
                        "pnl": round(pnl, 2),
                        "commission": round(cost, 2),
                        "net_pnl": round(net_pnl, 2),
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "exit_reason": exit_reason,
                        "strategy": self.name,
                        "z_entry": round((entry_price - row["sma"]) / row["std"] if row["std"] != 0 else 0, 2),
                        "z_exit": round(z, 2),
                    })
                    capital += net_pnl
                    in_position = False
                    direction = None

            # ── Check new entry ──
            if not in_position:
                if z < -Z_ENTRY:
                    # LONG : sous-evalue
                    in_position = True
                    direction = "LONG"
                    entry_price = price
                    entry_time = ts

                elif z > Z_ENTRY:
                    # SHORT : sur-evalue
                    in_position = True
                    direction = "SHORT"
                    entry_price = price
                    entry_time = ts

        # ── Close remaining position ──
        if in_position and len(df) > 0:
            last = df.iloc[-1]
            ts = df.index[-1]
            price = last["close"]
            allocated = capital * POSITION_SIZE_PCT
            cost = allocated * FX_COST_PCT
            if direction == "LONG":
                pnl = (price / entry_price - 1) * allocated
            else:
                pnl = (entry_price / price - 1) * allocated
            net_pnl = pnl - cost

            entry_date = entry_time.date() if hasattr(entry_time, "date") else pd.Timestamp(entry_time).date()
            trades.append({
                "ticker": "USDCHF",
                "date": entry_date,
                "direction": direction,
                "entry_price": round(entry_price, 5),
                "exit_price": round(price, 5),
                "shares": 1,
                "pnl": round(pnl, 2),
                "commission": round(cost, 2),
                "net_pnl": round(net_pnl, 2),
                "entry_time": entry_time,
                "exit_time": ts,
                "exit_reason": "end_of_data",
                "strategy": self.name,
                "z_entry": 0,
                "z_exit": round(last["z_score"] if "z_score" in last.index else 0, 2),
            })

        return pd.DataFrame(trades)
