"""
Gold Fear Gauge — SHORT ONLY

Edge : Quand GLD monte (> +0.5%) et SPY baisse (< -0.3%) en meme temps,
c'est un signal risk-off clair. Les investisseurs fuient vers l'or.
Dans ce regime, les high-beta stocks souffrent le plus.
On short le high-beta qui decline le plus et qui est sous son VWAP.

Declenchement a 10:30 ET (assez de prix pour confirmer le regime).
Stop 1.0%, target 2.0% ou exit a 14:00 ET.
Max 1 trade/jour.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap

# Tickers de signal (regime) vs tickers tradables (high-beta)
SIGNAL_TICKERS = ["GLD", "SPY"]
TRADE_TARGETS = ["TSLA", "NVDA", "AMD", "COIN", "MARA"]


class GoldFearGaugeStrategy(BaseStrategy):
    name = "Gold Fear Gauge"

    GLD_MIN_MOVE = 0.005       # GLD > +0.5% depuis l'open
    SPY_MAX_MOVE = -0.003      # SPY < -0.3% depuis l'open
    STOP_PCT = 0.010           # 1.0%
    TARGET_PCT = 0.020         # 2.0%
    MAX_TRADES_PER_DAY = 1

    def get_required_tickers(self) -> list[str]:
        return ["GLD", "SPY", "TSLA", "NVDA", "AMD", "COIN", "MARA"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        # Verifier les tickers de signal
        if "GLD" not in data or "SPY" not in data:
            return []

        gld_df = data["GLD"]
        spy_df = data["SPY"]

        if len(gld_df) < 10 or len(spy_df) < 10:
            return []

        # Open du jour pour GLD et SPY
        gld_open = gld_df.iloc[0]["open"]
        spy_open = spy_df.iloc[0]["open"]

        signals = []
        signal_found = False

        # Iterer barre par barre a partir de 10:30 pour detecter le regime
        gld_tradeable = gld_df.between_time("10:30", "14:00")

        for ts, gld_bar in gld_tradeable.iterrows():
            if signal_found:
                break

            # Verifier le regime risk-off a cette barre
            gld_move = (gld_bar["close"] - gld_open) / gld_open

            # Trouver la barre SPY correspondante
            if ts not in spy_df.index:
                # Chercher la barre SPY la plus proche
                spy_at_ts = spy_df[spy_df.index <= ts]
                if spy_at_ts.empty:
                    continue
                spy_bar = spy_at_ts.iloc[-1]
            else:
                spy_bar = spy_df.loc[ts]

            spy_move = (spy_bar["close"] - spy_open) / spy_open

            # Condition risk-off : GLD up + SPY down
            if gld_move < self.GLD_MIN_MOVE or spy_move > self.SPY_MAX_MOVE:
                continue

            # Risk-off confirme — chercher le meilleur short parmi les high-beta
            best_target = None
            best_decline = 0.0

            for target_ticker in TRADE_TARGETS:
                if target_ticker not in data:
                    continue

                target_df = data[target_ticker]
                if len(target_df) < 20:
                    continue

                # Trouver la barre la plus proche du timestamp
                target_at_ts = target_df[target_df.index <= ts]
                if target_at_ts.empty:
                    continue

                target_bar = target_at_ts.iloc[-1]
                target_open = target_df.iloc[0]["open"]
                target_move = (target_bar["close"] - target_open) / target_open

                # Le target doit etre en baisse
                if target_move >= 0:
                    continue

                # Verifier que le prix est sous le VWAP
                target_vwap = vwap(target_df)
                vwap_at_ts = target_vwap[target_vwap.index <= ts]
                if vwap_at_ts.empty:
                    continue

                if target_bar["close"] > vwap_at_ts.iloc[-1]:
                    continue  # Au-dessus du VWAP — pas ideal pour un short

                # Selectionner le plus gros declin
                decline = abs(target_move)
                if decline > best_decline:
                    best_decline = decline
                    best_target = {
                        "ticker": target_ticker,
                        "price": target_bar["close"],
                        "move_pct": target_move,
                        "timestamp": target_at_ts.index[-1],
                    }

            if best_target is not None:
                entry_price = best_target["price"]
                stop = entry_price * (1 + self.STOP_PCT)
                target = entry_price * (1 - self.TARGET_PCT)

                signals.append(Signal(
                    action="SHORT",
                    ticker=best_target["ticker"],
                    entry_price=entry_price,
                    stop_loss=stop,
                    take_profit=target,
                    timestamp=best_target["timestamp"],
                    metadata={
                        "strategy": self.name,
                        "gld_move_pct": round(gld_move * 100, 2),
                        "spy_move_pct": round(spy_move * 100, 2),
                        "target_move_pct": round(best_target["move_pct"] * 100, 2),
                        "regime": "risk-off",
                    },
                ))
                signal_found = True

        return signals[:self.MAX_TRADES_PER_DAY]
