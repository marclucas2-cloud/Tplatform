"""
Strategie : Initial Balance Extension V2

Changements vs V1 (qui generait 0 trades) :
- ATR min : 0.8% au lieu de 1.5%
- IB range : 0.15% a 5% au lieu de 0.3% a 3%
- ADX min : 15 au lieu de 20
- SUPPRIME le filtre volume premiere heure > 50% (trop restrictif)
- Prix > $8
- Exclure ETFs leverages
- Max 3 trades/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio
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

MIN_PRICE = 8.0


class InitialBalanceExtensionV2Strategy(BaseStrategy):
    name = "Initial Balance Extension V2"

    def __init__(
        self,
        ib_extension: float = 1.0,           # V2iter2 : 1.0x au lieu de 1.5x
        vol_multiplier: float = 1.5,
        adx_threshold: float = 15.0,        # V2 : 15 au lieu de 20
        min_ib_pct: float = 0.0015,          # V2 : 0.15% au lieu de 0.3%
        max_ib_pct: float = 0.05,            # V2 : 5% au lieu de 3%
        min_atr_pct: float = 0.0005,         # V2 : 0.05% (adapte barres 5M)
        max_trades_per_day: int = 3,
    ):
        self.ib_extension = ib_extension
        self.vol_multiplier = vol_multiplier
        self.adx_threshold = adx_threshold
        self.min_ib_pct = min_ib_pct
        self.max_ib_pct = max_ib_pct
        self.min_atr_pct = min_atr_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── Regime filter : SPY direction ──
        spy_bullish = None
        if "SPY" in data:
            spy_df = data["SPY"]
            spy_ib = spy_df.between_time("09:30", "09:59")
            if len(spy_ib) >= 3:
                spy_open = spy_ib.iloc[0]["open"]
                spy_ib_close = spy_ib.iloc[-1]["close"]
                spy_bullish = spy_ib_close > spy_open

        for ticker, df in data.items():
            if ticker in EXCLUDE or ticker == config.BENCHMARK:
                continue

            if len(df) < 40:
                continue

            # ── Filtre prix minimum ──
            avg_price = df["close"].mean()
            if avg_price < MIN_PRICE:
                continue

            # ── Filtre ATR : 0.8% minimum (assoupli) ──
            atr_pct = self._compute_atr_pct(df)
            if atr_pct is None or atr_pct < self.min_atr_pct:
                continue

            # ── Calculer l'Initial Balance (9:30-10:00 ET) ──
            ib_bars = df.between_time("09:30", "09:59")
            if len(ib_bars) < 5:
                continue

            ib_high = ib_bars["high"].max()
            ib_low = ib_bars["low"].min()
            ib_range = ib_high - ib_low

            if ib_range <= 0:
                continue

            mid_price = (ib_high + ib_low) / 2

            # ── Filtre : IB range entre 0.15% et 5% du prix (elargi) ──
            ib_pct = ib_range / mid_price
            if ib_pct < self.min_ib_pct or ib_pct > self.max_ib_pct:
                continue

            # V2 : PAS de filtre volume premiere heure (supprime)

            # ── Calculer ADX pour le filtre directionnel ──
            df_copy = df.copy()
            adx_series = adx(df_copy, period=14)

            # ── Scanner les barres apres l'IB (10:00-14:00 ET) ──
            post_ib = df.between_time("10:00", "14:00")
            signal_found = False

            for ts, bar in post_ib.iterrows():
                if signal_found:
                    break

                if len(signals) >= self.max_trades_per_day:
                    break

                # ── ADX au moment de l'evaluation (shift 1 pour eviter lookahead) ──
                adx_idx = adx_series.index.get_indexer([ts], method="pad")
                if adx_idx[0] < 1:
                    continue
                current_adx = adx_series.iloc[adx_idx[0] - 1]
                if pd.isna(current_adx) or current_adx < self.adx_threshold:
                    continue

                # ── Volume ratio (barre courante vs moyenne 20 barres precedentes) ──
                bars_before = df.loc[:ts]
                if len(bars_before) < 21:
                    continue
                avg_vol_20 = bars_before["volume"].iloc[-21:-1].mean()
                if avg_vol_20 <= 0:
                    continue
                current_vol_ratio = bar["volume"] / avg_vol_20

                if current_vol_ratio < self.vol_multiplier:
                    continue

                # ── LONG : close > IB_high + SPY bullish ou neutre ──
                if bar["close"] > ib_high and spy_bullish is not False:
                    stop_loss = bar["close"] * 0.990   # stop 1.0%
                    take_profit = bar["close"] * 1.012  # target 1.2% (R:R 1.2)

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "ib_high": round(ib_high, 4),
                            "ib_low": round(ib_low, 4),
                            "ib_range_pct": round(ib_pct * 100, 2),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(current_vol_ratio, 1),
                        },
                    ))
                    signal_found = True

                # ── SHORT : close < IB_low + SPY bearish ou neutre ──
                elif bar["close"] < ib_low and spy_bullish is not True:
                    stop_loss = bar["close"] * 1.010   # stop 1.0%
                    take_profit = bar["close"] * 0.988  # target 1.2% (R:R 1.2)

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "ib_high": round(ib_high, 4),
                            "ib_low": round(ib_low, 4),
                            "ib_range_pct": round(ib_pct * 100, 2),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(current_vol_ratio, 1),
                        },
                    ))
                    signal_found = True

        return signals

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """ATR en pourcentage du prix moyen."""
        if len(df) < period + 1:
            return None

        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = tr.rolling(period).mean().iloc[-1]
        avg_price = close.mean()

        if avg_price <= 0 or pd.isna(atr):
            return None

        return atr / avg_price
