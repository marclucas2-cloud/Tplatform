"""
Strategie : Sector Leader Follow

Edge structurel :
Quand un ETF sectoriel (XLK, XLF, XLE...) bouge de >0.5% en 30 minutes,
ses composants principaux accusent un retard de 15-30 minutes.
On trade le composant "laggard" dans la meme direction que l'ETF leader.

Regles :
- Calculer le move de chaque ETF sectoriel sur 30 min (rolling)
- Si move > 0.5%, scanner les composants du secteur (SECTOR_MAP)
- Le composant doit avoir bouge < 0.2% dans la meme periode (laggard)
- Entry sur le laggard dans la direction du secteur
- Stop : 0.6% — Target : 0.8% (capture du rattrapage)
- Max 3 trades/jour, prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from universe import SECTOR_MAP
import config


MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
SECTOR_MOVE_PCT = 0.005      # 0.5% move ETF
LAGGARD_MAX_MOVE_PCT = 0.002 # Composant a bouge < 0.2%
STOP_PCT = 0.006             # 0.6%
TARGET_PCT = 0.008           # 0.8%
LOOKBACK_BARS = 6            # 30 min = 6 barres de 5 min


class SectorLeaderFollowStrategy(BaseStrategy):
    name = "Sector Leader Follow"

    def __init__(
        self,
        sector_move_pct: float = SECTOR_MOVE_PCT,
        laggard_max_move: float = LAGGARD_MAX_MOVE_PCT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        lookback_bars: int = LOOKBACK_BARS,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.sector_move_pct = sector_move_pct
        self.laggard_max_move = laggard_max_move
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.lookback_bars = lookback_bars
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        tickers = list(SECTOR_MAP.keys())
        for components in SECTOR_MAP.values():
            tickers.extend(components)
        return list(set(tickers))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        # Scanner apres 10:00 pour laisser chauffer les indicateurs
        for etf, components in SECTOR_MAP.items():
            if etf not in data:
                continue

            etf_df = data[etf]
            if len(etf_df) < self.lookback_bars + 5:
                continue

            # Barres tradeable : 10:00-15:30
            etf_tradeable = etf_df.between_time("10:00", "15:30")
            if len(etf_tradeable) < self.lookback_bars:
                continue

            for ts_idx in range(self.lookback_bars, len(etf_tradeable)):
                if len(candidates) >= self.max_trades_per_day:
                    break

                ts = etf_tradeable.index[ts_idx]
                current_bar = etf_tradeable.iloc[ts_idx]
                ref_bar = etf_tradeable.iloc[ts_idx - self.lookback_bars]

                # Move de l'ETF sur les N dernieres barres
                etf_move = (current_bar["close"] - ref_bar["close"]) / ref_bar["close"]

                if abs(etf_move) < self.sector_move_pct:
                    continue

                direction = "LONG" if etf_move > 0 else "SHORT"

                # Scanner les composants laggards
                for component in components:
                    if component not in data:
                        continue

                    comp_df = data[component]
                    if len(comp_df) < self.lookback_bars + 5:
                        continue
                    if comp_df.iloc[0]["open"] < MIN_PRICE:
                        continue

                    # Trouver la barre du composant au meme timestamp (ou la plus proche)
                    if ts not in comp_df.index:
                        # Chercher la barre la plus proche avant ce timestamp
                        comp_before = comp_df[comp_df.index <= ts]
                        if comp_before.empty:
                            continue
                        comp_ts = comp_before.index[-1]
                    else:
                        comp_ts = ts

                    comp_current = comp_df.loc[comp_ts]

                    # Trouver la barre de reference du composant
                    ref_ts = etf_tradeable.index[ts_idx - self.lookback_bars]
                    comp_ref_bars = comp_df[comp_df.index <= ref_ts]
                    if comp_ref_bars.empty:
                        continue
                    comp_ref = comp_ref_bars.iloc[-1]

                    # Move du composant
                    if comp_ref["close"] <= 0:
                        continue
                    comp_move = (comp_current["close"] - comp_ref["close"]) / comp_ref["close"]

                    # Le composant est un laggard si son move est faible
                    # ET dans la meme direction (ou neutre)
                    if abs(comp_move) > self.laggard_max_move:
                        continue

                    entry_price = comp_current["close"]
                    lag_score = abs(etf_move) - abs(comp_move)  # Plus le lag est grand, mieux c'est

                    if direction == "LONG":
                        stop_loss = entry_price * (1 - self.stop_pct)
                        take_profit = entry_price * (1 + self.target_pct)
                    else:
                        stop_loss = entry_price * (1 + self.stop_pct)
                        take_profit = entry_price * (1 - self.target_pct)

                    # Eviter les doublons de ticker
                    already_in = any(c["signal"].ticker == component for c in candidates)
                    if already_in:
                        continue

                    candidates.append({
                        "score": lag_score,
                        "signal": Signal(
                            action=direction,
                            ticker=component,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=comp_ts,
                            metadata={
                                "strategy": self.name,
                                "etf_leader": etf,
                                "etf_move_pct": round(etf_move * 100, 3),
                                "comp_move_pct": round(comp_move * 100, 3),
                                "lag_score": round(lag_score * 100, 3),
                            },
                        ),
                    })
                    break  # Un seul laggard par ETF par timestamp

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
