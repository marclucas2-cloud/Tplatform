"""
Failed Breakout Trap (Bull/Bear Trap)

Edge :
Quand un breakout du range (IB 30min) echoue et le prix revient dans le range,
les traders pieges creent un move violent dans l'autre direction.

Regles :
- Definir le range des 30 premieres minutes (IB = Initial Balance)
- Le prix casse le high/low du range
- Puis REVIENT dans le range dans les 2-3 barres suivantes
- Entree dans la direction OPPOSEE au breakout rate
- Stop : le high/low du breakout rate (l'extreme atteint)
- Target : l'autre cote du range (ou 1.5x le risque)
- Filtres :
  - Volume au breakout > 1.5x moyenne (confirme que des gens sont pieges)
  - ADX < 30 (pas de trend fort — les breakouts echouent plus souvent en range)
- Timing : 10:00-14:30 ET
- IMPORTANT : pas de lookahead — on definit "echoue" quand le prix revient
  DANS le range apres l'avoir casse (on attend la confirmation)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio
import config


# ── Parametres ──
IB_MINUTES = 30             # Initial Balance = 30 premieres minutes
BREAKOUT_CONFIRM_BARS = 3   # Le prix doit revenir dans le range dans les N barres
VOLUME_BREAKOUT_MULT = 1.5  # Volume au breakout > 1.5x moyenne
ADX_MAX = 28                # ADX < 28 (range market)
TARGET_RISK_MULT = 2.0      # Target = 2.0x le risque
MIN_RANGE_PCT = 0.003       # Range minimum = 0.3% du prix
MAX_RANGE_PCT = 0.020       # Range maximum = 2.0%
BREAKOUT_THRESHOLD = 0.001  # Le prix doit casser d'au moins 0.1% au-dela du range
MIN_PRICE = 20.0            # Prix minimum $20 (qualite)
MIN_VOLUME_DAY = 1_000_000  # Volume minimum 1M (tres haute liquidite seulement)

# Tickers leveraged/inverses a exclure
EXCLUDED_SUFFIXES = {"TQQQ", "SQQQ", "SPXU", "SPXS", "TZA", "TNA", "SOXL", "SOXS",
                     "UVXY", "UVIX", "VXX", "SVIX", "TSLL", "TSLQ", "TSLS", "TSDD",
                     "NVDL", "NVDX", "PSQ", "SH", "RWM", "SCO", "UCO", "JDST",
                     "TSLG", "SMCL", "TURB", "ZSL"}


class FailedBreakoutTrapStrategy(BaseStrategy):
    name = "Failed Breakout Trap"

    def __init__(
        self,
        ib_minutes: int = IB_MINUTES,
        confirm_bars: int = BREAKOUT_CONFIRM_BARS,
        vol_mult: float = VOLUME_BREAKOUT_MULT,
        adx_max: float = ADX_MAX,
        target_risk_mult: float = TARGET_RISK_MULT,
        min_range_pct: float = MIN_RANGE_PCT,
        max_range_pct: float = MAX_RANGE_PCT,
        min_price: float = MIN_PRICE,
        min_volume_day: int = MIN_VOLUME_DAY,
    ):
        self.ib_minutes = ib_minutes
        self.confirm_bars = confirm_bars
        self.vol_mult = vol_mult
        self.adx_max = adx_max
        self.target_risk_mult = target_risk_mult
        self.min_range_pct = min_range_pct
        self.max_range_pct = max_range_pct
        self.min_price = min_price
        self.min_volume_day = min_volume_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue
            if ticker in EXCLUDED_SUFFIXES:
                continue

            if len(df) < 40:
                continue

            # ── Filtre prix et volume minimum ──
            first_price = df.iloc[0]["open"]
            if first_price < self.min_price:
                continue
            total_vol = df["volume"].sum()
            if total_vol < self.min_volume_day:
                continue

            # ── Calculer le range IB (30 premieres minutes : 09:30-09:59) ──
            ib_end_minute = 30 + self.ib_minutes - 1  # 09:59 pour 30min
            ib_end_hour = 9 + ib_end_minute // 60
            ib_end_min = ib_end_minute % 60
            ib_bars = df.between_time("09:30", f"{ib_end_hour:02d}:{ib_end_min:02d}")

            if len(ib_bars) < 2:
                continue

            ib_high = ib_bars["high"].max()
            ib_low = ib_bars["low"].min()
            ib_range = ib_high - ib_low
            mid_price = (ib_high + ib_low) / 2

            if mid_price <= 0 or ib_range <= 0:
                continue

            # ── Filtre : range pas trop etroit ni trop large ──
            range_pct = ib_range / mid_price
            if range_pct < self.min_range_pct or range_pct > self.max_range_pct:
                continue

            # ── ADX : pas de trend fort ──
            adx_values = adx(df, period=14)
            # On prend l'ADX a la fin de l'IB (anti-lookahead)
            adx_at_ib = adx_values[adx_values.index <= ib_bars.index[-1]]
            if adx_at_ib.empty or pd.isna(adx_at_ib.iloc[-1]):
                # Si pas assez de donnees pour ADX, on skip le filtre
                current_adx = 25  # Valeur neutre
            else:
                current_adx = adx_at_ib.iloc[-1]

            if current_adx > self.adx_max:
                continue

            # ── Scanner les barres post-IB dans la fenetre 10:00-14:30 ──
            tradeable = df.between_time("10:00", "14:30")
            if tradeable.empty:
                continue

            # Volume moyen glissant
            vol_avg = df["volume"].rolling(20, min_periods=5).mean()

            # Tracking state pour detecter le pattern
            breakout_state = None  # None, "bull_breakout", "bear_breakout"
            breakout_extreme = None  # Le prix extreme atteint pendant le breakout
            breakout_bar_idx = None  # Quand le breakout a commence
            breakout_vol_ok = False
            signal_found = False

            for i, (ts, bar) in enumerate(tradeable.iterrows()):
                if signal_found:
                    break

                price = bar["close"]
                vol = bar["volume"]
                avg_v = vol_avg.get(ts, np.nan)
                if pd.isna(avg_v) or avg_v <= 0:
                    avg_v = df["volume"].mean()

                # ── Etat 1 : Detecter un breakout ──
                if breakout_state is None:
                    # Bull breakout : close au-dessus de l'IB high
                    if price > ib_high * (1 + BREAKOUT_THRESHOLD):
                        breakout_state = "bull_breakout"
                        breakout_extreme = bar["high"]
                        breakout_bar_idx = i
                        breakout_vol_ok = (vol > avg_v * self.vol_mult)
                    # Bear breakout : close en-dessous de l'IB low
                    elif price < ib_low * (1 - BREAKOUT_THRESHOLD):
                        breakout_state = "bear_breakout"
                        breakout_extreme = bar["low"]
                        breakout_bar_idx = i
                        breakout_vol_ok = (vol > avg_v * self.vol_mult)

                # ── Etat 2 : Suivre le breakout et detecter l'echec ──
                elif breakout_state == "bull_breakout":
                    bars_since = i - breakout_bar_idx

                    # Mettre a jour l'extreme
                    if bar["high"] > breakout_extreme:
                        breakout_extreme = bar["high"]

                    # Verifier volume au breakout si pas encore OK
                    if not breakout_vol_ok and vol > avg_v * self.vol_mult:
                        breakout_vol_ok = True

                    # Le prix revient dans le range = FAILED BREAKOUT
                    if price < ib_high and bars_since <= self.confirm_bars:
                        if breakout_vol_ok:
                            # SHORT : le bull breakout a echoue
                            stop_loss = breakout_extreme  # Au-dessus de l'extreme
                            risk = stop_loss - price
                            # Target : l'autre cote du range ou 1.5x risk
                            range_target = ib_low
                            risk_target = price - risk * self.target_risk_mult
                            take_profit = max(range_target, risk_target)  # Le plus proche

                            if risk > 0 and price - take_profit > 0:
                                signals.append(Signal(
                                    action="SHORT",
                                    ticker=ticker,
                                    entry_price=price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "trap_type": "bull_trap",
                                        "ib_high": round(ib_high, 2),
                                        "ib_low": round(ib_low, 2),
                                        "breakout_extreme": round(breakout_extreme, 2),
                                        "adx": round(current_adx, 1),
                                        "range_pct": round(range_pct * 100, 2),
                                    },
                                ))
                                signal_found = True
                        # Reset dans tous les cas
                        breakout_state = None
                        breakout_extreme = None
                        breakout_bar_idx = None
                        breakout_vol_ok = False

                    elif bars_since > self.confirm_bars:
                        # Breakout reussi — pas de trade, reset
                        breakout_state = None
                        breakout_extreme = None
                        breakout_bar_idx = None
                        breakout_vol_ok = False

                elif breakout_state == "bear_breakout":
                    bars_since = i - breakout_bar_idx

                    # Mettre a jour l'extreme
                    if bar["low"] < breakout_extreme:
                        breakout_extreme = bar["low"]

                    # Verifier volume au breakout si pas encore OK
                    if not breakout_vol_ok and vol > avg_v * self.vol_mult:
                        breakout_vol_ok = True

                    # Le prix revient dans le range = FAILED BREAKOUT
                    if price > ib_low and bars_since <= self.confirm_bars:
                        if breakout_vol_ok:
                            # LONG : le bear breakout a echoue
                            stop_loss = breakout_extreme  # En-dessous de l'extreme
                            risk = price - stop_loss
                            # Target : l'autre cote du range ou 1.5x risk
                            range_target = ib_high
                            risk_target = price + risk * self.target_risk_mult
                            take_profit = min(range_target, risk_target)  # Le plus proche

                            if risk > 0 and take_profit - price > 0:
                                signals.append(Signal(
                                    action="LONG",
                                    ticker=ticker,
                                    entry_price=price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "trap_type": "bear_trap",
                                        "ib_high": round(ib_high, 2),
                                        "ib_low": round(ib_low, 2),
                                        "breakout_extreme": round(breakout_extreme, 2),
                                        "adx": round(current_adx, 1),
                                        "range_pct": round(range_pct * 100, 2),
                                    },
                                ))
                                signal_found = True
                        # Reset dans tous les cas
                        breakout_state = None
                        breakout_extreme = None
                        breakout_bar_idx = None
                        breakout_vol_ok = False

                    elif bars_since > self.confirm_bars:
                        # Breakout reussi — pas de trade, reset
                        breakout_state = None
                        breakout_extreme = None
                        breakout_bar_idx = None
                        breakout_vol_ok = False

        return signals
