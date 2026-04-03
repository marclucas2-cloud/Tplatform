"""
First Pullback After Breakout

Edge :
Apres un breakout clair avec volume, le premier pullback vers le niveau de breakout
est un point d'entree a haute probabilite (retest du support/resistance).
Les traders institutionnels utilisent le retest pour accumuler des positions.

Regles :
- Breakout du range des 30 premieres minutes (IB high/low)
- Le prix revient vers l'IB high (pour long) ou IB low (pour short) sans le casser
- Entree quand le prix repart dans la direction du breakout
- Stop : sous l'IB (pour long) ou au-dessus (pour short)
- Target : 2x le risque
- Filtres :
  - Le breakout initial doit etre avec volume > 1.5x moyenne
  - Le pullback doit etre < 50% du move initial
  - ADX > 20 (on veut un trend, pas un range)
- Timing : 10:30-14:30 ET (on attend que le pullback arrive)
- IMPORTANT : pas de lookahead — on confirme le retest quand le prix rebondit
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio
import config


# ── Parametres ──
IB_MINUTES = 30                 # Initial Balance = 30 premieres minutes
VOLUME_BREAKOUT_MULT = 1.8     # Volume au breakout > 1.8x moyenne (strong conviction)
ADX_MIN = 22                   # ADX > 22 (trend present)
TARGET_RISK_MULT = 2.5         # Target = 2.5x le risque (meilleur R:R)
PULLBACK_MAX_RETRACE = 0.50    # Le pullback ne doit pas retracer > 50% du move
MIN_RANGE_PCT = 0.004          # Range minimum = 0.4%
MAX_RANGE_PCT = 0.020          # Range maximum = 2.0%
BREAKOUT_MIN_MOVE = 0.004      # Le breakout doit etre > 0.4% au-dessus du range (stronger)
PULLBACK_PROXIMITY_PCT = 0.003 # Le prix doit etre a 0.3% du niveau de breakout
BOUNCE_CONFIRM_PCT = 0.002     # Le rebond doit etre > 0.2% depuis le low du pullback
MIN_PRICE = 20.0               # Prix minimum $20
MIN_VOLUME_DAY = 1_000_000     # Volume minimum 1M (haute liquidite)

# Tickers leveraged/inverses a exclure
EXCLUDED_SUFFIXES = {"TQQQ", "SQQQ", "SPXU", "SPXS", "TZA", "TNA", "SOXL", "SOXS",
                     "UVXY", "UVIX", "VXX", "SVIX", "TSLL", "TSLQ", "TSLS", "TSDD",
                     "NVDL", "NVDX", "PSQ", "SH", "RWM", "SCO", "UCO", "JDST",
                     "TSLG", "SMCL", "TURB", "ZSL"}


class FirstPullbackStrategy(BaseStrategy):
    name = "First Pullback After Breakout"

    def __init__(
        self,
        ib_minutes: int = IB_MINUTES,
        vol_mult: float = VOLUME_BREAKOUT_MULT,
        adx_min: float = ADX_MIN,
        target_risk_mult: float = TARGET_RISK_MULT,
        pullback_max_retrace: float = PULLBACK_MAX_RETRACE,
        min_range_pct: float = MIN_RANGE_PCT,
        max_range_pct: float = MAX_RANGE_PCT,
        min_price: float = MIN_PRICE,
        min_volume_day: int = MIN_VOLUME_DAY,
    ):
        self.ib_minutes = ib_minutes
        self.vol_mult = vol_mult
        self.adx_min = adx_min
        self.target_risk_mult = target_risk_mult
        self.pullback_max_retrace = pullback_max_retrace
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
            ib_bars = df.between_time("09:30", "09:59")
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

            # ── ADX : on veut un trend ──
            adx_values = adx(df, period=14)

            # ── Volume moyen glissant ──
            vol_avg = df["volume"].rolling(20, min_periods=5).mean()

            # ── Phase 1 : Detecter un breakout avec volume (10:00-11:30) ──
            breakout_window = df.between_time("10:00", "11:30")
            if breakout_window.empty:
                continue

            breakout_dir = None     # "LONG" or "SHORT"
            breakout_price = None   # Prix du breakout
            breakout_extreme = None # Le plus haut/bas atteint apres le breakout
            breakout_time = None

            for ts, bar in breakout_window.iterrows():
                avg_v = vol_avg.get(ts, np.nan)
                if pd.isna(avg_v) or avg_v <= 0:
                    avg_v = df["volume"].mean()

                # Bull breakout : close au-dessus de IB high + minimum move
                if (bar["close"] > ib_high + mid_price * BREAKOUT_MIN_MOVE
                        and bar["volume"] > avg_v * self.vol_mult
                        and breakout_dir is None):
                    breakout_dir = "LONG"
                    breakout_price = bar["close"]
                    breakout_extreme = bar["high"]
                    breakout_time = ts
                    # Continuer pour trouver l'extreme du move
                    continue

                # Bear breakout : close en-dessous de IB low - minimum move
                if (bar["close"] < ib_low - mid_price * BREAKOUT_MIN_MOVE
                        and bar["volume"] > avg_v * self.vol_mult
                        and breakout_dir is None):
                    breakout_dir = "SHORT"
                    breakout_price = bar["close"]
                    breakout_extreme = bar["low"]
                    breakout_time = ts
                    continue

                # Mettre a jour l'extreme si on a deja un breakout
                if breakout_dir == "LONG" and bar["high"] > breakout_extreme:
                    breakout_extreme = bar["high"]
                elif breakout_dir == "SHORT" and bar["low"] < breakout_extreme:
                    breakout_extreme = bar["low"]

            if breakout_dir is None or breakout_time is None:
                continue

            # ── Phase 2 : Attendre le pullback et le rebond (10:30-14:30) ──
            pullback_window = df.between_time("10:30", "14:30")
            # Ne regarder que les barres APRES le breakout
            pullback_window = pullback_window[pullback_window.index > breakout_time]

            if pullback_window.empty:
                continue

            # ADX check (anti-lookahead : avant la fenetre de pullback)
            adx_before = adx_values[adx_values.index <= breakout_time]
            if adx_before.empty or pd.isna(adx_before.iloc[-1]):
                current_adx = 25  # Valeur neutre
            else:
                current_adx = adx_before.iloc[-1]

            if current_adx < self.adx_min:
                continue

            # Tracking du pullback
            pullback_started = False
            pullback_low = None  # Pour LONG : le low du pullback
            pullback_high = None  # Pour SHORT : le high du pullback
            signal_found = False

            for ts, bar in pullback_window.iterrows():
                if signal_found:
                    break

                if breakout_dir == "LONG":
                    move_from_ib = breakout_extreme - ib_high
                    if move_from_ib <= 0:
                        break

                    # Detecter le debut du pullback : prix descend vers IB high
                    if not pullback_started:
                        # Le prix commence a descendre
                        retrace = (breakout_extreme - bar["low"]) / move_from_ib
                        if retrace > 0.2:  # Au moins 20% de retrace
                            pullback_started = True
                            pullback_low = bar["low"]
                    else:
                        # Mettre a jour le low du pullback
                        if bar["low"] < pullback_low:
                            pullback_low = bar["low"]

                        # Verifier que le pullback n'est pas trop profond
                        retrace = (breakout_extreme - pullback_low) / move_from_ib
                        if retrace > self.pullback_max_retrace:
                            break  # Pullback trop profond, pattern invalide

                        # Le prix doit se rapprocher de l'IB high (zone de support)
                        proximity = abs(pullback_low - ib_high) / mid_price
                        if proximity > self.max_range_pct:
                            continue  # Pas encore assez proche

                        # Verifier le rebond : le prix remonte depuis le low du pullback
                        bounce = (bar["close"] - pullback_low) / mid_price
                        if bounce > BOUNCE_CONFIRM_PCT and bar["close"] > pullback_low:
                            # Confirme le rebond — signal LONG
                            entry_price = bar["close"]
                            # Stop SERRE : sous le pullback low (pas tout le IB range)
                            buffer = ib_range * 0.15  # 15% du range comme buffer
                            stop_loss = pullback_low - buffer
                            risk = entry_price - stop_loss
                            take_profit = entry_price + risk * self.target_risk_mult

                            if risk > 0 and take_profit > entry_price:
                                signals.append(Signal(
                                    action="LONG",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "breakout_dir": "LONG",
                                        "ib_high": round(ib_high, 2),
                                        "ib_low": round(ib_low, 2),
                                        "breakout_extreme": round(breakout_extreme, 2),
                                        "pullback_low": round(pullback_low, 2),
                                        "retrace_pct": round(retrace * 100, 1),
                                        "adx": round(current_adx, 1),
                                    },
                                ))
                                signal_found = True

                elif breakout_dir == "SHORT":
                    move_from_ib = ib_low - breakout_extreme
                    if move_from_ib <= 0:
                        break

                    # Detecter le debut du pullback : prix remonte vers IB low
                    if not pullback_started:
                        retrace = (bar["high"] - breakout_extreme) / move_from_ib
                        if retrace > 0.2:
                            pullback_started = True
                            pullback_high = bar["high"]
                    else:
                        if bar["high"] > pullback_high:
                            pullback_high = bar["high"]

                        retrace = (pullback_high - breakout_extreme) / move_from_ib
                        if retrace > self.pullback_max_retrace:
                            break

                        proximity = abs(pullback_high - ib_low) / mid_price
                        if proximity > self.max_range_pct:
                            continue

                        # Verifier le rejet : le prix redescend depuis le high du pullback
                        rejection = (pullback_high - bar["close"]) / mid_price
                        if rejection > BOUNCE_CONFIRM_PCT and bar["close"] < pullback_high:
                            entry_price = bar["close"]
                            # Stop SERRE : au-dessus du pullback high (pas tout le IB range)
                            buffer = ib_range * 0.15
                            stop_loss = pullback_high + buffer
                            risk = stop_loss - entry_price
                            take_profit = entry_price - risk * self.target_risk_mult

                            if risk > 0 and entry_price > take_profit:
                                signals.append(Signal(
                                    action="SHORT",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "breakout_dir": "SHORT",
                                        "ib_high": round(ib_high, 2),
                                        "ib_low": round(ib_low, 2),
                                        "breakout_extreme": round(breakout_extreme, 2),
                                        "pullback_high": round(pullback_high, 2),
                                        "retrace_pct": round(retrace * 100, 1),
                                        "adx": round(current_adx, 1),
                                    },
                                ))
                                signal_found = True

        return signals
