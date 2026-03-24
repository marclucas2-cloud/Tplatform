"""
Strategie : Volume Climax Reversal

Edge structurel :
Un spike de volume extreme (> 3x la moyenne) accompagne d'une longue meche
indique l'absorption : les market makers absorbent un flux directionnel massif.
Apres cette capitulation, le prix revient typiquement au VWAP car le
desequilibre a ete resorbe.

Regles :
- LONG : volume > 3x moyenne 20 barres + lower wick > 60% du range de la barre
         + close dans la moitie superieure + prix sous VWAP
- SHORT : volume > 3x + upper wick > 60% + close moitie inferieure + prix > VWAP
- Stop : 1.5x ATR(14) au-dela de l'extreme de la barre climax
- Target : VWAP du jour
- Filtres : ADX < 40 (pas de trend extreme), move depuis open < 5%,
  pas de trade dans les 30 dernieres minutes, ATR > 1%
- Frequence : 0-2 trades/jour, un seul signal par ticker par jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx
import config


class VolumeClimaxReversalStrategy(BaseStrategy):
    name = "Volume Climax Reversal"

    def __init__(
        self,
        vol_spike_threshold: float = 3.0,
        wick_pct_threshold: float = 0.6,
        atr_stop_multiplier: float = 1.5,
        max_adx: float = 40.0,
        max_move_from_open_pct: float = 0.05,
        min_atr_pct: float = 0.01,
        vol_lookback: int = 20,
        atr_period: int = 14,
        max_trades_per_day: int = 2,
    ):
        self.vol_spike_threshold = vol_spike_threshold
        self.wick_pct_threshold = wick_pct_threshold
        self.atr_stop_multiplier = atr_stop_multiplier
        self.max_adx = max_adx
        self.max_move_from_open_pct = max_move_from_open_pct
        self.min_atr_pct = min_atr_pct
        self.vol_lookback = vol_lookback
        self.atr_period = atr_period
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            if len(df) < 40:
                continue

            # ── Filtre ATR : on veut des tickers avec ATR > 1% ──
            atr_pct = self._compute_atr_pct(df, self.atr_period)
            if atr_pct is None or atr_pct < self.min_atr_pct:
                continue

            # ── Calculer le VWAP manuellement (reset chaque jour) ──
            df_copy = df.copy()
            typical_price = (df_copy["high"] + df_copy["low"] + df_copy["close"]) / 3
            cum_tp_vol = (typical_price * df_copy["volume"]).cumsum()
            cum_vol = df_copy["volume"].cumsum()
            df_copy["vwap"] = cum_tp_vol / cum_vol.replace(0, np.nan)

            # ── Calculer ATR(14) pour le stop ──
            atr_series = self._compute_atr_series(df_copy, self.atr_period)

            # ── Calculer ADX pour le filtre ──
            adx_series = adx(df_copy, period=self.atr_period)

            # ── Prix d'ouverture du jour ──
            day_open = df_copy.iloc[0]["open"]

            # ── Scanner les barres (10:00-15:00 ET, eviter ouverture et fermeture) ──
            tradeable = df_copy.between_time("10:00", "15:00")

            # Exclure les 30 dernieres minutes de la journee
            # (15:00 est deja la limite, mais on verifie aussi 15:30+ n'est pas inclus)
            tradeable_filtered = tradeable.between_time("10:00", "15:25")

            signal_found = False

            for ts, bar in tradeable_filtered.iterrows():
                if signal_found:
                    break

                if len(signals) >= self.max_trades_per_day:
                    break

                # ── Filtre : move depuis l'open < 5% (pas d'evenement exogene) ──
                move_from_open = abs(bar["close"] - day_open) / day_open
                if move_from_open > self.max_move_from_open_pct:
                    continue

                # ── Filtre ADX < 40 (pas de trend extreme, sinon pas de reversal) ──
                # Utiliser la valeur precedente (anti-lookahead)
                adx_idx = adx_series.index.get_indexer([ts], method="pad")
                if adx_idx[0] < 1:
                    continue
                current_adx = adx_series.iloc[adx_idx[0] - 1]
                if pd.isna(current_adx) or current_adx > self.max_adx:
                    continue

                # ── Volume spike : volume > 3x la moyenne des 20 barres precedentes ──
                bars_before = df_copy.loc[:ts]
                if len(bars_before) < self.vol_lookback + 1:
                    continue
                avg_vol = bars_before["volume"].iloc[-(self.vol_lookback + 1):-1].mean()
                if avg_vol <= 0:
                    continue
                vol_ratio = bar["volume"] / avg_vol
                if vol_ratio < self.vol_spike_threshold:
                    continue

                # ── Analyse de la meche (wick) ──
                bar_range = bar["high"] - bar["low"]
                if bar_range <= 0:
                    continue

                lower_wick = min(bar["open"], bar["close"]) - bar["low"]
                upper_wick = bar["high"] - max(bar["open"], bar["close"])
                lower_wick_pct = lower_wick / bar_range
                upper_wick_pct = upper_wick / bar_range

                # ── Close dans la moitie superieure ou inferieure ──
                bar_midpoint = (bar["high"] + bar["low"]) / 2
                close_in_upper_half = bar["close"] >= bar_midpoint
                close_in_lower_half = bar["close"] < bar_midpoint

                # ── VWAP pour la direction ──
                current_vwap = bar["vwap"]
                if pd.isna(current_vwap):
                    continue

                # ── ATR pour le stop ──
                atr_idx = atr_series.index.get_indexer([ts], method="pad")
                if atr_idx[0] < 1:
                    continue
                current_atr = atr_series.iloc[atr_idx[0] - 1]
                if pd.isna(current_atr) or current_atr <= 0:
                    continue

                # ── LONG : longue lower wick + close haut + prix sous VWAP (oversold) ──
                if (lower_wick_pct >= self.wick_pct_threshold
                        and close_in_upper_half
                        and bar["close"] < current_vwap):
                    stop_loss = bar["low"] - self.atr_stop_multiplier * current_atr
                    take_profit = current_vwap  # target = retour au VWAP

                    # Verifier que le R:R est positif
                    if take_profit <= bar["close"] or stop_loss >= bar["close"]:
                        continue

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vol_ratio": round(vol_ratio, 1),
                            "lower_wick_pct": round(lower_wick_pct * 100, 1),
                            "vwap": round(current_vwap, 4),
                            "adx": round(current_adx, 1),
                            "atr": round(current_atr, 4),
                        },
                    ))
                    signal_found = True

                # ── SHORT : longue upper wick + close bas + prix au-dessus VWAP ──
                elif (upper_wick_pct >= self.wick_pct_threshold
                      and close_in_lower_half
                      and bar["close"] > current_vwap):
                    stop_loss = bar["high"] + self.atr_stop_multiplier * current_atr
                    take_profit = current_vwap  # target = retour au VWAP

                    # Verifier que le R:R est positif
                    if take_profit >= bar["close"] or stop_loss <= bar["close"]:
                        continue

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vol_ratio": round(vol_ratio, 1),
                            "upper_wick_pct": round(upper_wick_pct * 100, 1),
                            "vwap": round(current_vwap, 4),
                            "adx": round(current_adx, 1),
                            "atr": round(current_atr, 4),
                        },
                    ))
                    signal_found = True

        return signals

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """ATR en pourcentage du prix moyen. None si pas assez de donnees."""
        if len(df) < period + 1:
            return None

        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_val = tr.rolling(period).mean().iloc[-1]
        avg_price = close.mean()

        if avg_price <= 0 or pd.isna(atr_val):
            return None
        return atr_val / avg_price

    @staticmethod
    def _compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Retourne la serie ATR complete (pour le calcul du stop)."""
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()
