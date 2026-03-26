"""
Strategie : Failed Rally Short

Edge structurel :
Les stocks en downtrend qui rallye vers le VWAP mais echouent a le depasser
sont rejetes par les vendeurs institutionnels qui utilisent le VWAP comme
reference de prix. L'echec au VWAP confirme la pression vendeuse et
le stock reprend sa baisse.

Regles :
- Le stock doit etre down > 0.5% depuis l'open (downtrend intraday)
- Le prix touche le VWAP (a 0.1% pres) mais NE ferme PAS au-dessus
- La barre suivante doit etre rouge (confirmation du rejet)
- Skip si le prix a deja croise le VWAP 2+ fois aujourd'hui (choppy)
- Stop : VWAP + 0.3%
- Target : retour au LOD ou 2x le risque (le plus proche)
- Max 2 trades/jour, min price $15
- Timing : 10:30-14:30 ET
- Tickers : SPY, QQQ, NVDA, TSLA, AMD, META
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap
import config


# ── Parametres ──
MIN_DOWN_FROM_OPEN = -0.005  # Stock down > 0.5% depuis l'open
VWAP_TOUCH_TOLERANCE = 0.001 # Touche VWAP a 0.1% pres
VWAP_STOP_BUFFER = 0.003     # Stop = VWAP + 0.3%
TARGET_RISK_MULT = 2.0       # Target = 2x risk (si LOD pas assez loin)
MAX_VWAP_CROSSES = 2         # Skip si > 2 croisements VWAP
MIN_PRICE = 15.0
MAX_TRADES_PER_DAY = 2

# Tickers focus
FOCUS_TICKERS = ["SPY", "QQQ", "NVDA", "TSLA", "AMD", "META"]


class FailedRallyShortStrategy(BaseStrategy):
    name = "Failed Rally Short"

    def __init__(
        self,
        min_down_from_open: float = MIN_DOWN_FROM_OPEN,
        vwap_touch_tolerance: float = VWAP_TOUCH_TOLERANCE,
        vwap_stop_buffer: float = VWAP_STOP_BUFFER,
        target_risk_mult: float = TARGET_RISK_MULT,
        max_vwap_crosses: int = MAX_VWAP_CROSSES,
        min_price: float = MIN_PRICE,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.min_down_from_open = min_down_from_open
        self.vwap_touch_tolerance = vwap_touch_tolerance
        self.vwap_stop_buffer = vwap_stop_buffer
        self.target_risk_mult = target_risk_mult
        self.max_vwap_crosses = max_vwap_crosses
        self.min_price = min_price
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return FOCUS_TICKERS[:]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trade_count = 0

        for ticker in FOCUS_TICKERS:
            if trade_count >= self.max_trades_per_day:
                break
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 30:
                continue

            day_bars = df[df.index.date == date]
            if len(day_bars) < 15:
                continue

            today_open = day_bars.iloc[0]["open"]
            if today_open < self.min_price or today_open <= 0:
                continue

            # ── Calculer VWAP intraday ──
            typical_price = (day_bars["high"] + day_bars["low"] + day_bars["close"]) / 3
            cum_tp_vol = (typical_price * day_bars["volume"]).cumsum()
            cum_vol = day_bars["volume"].cumsum()
            df_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)

            # ── LOD tracker ──
            running_lod = day_bars.iloc[0]["low"]

            # ── VWAP cross counter ──
            vwap_crosses = 0
            prev_above_vwap = None

            # ── Iterer barres 10:30-14:30 ──
            tradeable = day_bars.between_time("10:30", "14:30")
            if tradeable.empty:
                continue

            # State pour detecter le pattern (touch VWAP + reject)
            vwap_touched = False
            vwap_touch_ts = None
            vwap_at_touch = None
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                price = bar["close"]
                vwap_val = df_vwap.get(ts, np.nan)
                if pd.isna(vwap_val) or vwap_val <= 0:
                    continue

                # ── Mettre a jour LOD ──
                if bar["low"] < running_lod:
                    running_lod = bar["low"]

                # ── Compter les croisements VWAP ──
                currently_above = price > vwap_val
                if prev_above_vwap is not None and currently_above != prev_above_vwap:
                    vwap_crosses += 1
                prev_above_vwap = currently_above

                # ── Skip si trop de croisements (choppy) ──
                if vwap_crosses >= self.max_vwap_crosses:
                    break

                # ── Performance depuis l'open : doit etre down ──
                perf_from_open = (price - today_open) / today_open
                if perf_from_open > self.min_down_from_open:
                    # Pas assez down — reset touch state
                    vwap_touched = False
                    continue

                # ── Etape 1 : Detecter touch VWAP ──
                if not vwap_touched:
                    # Le high touche le VWAP (a tolerance pres) mais close en-dessous
                    vwap_distance_high = (bar["high"] - vwap_val) / vwap_val
                    vwap_distance_close = (price - vwap_val) / vwap_val

                    # Le high est proche du VWAP (touch)
                    if abs(vwap_distance_high) <= self.vwap_touch_tolerance or vwap_distance_high > 0:
                        # Mais le close est SOUS le VWAP (rejet)
                        if vwap_distance_close < 0:
                            vwap_touched = True
                            vwap_touch_ts = ts
                            vwap_at_touch = vwap_val
                    continue

                # ── Etape 2 : Confirmation — la barre apres le touch doit etre rouge ──
                if vwap_touched:
                    # La barre actuelle est la "next bar" apres le touch
                    if bar["close"] >= bar["open"]:
                        # Barre verte = pas de confirmation, reset
                        vwap_touched = False
                        continue

                    # ── Confirmation : barre rouge apres rejet VWAP ──
                    entry_price = price
                    stop_loss = vwap_at_touch * (1 + self.vwap_stop_buffer)
                    risk = stop_loss - entry_price

                    if risk <= 0:
                        vwap_touched = False
                        continue

                    # Target : LOD ou 2x risk (le plus proche)
                    lod_target = running_lod
                    risk_target = entry_price - risk * self.target_risk_mult
                    take_profit = max(lod_target, risk_target)  # Le plus proche (le max)

                    reward = entry_price - take_profit
                    if reward <= 0:
                        vwap_touched = False
                        continue

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vwap_at_touch": round(vwap_at_touch, 2),
                            "lod": round(running_lod, 2),
                            "perf_from_open": round(perf_from_open * 100, 2),
                            "vwap_crosses": vwap_crosses,
                            "risk_reward": round(reward / risk, 2),
                        },
                    ))
                    signal_found = True
                    trade_count += 1

        return signals
