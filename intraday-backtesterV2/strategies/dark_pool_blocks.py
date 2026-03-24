"""
Stratégie 10 : Dark Pool Block Detection
Les gros blocs institutionnels révèlent la direction "smart money".

Hypothèse :
- Les trades > 5x le volume moyen par barre signalent de l'activité institutionnelle
- Si un gros bloc arrive SANS mouvement de prix → accumulation silencieuse
- Si un gros bloc arrive AVEC mouvement → momentum institutionnel

Proxy sans données dark pool réelles :
- Détecter les "volume anomalies" : barres avec volume > 5x moyenne
- Classifier : absorption (volume élevé, petit range) vs impulsion (volume + range)
- Absorption → anticiper le breakout dans la direction de la clôture
- Impulsion → suivre le momentum
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


class DarkPoolBlockStrategy(BaseStrategy):
    name = "Dark Pool Blocks"

    def __init__(self, vol_multiplier: float = 5.0, absorption_range_pct: float = 0.001,
                 stop_pct: float = 0.004, target_pct: float = 0.008):
        self.vol_multiplier = vol_multiplier
        self.absorption_range = absorption_range_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker in [config.BENCHMARK, "QQQ"] or len(df) < 30:
                continue

            df = df.copy()

            # Volume moyen rolling
            df["vol_avg"] = df["volume"].rolling(50, min_periods=20).mean()
            df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

            # Range de la barre en %
            df["bar_range_pct"] = (df["high"] - df["low"]) / df["close"]

            # Classifier les anomalies
            tradeable = df.between_time("09:45", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.vol_multiplier:
                    continue

                is_absorption = bar["bar_range_pct"] < self.absorption_range
                is_bullish_bar = bar["close"] > bar["open"]

                if is_absorption:
                    # Volume massif + petit range = accumulation silencieuse
                    # Direction = direction de la clôture de la barre
                    if is_bullish_bar:
                        signals.append(Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["low"] * 0.999,
                            take_profit=bar["close"] * (1 + self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "block_type": "absorption",
                                "vol_ratio": round(bar["vol_ratio"], 1),
                                "bar_range_pct": round(bar["bar_range_pct"] * 100, 3),
                            },
                        ))
                    else:
                        signals.append(Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["high"] * 1.001,
                            take_profit=bar["close"] * (1 - self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "block_type": "absorption",
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ))
                    signal_found = True

                else:
                    # Volume massif + grand range = impulsion → suivre le momentum
                    if is_bullish_bar and bar["bar_range_pct"] > 0.003:
                        signals.append(Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 - self.stop_pct),
                            take_profit=bar["close"] * (1 + self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "block_type": "impulse",
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ))
                        signal_found = True
                    elif not is_bullish_bar and bar["bar_range_pct"] > 0.003:
                        signals.append(Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 + self.stop_pct),
                            take_profit=bar["close"] * (1 - self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "block_type": "impulse",
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ))
                        signal_found = True

        return signals
