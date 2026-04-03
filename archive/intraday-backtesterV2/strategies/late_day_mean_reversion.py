"""
Strategie : Late Day Mean Reversion (Power Hour Reversion)

Edge structurel :
Les stocks qui ont fait un move > 3% dans la journee (9:30-14:00) tendent
a revert partiellement dans la derniere heure (14:00-15:55) quand :
1. Le RSI intraday est extreme (> 80 ou < 20)
2. Le prix s'est eloigne significativement du VWAP
3. Le volume a baisse l'apres-midi (le move s'essouffle)

Cet edge vient du profit-taking institutionnel et du "window dressing"
en fin de journee. Les algos de MOC (Market On Close) rebalancent
vers le VWAP, creant un pull-back naturel.

Regles :
- A 14:00, scanner les stocks avec move > 3% depuis l'open
- Le prix doit etre > 1.5% au-dessus du VWAP (LONG reversion = SHORT)
  ou < 1.5% en-dessous du VWAP (SHORT reversion = LONG)
- RSI intraday > 75 (pour SHORT) ou < 25 (pour LONG)
- Volume de la barre de 14:00 < volume moyen (momentum ralentit)
- SHORT les surperformants, LONG les sous-performants
- Stop : extension de 0.5x la distance prix-VWAP
- Target : VWAP (ou 50% de la distance prix-VWAP)
- Sortie : 15:55 au plus tard (intraday obligatoire)
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi
import config


# ── Tickers a exclure ──
EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "TSLG", "TURB", "RWM",
    "PSQ", "SH", "SDS", "SMCL", "SNDK", "ZSL",
    "SPYM", "RKLZ",
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA", "IVV", "ITOT", "VOO",
    "VEA", "VWO", "VXUS", "SCHB", "SCHD", "SCHF", "SCHG",
    "SCHH", "SCHX", "RSP", "LQD", "VCIT", "USHY",
    "PDBC", "PSLV",
}

# ── Parametres ──
MIN_DAY_MOVE_PCT = 0.03      # Move > 3% depuis l'open
VWAP_DISTANCE_PCT = 0.015    # Prix > 1.5% du VWAP
RSI_OVERBOUGHT = 75          # RSI > 75 = overbought
RSI_OVERSOLD = 25            # RSI < 25 = oversold
TARGET_REVERSION_PCT = 0.5   # Target = 50% de la distance prix-VWAP
STOP_EXTENSION_PCT = 0.25    # Stop = 25% extension au-dela du prix (tres serre)
MIN_PRICE = 8.0


class LateDayMeanReversionStrategy(BaseStrategy):
    name = "Late Day Mean Reversion"

    def __init__(
        self,
        min_move_pct: float = MIN_DAY_MOVE_PCT,
        vwap_distance_pct: float = VWAP_DISTANCE_PCT,
        target_reversion: float = TARGET_REVERSION_PCT,
        stop_extension: float = STOP_EXTENSION_PCT,
        max_trades_per_day: int = 3,
    ):
        self.min_move_pct = min_move_pct
        self.vwap_distance_pct = vwap_distance_pct
        self.target_reversion = target_reversion
        self.stop_extension = stop_extension
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker in EXCLUDE:
                continue
            if len(df) < 40:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # ── VWAP ──
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            cum_tp_vol = (typical_price * df["volume"]).cumsum()
            cum_vol = df["volume"].cumsum()
            df_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)

            # ── RSI ──
            df_rsi = rsi(df["close"], period=14)

            # ── Volume moyen ──
            vol_avg = df["volume"].rolling(20, min_periods=5).mean()

            # ── Scanner a 14:00 ──
            scan_bars = df.between_time("14:00", "14:14")
            if len(scan_bars) < 1:
                continue

            scan_bar = scan_bars.iloc[0]
            scan_ts = scan_bars.index[0]
            current_price = scan_bar["close"]

            # ── Move depuis l'open ──
            day_move = (current_price - first_price) / first_price

            if abs(day_move) < self.min_move_pct:
                continue

            # ── VWAP distance ──
            vwap_val = df_vwap.loc[scan_ts] if scan_ts in df_vwap.index else np.nan
            if pd.isna(vwap_val) or vwap_val <= 0:
                continue

            vwap_dist = (current_price - vwap_val) / vwap_val

            if abs(vwap_dist) < self.vwap_distance_pct:
                continue

            # ── RSI ──
            rsi_val = df_rsi.loc[scan_ts] if scan_ts in df_rsi.index else np.nan
            if pd.isna(rsi_val):
                continue

            # ── Volume diminue (momentum ralentit) ──
            current_vol = scan_bar["volume"]
            avg_vol = vol_avg.loc[scan_ts] if scan_ts in vol_avg.index else np.nan
            if pd.isna(avg_vol) or avg_vol <= 0:
                continue

            # Le volume DOIT etre en baisse vs moyenne (essoufflement)
            vol_ratio = current_vol / avg_vol
            if vol_ratio > 1.5:  # Trop de volume = le move continue
                continue

            # ── Determiner la direction de la reversion ──
            if day_move > 0 and vwap_dist > 0 and rsi_val > RSI_OVERBOUGHT:
                # Le stock a trop monte → SHORT (reversion vers le bas)
                distance = current_price - vwap_val
                target_move = distance * self.target_reversion
                stop_extension_val = distance * self.stop_extension

                entry_price = current_price
                stop_loss = current_price + stop_extension_val
                take_profit = current_price - target_move

                risk = stop_loss - entry_price
                reward = entry_price - take_profit
                if risk <= 0 or reward <= 0:
                    continue

                candidates.append({
                    "score": abs(vwap_dist) * abs(rsi_val - 50) / 50,
                    "signal": Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=scan_ts,
                        metadata={
                            "strategy": self.name,
                            "day_move_pct": round(day_move * 100, 2),
                            "vwap_dist_pct": round(vwap_dist * 100, 2),
                            "rsi": round(rsi_val, 1),
                            "vol_ratio": round(vol_ratio, 2),
                        },
                    ),
                })

            elif day_move < 0 and vwap_dist < 0 and rsi_val < RSI_OVERSOLD:
                # Le stock a trop baisse → LONG (reversion vers le haut)
                distance = vwap_val - current_price
                target_move = distance * self.target_reversion
                stop_extension_val = distance * self.stop_extension

                entry_price = current_price
                stop_loss = current_price - stop_extension_val
                take_profit = current_price + target_move

                risk = entry_price - stop_loss
                reward = take_profit - entry_price
                if risk <= 0 or reward <= 0:
                    continue

                candidates.append({
                    "score": abs(vwap_dist) * abs(rsi_val - 50) / 50,
                    "signal": Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=scan_ts,
                        metadata={
                            "strategy": self.name,
                            "day_move_pct": round(day_move * 100, 2),
                            "vwap_dist_pct": round(vwap_dist * 100, 2),
                            "rsi": round(rsi_val, 1),
                            "vol_ratio": round(vol_ratio, 2),
                        },
                    ),
                })

        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.max_trades_per_day]:
            signals.append(c["signal"])

        return signals
