"""
Stratégie 6 : Mean Reversion (Bollinger Bands + RSI)
Le prix revient à la moyenne quand il s'en écarte trop.

Règles :
- LONG quand prix touche bande basse ET RSI(7) < 25
- SHORT quand prix touche bande haute ET RSI(7) > 75
- Exit : retour à la SMA20 (middle band)
- Stop-loss : 1% au-delà de la bande
- Filtre : ADX(14) < 30 (pas de trend fort)
"""
import pandas as pd
from backtest_engine import BaseStrategy, Signal
from utils.indicators import bollinger_bands, rsi, adx
import config


class MeanReversionStrategy(BaseStrategy):
    name = "Mean Reversion BB+RSI"

    def __init__(self, bb_period: int = 20, bb_std: float = 2.5,
                 rsi_period: int = 7, rsi_long: float = 25, rsi_short: float = 75,
                 adx_max: float = 30, stop_pct: float = 0.01):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_long = rsi_long
        self.rsi_short = rsi_short
        self.adx_max = adx_max
        self.stop_pct = stop_pct

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK or len(df) < 30:
                continue

            df = df.copy()

            # Indicateurs
            upper, middle, lower = bollinger_bands(df["close"], self.bb_period, self.bb_std)
            df["bb_upper"] = upper
            df["bb_middle"] = middle
            df["bb_lower"] = lower
            df["rsi"] = rsi(df["close"], self.rsi_period)
            df["adx"] = adx(df, 14)

            # Scanner après warmup
            tradeable = df.between_time("10:00", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if pd.isna(bar["bb_upper"]) or pd.isna(bar["rsi"]) or pd.isna(bar["adx"]):
                    continue

                # Filtre : pas de trend fort
                if bar["adx"] > self.adx_max:
                    continue

                # LONG : prix touche bande basse + RSI survendu
                if bar["close"] <= bar["bb_lower"] and bar["rsi"] < self.rsi_long:
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 - self.stop_pct),
                        take_profit=bar["bb_middle"],  # Target = middle band
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "rsi": round(bar["rsi"], 1),
                            "adx": round(bar["adx"], 1),
                        },
                    ))
                    signal_found = True

                # SHORT : prix touche bande haute + RSI suracheté
                elif bar["close"] >= bar["bb_upper"] and bar["rsi"] > self.rsi_short:
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 + self.stop_pct),
                        take_profit=bar["bb_middle"],
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "rsi": round(bar["rsi"], 1),
                            "adx": round(bar["adx"], 1),
                        },
                    ))
                    signal_found = True

        return signals
