"""
Strategie : TLT/SPY Divergence

Edge structurel :
TLT (obligations long terme) et SPY (actions) sont normalement inversement correles.
Quand ils bougent dans la MEME direction pendant 30+ min, c'est une anomalie
qui se resout — on trade la correction du mouvement le plus faible.

Iteration barre par barre. Stop 0.5%, target 0.8%.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap, volume_ratio


class TLTSPYDivergenceStrategy(BaseStrategy):
    name = "TLT SPY Divergence"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    SAME_DIR_BARS = 6           # 6 barres 5M = 30 min dans la meme direction
    MIN_MOVE_PCT = 0.003        # Chaque asset doit bouger d'au moins 0.3%
    STOP_PCT = 0.005            # 0.5%
    TARGET_PCT = 0.008          # 0.8%
    TRADE_TICKER = "SPY"        # On trade SPY (plus liquide)

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "TLT"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        # Verifier que les deux tickers sont disponibles
        if "SPY" not in data or "TLT" not in data:
            return []

        spy_df = data["SPY"]
        tlt_df = data["TLT"]

        if len(spy_df) < self.SAME_DIR_BARS + 5 or len(tlt_df) < self.SAME_DIR_BARS + 5:
            return []

        # Aligner les deux DataFrames sur le meme index
        common_idx = spy_df.index.intersection(tlt_df.index)
        if len(common_idx) < self.SAME_DIR_BARS + 5:
            return []

        spy = spy_df.loc[common_idx].copy()
        tlt = tlt_df.loc[common_idx].copy()

        # Calculer les returns cumulatifs par fenetre glissante
        spy["ret"] = spy["close"].pct_change()
        tlt["ret"] = tlt["close"].pct_change()

        spy["vol_ratio"] = volume_ratio(spy["volume"], 20)

        candidates = []

        # Iterer barre par barre (10:00-15:30)
        tradeable_idx = spy.between_time("10:00", "15:30").index
        signal_found = False

        for ts in tradeable_idx:
            if signal_found:
                break

            idx = spy.index.get_loc(ts)
            if idx < self.SAME_DIR_BARS:
                continue

            # Calculer le mouvement sur la fenetre de lookback
            spy_window = spy.iloc[idx - self.SAME_DIR_BARS:idx + 1]
            tlt_window = tlt.iloc[idx - self.SAME_DIR_BARS:idx + 1]

            spy_move = (spy_window["close"].iloc[-1] - spy_window["close"].iloc[0]) / spy_window["close"].iloc[0]
            tlt_move = (tlt_window["close"].iloc[-1] - tlt_window["close"].iloc[0]) / tlt_window["close"].iloc[0]

            # Les deux doivent avoir un mouvement significatif
            if abs(spy_move) < self.MIN_MOVE_PCT or abs(tlt_move) < self.MIN_MOVE_PCT:
                continue

            # Verifier qu'ils bougent dans la MEME direction (anomalie)
            same_direction = (spy_move > 0 and tlt_move > 0) or (spy_move < 0 and tlt_move < 0)
            if not same_direction:
                continue  # Comportement normal (inverse) — pas de signal

            # Filtre volume SPY
            vol_r = spy["vol_ratio"].iloc[idx]
            if pd.isna(vol_r):
                continue

            entry_price = spy["close"].iloc[idx]

            # Determiner qui est le "weak mover" et trader la correction
            # Si les deux montent et SPY a monte moins que TLT : SPY va corriger (SHORT)
            # Si les deux baissent et SPY a baisse moins que TLT : SPY va corriger (LONG)

            if spy_move > 0 and tlt_move > 0:
                # Les deux montent — normalement inversement correles
                # SPY devrait baisser pour corriger
                stop_loss = entry_price * (1 + self.STOP_PCT)
                take_profit = entry_price * (1 - self.TARGET_PCT)
                action = "SHORT"
                score = abs(spy_move) + abs(tlt_move)

            else:
                # Les deux baissent — SPY devrait remonter pour corriger
                stop_loss = entry_price * (1 - self.STOP_PCT)
                take_profit = entry_price * (1 + self.TARGET_PCT)
                action = "LONG"
                score = abs(spy_move) + abs(tlt_move)

            candidates.append({
                "score": score,
                "signal": Signal(
                    action=action,
                    ticker=self.TRADE_TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "spy_move_pct": round(spy_move * 100, 2),
                        "tlt_move_pct": round(tlt_move * 100, 2),
                        "same_dir_bars": self.SAME_DIR_BARS,
                    },
                ),
            })
            signal_found = True

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
