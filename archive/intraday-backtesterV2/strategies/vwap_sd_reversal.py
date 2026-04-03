"""
Strategie 10 : VWAP SD Extreme Reversal

Edge structurel :
Le VWAP +/- 2.5 ecarts-types (SD) represente une zone extreme statistique.
Les market makers et algorithmes d'execution institutionnels (TWAP/VWAP algos)
utilisent ces niveaux comme bornes. Quand le prix atteint VWAP +/- 2.5 SD,
la probabilite de mean reversion est elevee car les market makers ont accumule
de l'inventaire dans l'autre direction et doivent revenir au prix moyen.

Regles :
- LONG : prix touche VWAP - 2.5 SD, puis rebond confirme (close > band).
  Volume de la barre de rebond > 1.2x moyenne.
- SHORT : prix touche VWAP + 2.5 SD, puis rejet confirme (close < band).
  Volume > 1.2x.
- Stop : VWAP +/- 3.5 SD
- Target : VWAP +/- 1 SD (profit partiel), puis VWAP
- Filtres : skip si < 30 barres, ADX > 45, ou channel 3 SD
- Timing : 10:30-15:00 ET
- Frequence : 0-2 trades/jour, un seul signal par ticker par jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio
import config


# ── Parametres par defaut ──
EXTREME_SD = 2.5          # Bande extreme (entree)
STOP_SD = 3.5             # Bande de stop
TARGET_PARTIAL_SD = 1.0   # Target partiel (retour a 1 SD)
VOL_MULTIPLIER = 1.2      # Volume minimum de la barre de rebond
ADX_MAX = 45              # Filtre trend trop fort
MIN_BARS = 30             # Nombre min de barres pour VWAP fiable
CHANNEL_SD = 3.0          # Skip si prix dans un channel de 3 SD


class VWAPSDReversalStrategy(BaseStrategy):
    name = "VWAP SD Extreme Reversal"

    def __init__(
        self,
        extreme_sd: float = EXTREME_SD,
        stop_sd: float = STOP_SD,
        target_sd: float = TARGET_PARTIAL_SD,
        vol_multiplier: float = VOL_MULTIPLIER,
        adx_max: float = ADX_MAX,
        min_bars: int = MIN_BARS,
        max_trades_per_day: int = 2,
    ):
        self.extreme_sd = extreme_sd
        self.stop_sd = stop_sd
        self.target_sd = target_sd
        self.vol_multiplier = vol_multiplier
        self.adx_max = adx_max
        self.min_bars = min_bars
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            if len(df) < self.min_bars:
                continue

            # ── Filtre volume journalier : on veut des stocks liquides (> 1M) ──
            total_volume = df["volume"].sum()
            if total_volume < 1_000_000:
                continue

            df = df.copy()

            # ── Calculer VWAP et SD cumulatifs intraday ──
            df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
            df["tp_vol"] = df["typical_price"] * df["volume"]
            df["cum_tp_vol"] = df["tp_vol"].cumsum()
            df["cum_vol"] = df["volume"].cumsum()
            df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)

            # SD = sqrt(cum((typical_price - vwap)^2 * volume) / cum(volume))
            df["sq_dev_vol"] = ((df["typical_price"] - df["vwap"]) ** 2) * df["volume"]
            df["cum_sq_dev_vol"] = df["sq_dev_vol"].cumsum()
            df["vwap_sd"] = np.sqrt(
                df["cum_sq_dev_vol"] / df["cum_vol"].replace(0, np.nan)
            )

            # ── Bandes VWAP ──
            df["upper_extreme"] = df["vwap"] + self.extreme_sd * df["vwap_sd"]
            df["lower_extreme"] = df["vwap"] - self.extreme_sd * df["vwap_sd"]
            df["upper_stop"] = df["vwap"] + self.stop_sd * df["vwap_sd"]
            df["lower_stop"] = df["vwap"] - self.stop_sd * df["vwap_sd"]
            df["upper_target"] = df["vwap"] + self.target_sd * df["vwap_sd"]
            df["lower_target"] = df["vwap"] - self.target_sd * df["vwap_sd"]

            # ── ADX pour filtre anti-trend ──
            adx_series = adx(df, period=14)

            # ── Volume moyen glissant (20 barres) ──
            df["vol_avg_20"] = df["volume"].rolling(20).mean()

            # ── Scanner les barres dans la fenetre 10:30-15:00 ET ──
            tradeable = df.between_time("10:30", "15:00")

            if len(tradeable) < self.min_bars:
                continue

            signal_found = False

            for i in range(1, len(tradeable)):
                if signal_found:
                    break
                if len(signals) >= self.max_trades_per_day:
                    break

                ts = tradeable.index[i]
                bar = tradeable.iloc[i]
                prev = tradeable.iloc[i - 1]

                # ── Skip si pas assez de barres accumulees pour un VWAP fiable ──
                bars_so_far = df.loc[:ts]
                if len(bars_so_far) < self.min_bars:
                    continue

                # ── Valeurs courantes (shift logique : on utilise prev bar pour les indicateurs) ──
                vwap_val = prev["vwap"]
                vwap_sd_val = prev["vwap_sd"]
                if pd.isna(vwap_val) or pd.isna(vwap_sd_val) or vwap_sd_val <= 0:
                    continue

                upper_extreme = prev["upper_extreme"]
                lower_extreme = prev["lower_extreme"]

                # ── Filtre ADX : pas de trend trop fort ──
                adx_idx = adx_series.index.get_indexer([ts], method="pad")
                if adx_idx[0] < 1:
                    continue
                # Anti-lookahead : ADX de la barre precedente
                current_adx = adx_series.iloc[adx_idx[0] - 1]
                if pd.isna(current_adx) or current_adx > self.adx_max:
                    continue

                # ── Filtre distribution bimodale : skip si prix dans channel 3 SD ──
                # On verifie que le prix a touche les extremes et n'est pas "colle"
                # entre +/- 3 SD depuis un moment (ce qui suggere une distribution bimodale)
                upper_3sd = prev["vwap"] + CHANNEL_SD * prev["vwap_sd"]
                lower_3sd = prev["vwap"] - CHANNEL_SD * prev["vwap_sd"]
                recent_bars = tradeable.iloc[max(0, i - 10):i]
                if len(recent_bars) > 5:
                    all_in_3sd_channel = (
                        (recent_bars["high"] >= upper_3sd).any()
                        and (recent_bars["low"] <= lower_3sd).any()
                    )
                    if all_in_3sd_channel:
                        continue

                # ── Filtre volume : barre de rebond > 1.2x moyenne 20 barres ──
                vol_avg = prev["vol_avg_20"]
                if pd.isna(vol_avg) or vol_avg <= 0:
                    continue
                if bar["volume"] < self.vol_multiplier * vol_avg:
                    continue

                # ── LONG : prix a touche VWAP - 2.5 SD puis rebond confirme ──
                # prev.low <= lower_extreme ET bar.close > lower_extreme (rebond)
                if prev["low"] <= lower_extreme and bar["close"] > lower_extreme:
                    entry_price = bar["close"]
                    # Stop a VWAP - 3.5 SD (bande suivante)
                    stop_loss = prev["vwap"] - self.stop_sd * prev["vwap_sd"]
                    # Target : retour a VWAP - 1 SD (conservateur)
                    take_profit = prev["vwap"] - self.target_sd * prev["vwap_sd"]

                    # Verifier que le RR est raisonnable
                    risk = entry_price - stop_loss
                    reward = take_profit - entry_price
                    if risk <= 0 or reward <= 0 or reward / risk < 0.8:
                        continue

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vwap": round(vwap_val, 4),
                            "vwap_sd": round(vwap_sd_val, 4),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(bar["volume"] / vol_avg, 2),
                            "distance_to_vwap_pct": round(
                                (entry_price - vwap_val) / vwap_val * 100, 2
                            ),
                        },
                    ))
                    signal_found = True

                # ── SHORT : prix a touche VWAP + 2.5 SD puis rejet confirme ──
                # prev.high >= upper_extreme ET bar.close < upper_extreme (rejet)
                elif prev["high"] >= upper_extreme and bar["close"] < upper_extreme:
                    entry_price = bar["close"]
                    # Stop a VWAP + 3.5 SD
                    stop_loss = prev["vwap"] + self.stop_sd * prev["vwap_sd"]
                    # Target : retour a VWAP + 1 SD
                    take_profit = prev["vwap"] + self.target_sd * prev["vwap_sd"]

                    # Verifier que le RR est raisonnable
                    risk = stop_loss - entry_price
                    reward = entry_price - take_profit
                    if risk <= 0 or reward <= 0 or reward / risk < 0.8:
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
                            "vwap": round(vwap_val, 4),
                            "vwap_sd": round(vwap_sd_val, 4),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(bar["volume"] / vol_avg, 2),
                            "distance_to_vwap_pct": round(
                                (entry_price - vwap_val) / vwap_val * 100, 2
                            ),
                        },
                    ))
                    signal_found = True

        return signals
