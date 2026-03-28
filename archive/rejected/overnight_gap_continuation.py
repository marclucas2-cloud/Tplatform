"""
Strategie : Overnight Gap Continuation

Edge structurel :
La Gap CONTINUATION fonctionne quand le gap est accompagne de volume
et de momentum dans la direction du gap.

V4 : Sweet spot entre selectivite et nombre de trades
- Gap minimum 1.2%
- Volume > 1.8x
- Confirmation : close au-dela du opening range
- Max 3 trades/jour (meilleurs setups seulement)
- R:R 1:2
- Exclure ETFs leverages + ETFs passifs
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# ── Tickers a exclure ──
EXCLUDE = {
    # ETFs leverages
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "TSLG", "TURB", "RWM",
    "PSQ", "SH", "SDS", "SMCL", "SNDK", "ZSL",
    "SPYM", "RKLZ",
    # ETFs passifs (gaps non informatifs)
    "SPY", "QQQ", "IWM", "DIA", "IVV", "ITOT", "VOO",
    "VEA", "VWO", "VXUS", "SCHB", "SCHD", "SCHF", "SCHG",
    "SCHH", "SCHW", "SCHX", "RSP", "LQD", "VCIT", "USHY",
    "PDBC", "PSLV",
}

# ── Parametres V4 ──
GAP_MIN_PCT = 1.1
VOL_CONFIRMATION_MULT = 1.8
RR_RATIO = 2.0
MIN_PRICE = 5.0


class OvernightGapContinuationStrategy(BaseStrategy):
    name = "Overnight Gap Continuation"

    def __init__(
        self,
        gap_min_pct: float = GAP_MIN_PCT,
        vol_mult: float = VOL_CONFIRMATION_MULT,
        rr_ratio: float = RR_RATIO,
        max_trades_per_day: int = 3,
    ):
        self.gap_min_pct = gap_min_pct
        self.vol_mult = vol_mult
        self.rr_ratio = rr_ratio
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker in EXCLUDE:
                continue
            if len(df) < 10:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            day_bars = df[df.index.date == date]
            if len(day_bars) < 5:
                continue

            # ── Close veille ──
            prev_day_bars = df[df.index.date < date]
            if prev_day_bars.empty:
                prev_close = day_bars.iloc[0].get("vwap", None)
                if prev_close is None or pd.isna(prev_close) or prev_close <= 0:
                    continue
            else:
                prev_close = prev_day_bars.iloc[-1]["close"]

            today_open = day_bars.iloc[0]["open"]
            if prev_close <= 0:
                continue

            gap_pct = ((today_open - prev_close) / prev_close) * 100
            if abs(gap_pct) < self.gap_min_pct:
                continue

            gap_direction = "UP" if gap_pct > 0 else "DOWN"

            # ── Volume des 15 premieres minutes ──
            opening_bars = day_bars.between_time("09:30", "09:44")
            if len(opening_bars) < 1:
                continue

            opening_volume = opening_bars["volume"].sum()
            opening_high = opening_bars["high"].max()
            opening_low = opening_bars["low"].min()

            avg_opening_vol = self._avg_opening_volume(df, date)
            if avg_opening_vol <= 0:
                avg_opening_vol = df["volume"].mean() * 3
                if avg_opening_vol <= 0:
                    continue

            vol_ratio = opening_volume / avg_opening_vol
            if vol_ratio < self.vol_mult:
                continue

            # ── Confirmation : 9:35-10:30 ──
            confirmation_bars = day_bars.between_time("09:35", "10:30")
            if len(confirmation_bars) < 1:
                continue

            confirmed = False
            entry_ts = None
            entry_price = None

            for ts, bar in confirmation_bars.iterrows():
                if gap_direction == "UP":
                    if bar["close"] > opening_high and bar["close"] > bar["open"]:
                        confirmed = True
                        entry_ts = ts
                        entry_price = bar["close"]
                        break
                else:
                    if bar["close"] < opening_low and bar["close"] < bar["open"]:
                        confirmed = True
                        entry_ts = ts
                        entry_price = bar["close"]
                        break

            if not confirmed:
                continue

            if gap_direction == "UP":
                stop_loss = opening_low
                risk = entry_price - stop_loss
                if risk <= 0 or risk / entry_price > 0.03:
                    continue
                take_profit = entry_price + risk * self.rr_ratio

                candidates.append({
                    "score": abs(gap_pct) * vol_ratio,
                    "signal": Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=entry_ts,
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct, 2),
                            "vol_ratio": round(vol_ratio, 2),
                        },
                    ),
                })
            else:
                stop_loss = opening_high
                risk = stop_loss - entry_price
                if risk <= 0 or risk / entry_price > 0.03:
                    continue
                take_profit = entry_price - risk * self.rr_ratio

                candidates.append({
                    "score": abs(gap_pct) * vol_ratio,
                    "signal": Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=entry_ts,
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct, 2),
                            "vol_ratio": round(vol_ratio, 2),
                        },
                    ),
                })

        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.max_trades_per_day]:
            signals.append(c["signal"])

        return signals

    @staticmethod
    def _avg_opening_volume(df: pd.DataFrame, current_date) -> float:
        all_dates = sorted(set(df.index.date))
        prev_dates = [d for d in all_dates if d < current_date]
        if not prev_dates:
            return 0
        opening_vols = []
        for d in prev_dates[-20:]:
            day_df = df[df.index.date == d]
            opening = day_df.between_time("09:30", "09:44")
            if not opening.empty:
                opening_vols.append(opening["volume"].sum())
        if not opening_vols:
            return 0
        return np.mean(opening_vols)
