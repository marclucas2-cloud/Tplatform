"""
Stratégie 7 : MOC Imbalance Anticipation (15:00-15:55 ET)

Edge structurel :
Les ordres Market-on-Close (MOC) créent des déséquilibres prévisibles.
Les imbalances MOC sont publiées à 15:50 ET par NYSE, mais on peut ANTICIPER
la direction en observant le flux de la dernière heure : si le volume buy/sell
ratio est fortement déséquilibré entre 15:00-15:30, le MOC amplifiera ce
mouvement. Les stocks avec fort volume power hour dans une direction tendent
à accélérer dans les 15 dernières minutes.

Règles :
- Signal 15:00-15:30 ET, sortie forcée à 15:55 ET (avant close)
- LONG : volume buy ratio > 60% (close > open sur majorité des barres 5M
  + volume > 1.5x moyenne) + prix au-dessus du VWAP
- SHORT : volume sell ratio > 60% (close < open majorité + volume > 1.5x)
  + prix sous VWAP
- Stop : 0.3% (serré, holding < 30 min)
- Target : 0.5% (haute probabilité)
- Filtres : volume power hour >= moyenne, move du jour < 3%, ADX >= 15
- Fréquence : 0-3 trades/jour, un seul signal par ticker par jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap, adx
import config


class MOCImbalanceStrategy(BaseStrategy):
    name = "MOC Imbalance Anticipation"

    # Tickers ciblés : gros volumes, forte liquidité MOC
    FOCUS_TICKERS = [
        "SPY", "QQQ", "AAPL", "NVDA", "TSLA",
        "META", "AMZN", "GOOGL", "MSFT", "JPM",
    ]

    def __init__(
        self,
        buy_sell_ratio_threshold: float = 0.60,
        vol_multiplier: float = 1.5,
        stop_pct: float = 0.003,
        target_pct: float = 0.005,
        max_day_move_pct: float = 3.0,
        adx_threshold: float = 15.0,
        max_trades_per_day: int = 3,
    ):
        self.buy_sell_ratio_threshold = buy_sell_ratio_threshold
        self.vol_multiplier = vol_multiplier
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_day_move_pct = max_day_move_pct
        self.adx_threshold = adx_threshold
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return self.FOCUS_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            # Focus uniquement sur les gros volumes
            if ticker not in self.FOCUS_TICKERS:
                continue

            if len(df) < 40:
                continue

            # ── Filtre ADX : besoin de direction (anti-lookahead : shift 1) ──
            adx_series = adx(df.copy(), period=14)
            # Prendre l'ADX à la dernière barre AVANT 15:00
            bars_before_3pm = adx_series[adx_series.index.time < pd.Timestamp("15:00").time()]
            if bars_before_3pm.empty:
                continue
            current_adx = bars_before_3pm.iloc[-1]
            if pd.isna(current_adx) or current_adx < self.adx_threshold:
                continue

            # ── Filtre : move du jour < 3% (pas trop late) ──
            morning = df.between_time("09:30", "09:31")
            if morning.empty:
                continue
            day_open = morning.iloc[0]["open"]
            bars_at_3pm = df.between_time("14:55", "15:05")
            if bars_at_3pm.empty:
                continue
            price_at_3pm = bars_at_3pm.iloc[0]["close"]
            day_move_pct = abs((price_at_3pm - day_open) / day_open) * 100
            if day_move_pct > self.max_day_move_pct:
                continue

            # ── Calculer le VWAP pour le filtre momentum ──
            vwap_series = vwap(df)

            # ── Fenêtre 15:00-15:30 : analyser le buy/sell ratio ──
            power_window = df.between_time("15:00", "15:29")
            if len(power_window) < 3:
                continue

            # ── Filtre : volume power hour >= moyenne journalière ──
            # Volume moyen par barre sur la journée entière
            total_bars = len(df)
            if total_bars == 0:
                continue
            avg_vol_per_bar = df["volume"].mean()
            power_avg_vol = power_window["volume"].mean()
            if avg_vol_per_bar > 0 and power_avg_vol < avg_vol_per_bar:
                continue

            # ── Approximer le buy/sell ratio ──
            # Barres "buy" : close > open (pression acheteuse)
            # Barres "sell" : close < open (pression vendeuse)
            buy_bars = (power_window["close"] > power_window["open"]).sum()
            sell_bars = (power_window["close"] < power_window["open"]).sum()
            total_bars_window = len(power_window)

            if total_bars_window == 0:
                continue

            buy_ratio = buy_bars / total_bars_window
            sell_ratio = sell_bars / total_bars_window

            # ── Filtre volume : les barres de la fenêtre > 1.5x moyenne ──
            high_vol_bars = (power_window["volume"] > avg_vol_per_bar * self.vol_multiplier).sum()
            has_volume_conviction = high_vol_bars >= total_bars_window * 0.3  # Au moins 30% des barres

            if not has_volume_conviction:
                continue

            # ── VWAP au moment de l'évaluation (dernière barre de la fenêtre) ──
            last_bar_ts = power_window.index[-1]
            if last_bar_ts not in vwap_series.index:
                continue
            current_vwap = vwap_series.loc[last_bar_ts]
            last_price = power_window.iloc[-1]["close"]

            if pd.isna(current_vwap):
                continue

            # ── Déterminer la direction ──
            direction = None
            if buy_ratio >= self.buy_sell_ratio_threshold and last_price > current_vwap:
                direction = "LONG"
                score = buy_ratio
            elif sell_ratio >= self.buy_sell_ratio_threshold and last_price < current_vwap:
                direction = "SHORT"
                score = sell_ratio

            if direction is not None:
                candidates.append({
                    "ticker": ticker,
                    "direction": direction,
                    "score": score,
                    "entry_price": last_price,
                    "entry_ts": last_bar_ts,
                    "buy_ratio": round(buy_ratio, 2),
                    "sell_ratio": round(sell_ratio, 2),
                    "adx": round(current_adx, 1),
                    "day_move_pct": round(day_move_pct, 2),
                    "vwap": round(current_vwap, 4),
                })

        # ── Trier par score (plus fort déséquilibre d'abord) ──
        candidates.sort(key=lambda x: x["score"], reverse=True)
        candidates = candidates[:self.max_trades_per_day]

        for c in candidates:
            entry_price = c["entry_price"]

            if c["direction"] == "LONG":
                stop_loss = entry_price * (1 - self.stop_pct)
                take_profit = entry_price * (1 + self.target_pct)
            else:  # SHORT
                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action=c["direction"],
                ticker=c["ticker"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=c["entry_ts"],
                metadata={
                    "strategy": self.name,
                    "buy_ratio": c["buy_ratio"],
                    "sell_ratio": c["sell_ratio"],
                    "adx": c["adx"],
                    "day_move_pct": c["day_move_pct"],
                    "vwap": c["vwap"],
                },
            ))

        return signals
