"""
Strategie : Volume Climax Reversal V2

Changements vs V1 (0 trades sur 186 tickers, -25% sur 207) :
- Volume spike : 2.5x au lieu de 3.0x
- Wick threshold : 50% au lieu de 60%
- ADX max : 35 au lieu de 40
- Prix > $15 (exclure micro caps / ETFs leverages meches geantes)
- Exclure ETFs leverages/inverses
- Stop : 1.0% fixe au lieu de 1.5x ATR (stop ATR trop large)
- Target : 1.5% fixe (pas VWAP — trop ambitieux)
- Max day move : 4% au lieu de 5%
- Max 2 trades/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx
import config


# ── Tickers a exclure ──
EXCLUDE = {
    # ETFs leverages / inverses
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "RWM", "PSQ", "SH", "SDS",
    # Benchmarks
    "SPY", "QQQ", "IWM", "DIA",
}

MIN_PRICE = 15.0


class VolumeClimaxReversalV2Strategy(BaseStrategy):
    name = "Volume Climax Reversal V2"

    def __init__(
        self,
        vol_spike_threshold: float = 2.5,     # V2 : 2.5x au lieu de 3.0x
        wick_pct_threshold: float = 0.50,      # V2 : 50% au lieu de 60%
        stop_pct: float = 0.008,               # V2best : 0.8% stop
        target_pct: float = 0.010,             # V2best : 1.0% target (R:R 1.25)
        max_adx: float = 35.0,                 # V2 : 35 au lieu de 40
        max_move_from_open_pct: float = 0.04,  # V2 : 4% au lieu de 5%
        min_atr_pct: float = 0.0005,          # V2 : adapte barres 5M
        vol_lookback: int = 20,
        atr_period: int = 14,
        max_trades_per_day: int = 2,
    ):
        self.vol_spike_threshold = vol_spike_threshold
        self.wick_pct_threshold = wick_pct_threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_adx = max_adx
        self.max_move_from_open_pct = max_move_from_open_pct
        self.min_atr_pct = min_atr_pct
        self.vol_lookback = vol_lookback
        self.atr_period = atr_period
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker in EXCLUDE or ticker == config.BENCHMARK:
                continue

            if len(df) < 40:
                continue

            # ── Filtre prix minimum $15 ──
            avg_price = df["close"].mean()
            if avg_price < MIN_PRICE:
                continue

            # ── Filtre ATR : on veut des tickers avec ATR > 1% ──
            atr_pct = self._compute_atr_pct(df, self.atr_period)
            if atr_pct is None or atr_pct < self.min_atr_pct:
                continue

            # ── Calculer ADX pour le filtre ──
            df_copy = df.copy()
            adx_series = adx(df_copy, period=self.atr_period)

            # ── Prix d'ouverture du jour ──
            day_open = df_copy.iloc[0]["open"]

            # ── Scanner les barres (10:00-15:25 ET) ──
            tradeable = df_copy.between_time("10:00", "15:25")

            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if len(signals) >= self.max_trades_per_day:
                    break

                # ── Filtre : move depuis l'open < 4% ──
                move_from_open = abs(bar["close"] - day_open) / day_open
                if move_from_open > self.max_move_from_open_pct:
                    continue

                # ── Filtre ADX < 35 (pas de trend extreme) ──
                adx_idx = adx_series.index.get_indexer([ts], method="pad")
                if adx_idx[0] < 1:
                    continue
                current_adx = adx_series.iloc[adx_idx[0] - 1]
                if pd.isna(current_adx) or current_adx > self.max_adx:
                    continue

                # ── Volume spike : volume > 2.5x la moyenne des 20 barres precedentes ──
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

                # ── LONG : longue lower wick + close haut (reversal haussier) ──
                if (lower_wick_pct >= self.wick_pct_threshold
                        and close_in_upper_half):

                    entry = bar["close"]
                    stop_loss = entry * (1 - self.stop_pct)     # 1.0% fixe
                    take_profit = entry * (1 + self.target_pct)  # 1.5% fixe

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vol_ratio": round(vol_ratio, 1),
                            "lower_wick_pct": round(lower_wick_pct * 100, 1),
                            "adx": round(current_adx, 1),
                        },
                    ))
                    signal_found = True

                # ── SHORT : longue upper wick + close bas (reversal baissier) ──
                elif (upper_wick_pct >= self.wick_pct_threshold
                      and close_in_lower_half):

                    entry = bar["close"]
                    stop_loss = entry * (1 + self.stop_pct)     # 1.0% fixe
                    take_profit = entry * (1 - self.target_pct)  # 1.5% fixe

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vol_ratio": round(vol_ratio, 1),
                            "upper_wick_pct": round(upper_wick_pct * 100, 1),
                            "adx": round(current_adx, 1),
                        },
                    ))
                    signal_found = True

        return signals

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """ATR en pourcentage du prix moyen."""
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
