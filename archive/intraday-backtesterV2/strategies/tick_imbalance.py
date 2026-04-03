"""
Stratégie 9 : Tick Imbalance / Order Flow
Détecte les déséquilibres achat/vente comme signal directionnel.

Concept (Marcos López de Prado - "Advances in Financial ML") :
- Les barres classiques (temps) perdent de l'information
- Le "tick rule" classifie chaque trade comme buy ou sell
- Un déséquilibre extrême prédit la direction à court terme

Proxy sans données L2 (adapté aux données OHLCV) :
- Si close > open → barre "acheteuse" 
- Volume-weighted buy pressure = Σ(volume * (close-low)/(high-low))
- Volume-weighted sell pressure = Σ(volume * (high-close)/(high-low))
- Ratio buy/total > 0.65 pendant 5 barres → signal LONG
- Ratio buy/total < 0.35 pendant 5 barres → signal SHORT

Extension : "Volume Clock" — regrouper par volume fixe plutôt que par temps
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


class TickImbalanceStrategy(BaseStrategy):
    name = "Tick Imbalance"

    def __init__(self, imbalance_threshold: float = 0.65, consecutive_bars: int = 5,
                 stop_pct: float = 0.003, target_pct: float = 0.006):
        self.threshold = imbalance_threshold
        self.consecutive = consecutive_bars
        self.stop_pct = stop_pct
        self.target_pct = target_pct

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker in [config.BENCHMARK, "QQQ"] or len(df) < 30:
                continue

            df = df.copy()

            # ── Buy/Sell pressure proxy ──
            bar_range = df["high"] - df["low"]
            bar_range = bar_range.replace(0, np.nan)

            # Proportion du mouvement qui est "achat" vs "vente"
            df["buy_pressure"] = df["volume"] * (df["close"] - df["low"]) / bar_range
            df["sell_pressure"] = df["volume"] * (df["high"] - df["close"]) / bar_range
            df["total_pressure"] = df["buy_pressure"] + df["sell_pressure"]
            df["buy_ratio"] = df["buy_pressure"] / df["total_pressure"].replace(0, np.nan)

            # ── Imbalance rolling ──
            df["buy_ratio_avg"] = df["buy_ratio"].rolling(self.consecutive).mean()

            # ── Volume anomaly detection ──
            df["vol_sma"] = df["volume"].rolling(20).mean()
            df["vol_spike"] = df["volume"] > df["vol_sma"] * 1.5

            # Scanner après warmup
            tradeable = df.between_time("09:45", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if pd.isna(bar["buy_ratio_avg"]):
                    continue

                # Besoin d'un volume spike pour confirmer
                if not bar["vol_spike"]:
                    continue

                # LONG : pression acheteuse dominante
                if bar["buy_ratio_avg"] > self.threshold:
                    # Vérifier que le VWAP confirme (prix au-dessus)
                    vwap_val = bar.get("vwap", bar["close"])
                    if bar["close"] >= vwap_val * 0.999:  # Tolérance
                        signals.append(Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 - self.stop_pct),
                            take_profit=bar["close"] * (1 + self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "buy_ratio": round(bar["buy_ratio_avg"], 3),
                                "vol_spike": True,
                            },
                        ))
                        signal_found = True

                # SHORT : pression vendeuse dominante
                elif bar["buy_ratio_avg"] < (1 - self.threshold):
                    vwap_val = bar.get("vwap", bar["close"])
                    if bar["close"] <= vwap_val * 1.001:
                        signals.append(Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 + self.stop_pct),
                            take_profit=bar["close"] * (1 - self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "buy_ratio": round(bar["buy_ratio_avg"], 3),
                                "vol_spike": True,
                            },
                        ))
                        signal_found = True

        return signals
