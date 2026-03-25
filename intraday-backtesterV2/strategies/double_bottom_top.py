"""
Strategie : Double Bottom / Double Top

Edge structurel :
Le prix qui touche le meme niveau deux fois (within 0.2% de tolerance)
sur une periode de 1-2 heures, avec une divergence RSI (deuxieme touche
a un RSI plus haut pour double bottom, plus bas pour double top),
est un signal de reversal classique et robuste.

Regles :
- Scanner les pivots (lows pour double bottom, highs pour double top)
- Deux touches du meme niveau (tolerance 0.2%)
- Espacement : 12-24 barres (1-2 heures en 5min)
- Divergence RSI : RSI au 2eme touch > RSI au 1er touch (bottom)
                   RSI au 2eme touch < RSI au 1er touch (top)
- Stop : sous le double bottom / au-dessus du double top + buffer
- Target : hauteur du pattern (neckline - bottom) projete au-dessus
- Max 3 trades/jour, prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi
import config


LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
}

MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
TOLERANCE_PCT = 0.002        # 0.2% tolerance pour le "meme niveau"
MIN_SPACING_BARS = 12        # Min 12 barres entre les 2 touches (1 heure)
MAX_SPACING_BARS = 24        # Max 24 barres (2 heures)
RSI_PERIOD = 14
STOP_BUFFER_PCT = 0.003      # 0.3% buffer au-dela du pattern
RR_RATIO = 1.5               # Risk:Reward 1:1.5


class DoubleBottomTopStrategy(BaseStrategy):
    name = "Double Bottom Top"

    def __init__(
        self,
        tolerance_pct: float = TOLERANCE_PCT,
        min_spacing: int = MIN_SPACING_BARS,
        max_spacing: int = MAX_SPACING_BARS,
        rsi_period: int = RSI_PERIOD,
        stop_buffer: float = STOP_BUFFER_PCT,
        rr_ratio: float = RR_RATIO,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.tolerance_pct = tolerance_pct
        self.min_spacing = min_spacing
        self.max_spacing = max_spacing
        self.rsi_period = rsi_period
        self.stop_buffer = stop_buffer
        self.rr_ratio = rr_ratio
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 30:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            df = df.copy()
            df["rsi"] = rsi(df["close"], period=self.rsi_period)

            # Scanner entre 10:30 et 15:30
            tradeable = df.between_time("10:30", "15:30")
            if len(tradeable) < self.min_spacing + 2:
                continue

            tradeable_list = list(tradeable.iterrows())
            signal_found = False

            for i in range(self.min_spacing, len(tradeable_list)):
                if signal_found:
                    break

                ts_current, bar_current = tradeable_list[i]
                current_rsi = bar_current.get("rsi", np.nan)
                if pd.isna(current_rsi):
                    continue

                # Chercher un match dans les barres precedentes
                start_j = max(0, i - self.max_spacing)
                end_j = i - self.min_spacing + 1

                for j in range(start_j, end_j):
                    if signal_found:
                        break

                    ts_prev, bar_prev = tradeable_list[j]
                    prev_rsi = bar_prev.get("rsi", np.nan)
                    if pd.isna(prev_rsi):
                        continue

                    # ── Double Bottom : deux lows au meme niveau ──
                    low_diff = abs(bar_current["low"] - bar_prev["low"])
                    if bar_prev["low"] > 0:
                        low_diff_pct = low_diff / bar_prev["low"]
                    else:
                        continue

                    if low_diff_pct < self.tolerance_pct:
                        # Double bottom detecte — verifier divergence RSI
                        # RSI au 2eme touch doit etre PLUS HAUT (divergence haussiere)
                        if current_rsi > prev_rsi + 3:  # Min 3 points de difference
                            # Neckline = high entre les 2 touches
                            neckline = max(
                                tradeable_list[k][1]["high"]
                                for k in range(j, i + 1)
                            )
                            pattern_bottom = min(bar_prev["low"], bar_current["low"])
                            pattern_height = neckline - pattern_bottom

                            if pattern_height <= 0 or pattern_height / bar_current["close"] > 0.05:
                                continue

                            entry_price = bar_current["close"]
                            stop_loss = pattern_bottom * (1 - self.stop_buffer)
                            risk = entry_price - stop_loss
                            if risk <= 0 or risk / entry_price > 0.03:
                                continue

                            take_profit = entry_price + risk * self.rr_ratio

                            candidates.append({
                                "score": (current_rsi - prev_rsi) * pattern_height / entry_price,
                                "signal": Signal(
                                    action="LONG",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts_current,
                                    metadata={
                                        "strategy": self.name,
                                        "pattern": "double_bottom",
                                        "rsi_divergence": round(current_rsi - prev_rsi, 1),
                                        "pattern_height_pct": round(pattern_height / entry_price * 100, 2),
                                        "neckline": round(neckline, 2),
                                    },
                                ),
                            })
                            signal_found = True

                    # ── Double Top : deux highs au meme niveau ──
                    high_diff = abs(bar_current["high"] - bar_prev["high"])
                    if bar_prev["high"] > 0:
                        high_diff_pct = high_diff / bar_prev["high"]
                    else:
                        continue

                    if not signal_found and high_diff_pct < self.tolerance_pct:
                        # Double top detecte — verifier divergence RSI
                        # RSI au 2eme touch doit etre PLUS BAS (divergence baissiere)
                        if current_rsi < prev_rsi - 3:
                            # Support = low entre les 2 touches
                            support = min(
                                tradeable_list[k][1]["low"]
                                for k in range(j, i + 1)
                            )
                            pattern_top = max(bar_prev["high"], bar_current["high"])
                            pattern_height = pattern_top - support

                            if pattern_height <= 0 or pattern_height / bar_current["close"] > 0.05:
                                continue

                            entry_price = bar_current["close"]
                            stop_loss = pattern_top * (1 + self.stop_buffer)
                            risk = stop_loss - entry_price
                            if risk <= 0 or risk / entry_price > 0.03:
                                continue

                            take_profit = entry_price - risk * self.rr_ratio

                            candidates.append({
                                "score": (prev_rsi - current_rsi) * pattern_height / entry_price,
                                "signal": Signal(
                                    action="SHORT",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts_current,
                                    metadata={
                                        "strategy": self.name,
                                        "pattern": "double_top",
                                        "rsi_divergence": round(prev_rsi - current_rsi, 1),
                                        "pattern_height_pct": round(pattern_height / entry_price * 100, 2),
                                        "support": round(support, 2),
                                    },
                                ),
                            })
                            signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
