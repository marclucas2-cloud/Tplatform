"""
TLT Bank Signal — LONG ONLY

Edge : Quand TLT baisse > 0.5% (yields en hausse), les banques beneficient
car leur marge d'interet nette augmente. Effet intraday exploitable.
A 14:00 ET : si TLT est down > 0.5% depuis l'open, acheter la banque
la plus forte (relative strength) parmi JPM, BAC, WFC, GS.

Entree 14:00-14:30, sortie 15:55 (meme jour, pas overnight).
Stop 1.0%, target 1.5%. Max 1 trade/jour.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap


BANK_TICKERS = ["JPM", "BAC", "WFC", "GS"]


class TLTBankSignalStrategy(BaseStrategy):
    name = "TLT Bank Signal"

    TLT_MIN_DROP = -0.005      # TLT doit baisser > 0.5%
    STOP_PCT = 0.010           # 1.0%
    TARGET_PCT = 0.015         # 1.5%
    MAX_TRADES_PER_DAY = 1

    def get_required_tickers(self) -> list[str]:
        return ["TLT", "JPM", "BAC", "WFC", "GS"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        if "TLT" not in data:
            return []

        tlt_df = data["TLT"]
        if len(tlt_df) < 20:
            return []

        tlt_open = tlt_df.iloc[0]["open"]

        signals = []
        signal_found = False

        # Iterer barre par barre 14:00-14:30 (fenetre d'entree)
        tlt_entry_window = tlt_df.between_time("14:00", "14:30")

        for ts, tlt_bar in tlt_entry_window.iterrows():
            if signal_found:
                break

            # Verifier la condition TLT
            tlt_move = (tlt_bar["close"] - tlt_open) / tlt_open
            if tlt_move > self.TLT_MIN_DROP:
                continue  # TLT n'a pas assez baisse

            # TLT en baisse confirmee — chercher la banque la plus forte
            best_bank = None
            best_strength = -999.0

            for bank_ticker in BANK_TICKERS:
                if bank_ticker not in data:
                    continue

                bank_df = data[bank_ticker]
                if len(bank_df) < 20:
                    continue

                # Trouver la barre la plus proche du timestamp
                bank_at_ts = bank_df[bank_df.index <= ts]
                if bank_at_ts.empty:
                    continue

                bank_bar = bank_at_ts.iloc[-1]
                bank_open = bank_df.iloc[0]["open"]
                bank_move = (bank_bar["close"] - bank_open) / bank_open

                # Relative strength : la banque qui performe le mieux
                # Idealement deja en hausse ou avec la plus petite baisse
                strength = bank_move

                # Verifier que le prix est au-dessus du VWAP (confirmation haussiere)
                bank_vwap = vwap(bank_df)
                vwap_at_ts = bank_vwap[bank_vwap.index <= ts]
                if vwap_at_ts.empty:
                    continue

                above_vwap = bank_bar["close"] > vwap_at_ts.iloc[-1]

                # Bonus de force si au-dessus du VWAP
                if above_vwap:
                    strength += 0.005  # Bonus 0.5%

                if strength > best_strength:
                    best_strength = strength
                    best_bank = {
                        "ticker": bank_ticker,
                        "price": bank_bar["close"],
                        "move_pct": bank_move,
                        "above_vwap": above_vwap,
                        "timestamp": bank_at_ts.index[-1],
                    }

            if best_bank is not None:
                entry_price = best_bank["price"]
                stop = entry_price * (1 - self.STOP_PCT)
                target = entry_price * (1 + self.TARGET_PCT)

                signals.append(Signal(
                    action="LONG",
                    ticker=best_bank["ticker"],
                    entry_price=entry_price,
                    stop_loss=stop,
                    take_profit=target,
                    timestamp=best_bank["timestamp"],
                    metadata={
                        "strategy": self.name,
                        "tlt_move_pct": round(tlt_move * 100, 2),
                        "bank_move_pct": round(best_bank["move_pct"] * 100, 2),
                        "above_vwap": best_bank["above_vwap"],
                        "bank": best_bank["ticker"],
                    },
                ))
                signal_found = True

        return signals[:self.MAX_TRADES_PER_DAY]
