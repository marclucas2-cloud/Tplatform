"""
Strategie : Breakdown Continuation

Edge structurel :
Quand un stock fait un nouveau Low of Day (LOD) apres 11:00 avec du volume,
les stops sous le LOD sont declenches et la pression vendeuse s'accelere.
Ce sont souvent des liquidations forcees de longs pieges le matin.

Regles :
- Tracker le LOD running pour chaque ticker
- Apres 11:00 : si prix casse sous le LOD de > 0.2%, volume > 1.5x moyenne,
  et prix sous VWAP → SHORT
- Stop : LOD precedent + 0.1%
- Target : 2x le risque
- Skip si stock deja down > 4% (risque SSR)
- Skip 12:00-12:30 (lunch fakeouts)
- Max 3 trades/jour, min price $15
- Timing : 11:00-14:30 ET
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap, volume_ratio
import config


# ── Parametres ──
BREAKDOWN_THRESHOLD_PCT = 0.002   # Casse LOD de > 0.2%
VOL_CONFIRM_MULT = 1.5            # Volume > 1.5x moyenne
MAX_DAY_DECLINE_PCT = 0.04        # Skip si deja down > 4% (SSR)
STOP_BUFFER_PCT = 0.001           # Stop = LOD precedent + 0.1%
TARGET_RISK_MULT = 2.0            # Target = 2x risk
MIN_PRICE = 15.0
MAX_TRADES_PER_DAY = 3

# Tickers a exclure
EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "TSLG", "TURB", "RWM",
    "PSQ", "SH", "SDS", "SMCL", "ZSL",
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO",
}


class BreakdownContinuationStrategy(BaseStrategy):
    name = "Breakdown Continuation"

    def __init__(
        self,
        breakdown_threshold: float = BREAKDOWN_THRESHOLD_PCT,
        vol_mult: float = VOL_CONFIRM_MULT,
        max_decline_pct: float = MAX_DAY_DECLINE_PCT,
        stop_buffer_pct: float = STOP_BUFFER_PCT,
        target_risk_mult: float = TARGET_RISK_MULT,
        min_price: float = MIN_PRICE,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.breakdown_threshold = breakdown_threshold
        self.vol_mult = vol_mult
        self.max_decline_pct = max_decline_pct
        self.stop_buffer_pct = stop_buffer_pct
        self.target_risk_mult = target_risk_mult
        self.min_price = min_price
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker in EXCLUDE:
                continue
            if ticker == config.BENCHMARK:
                continue
            if len(df) < 40:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < self.min_price:
                continue

            day_bars = df[df.index.date == date]
            if len(day_bars) < 10:
                continue

            today_open = day_bars.iloc[0]["open"]
            if today_open <= 0:
                continue

            # ── VWAP ──
            typical_price = (day_bars["high"] + day_bars["low"] + day_bars["close"]) / 3
            cum_tp_vol = (typical_price * day_bars["volume"]).cumsum()
            cum_vol = day_bars["volume"].cumsum()
            df_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)

            # ── Volume moyen glissant ──
            vol_avg = df["volume"].rolling(20, min_periods=5).mean()

            # ── Calculer le LOD running sur les barres du matin (9:30-10:59) ──
            morning_bars = day_bars.between_time("09:30", "10:59")
            if morning_bars.empty:
                continue

            morning_lod = morning_bars["low"].min()

            # ── Verifier que le stock n'est pas deja trop down ──
            current_check = day_bars.between_time("10:55", "11:05")
            if current_check.empty:
                continue
            price_at_11 = current_check.iloc[-1]["close"]
            day_decline = (today_open - price_at_11) / today_open
            if day_decline > self.max_decline_pct:
                continue

            # ── Iterer barres 11:00-14:30, skip 12:00-12:30 ──
            tradeable = day_bars.between_time("11:00", "14:30")
            if tradeable.empty:
                continue

            running_lod = morning_lod
            prev_lod = morning_lod
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                bar_time = ts.time()

                # ── Skip lunch period 12:00-12:30 (fakeouts) ──
                if pd.Timestamp("12:00").time() <= bar_time <= pd.Timestamp("12:30").time():
                    # Toujours tracker le LOD pendant le lunch
                    if bar["low"] < running_lod:
                        prev_lod = running_lod
                        running_lod = bar["low"]
                    continue

                price = bar["close"]

                # ── Verifier si le stock est trop down ──
                current_decline = (today_open - price) / today_open
                if current_decline > self.max_decline_pct:
                    break  # Trop down, risque SSR

                # ── Nouveau LOD? ──
                if bar["low"] < running_lod:
                    prev_lod = running_lod
                    running_lod = bar["low"]

                    # Verifier la cassure de LOD de > 0.2%
                    breakdown_pct = (prev_lod - bar["low"]) / prev_lod
                    if breakdown_pct < self.breakdown_threshold:
                        continue

                    # ── Volume > 1.5x moyenne ──
                    avg_v = vol_avg.get(ts, np.nan)
                    if pd.isna(avg_v) or avg_v <= 0:
                        avg_v = df["volume"].mean()
                    if bar["volume"] < self.vol_mult * avg_v:
                        continue

                    # ── Prix sous VWAP ──
                    vwap_val = df_vwap.get(ts, np.nan)
                    if pd.isna(vwap_val) or price >= vwap_val:
                        continue

                    # ── Signal SHORT ──
                    stop_loss = prev_lod * (1 + self.stop_buffer_pct)
                    risk = stop_loss - price
                    if risk <= 0:
                        continue
                    take_profit = price - risk * self.target_risk_mult

                    candidates.append({
                        "score": breakdown_pct * (bar["volume"] / avg_v),
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "breakdown_pct": round(breakdown_pct * 100, 2),
                                "prev_lod": round(prev_lod, 2),
                                "new_lod": round(running_lod, 2),
                                "vwap": round(vwap_val, 2),
                                "day_decline_pct": round(current_decline * 100, 2),
                            },
                        ),
                    })
                    signal_found = True

        # ── Trier par score et limiter ──
        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.max_trades_per_day]:
            signals.append(c["signal"])

        return signals
