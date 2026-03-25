"""
Strategie 2 V2 : Mean Reversion BB+RSI — Filtres stricts

Ameliorations vs V1 :
- RSI seuils resserres : 15/85 au lieu de 25/75 (signaux extremes seulement)
- Filtre volume > 1.5x moyenne (confirmation institutionnelle)
- Filtre : premier touch des bandes BB uniquement (pas les rebounds multiples)
- Prix > $10
- Exclure ETFs leverages
- Max 3 trades/jour (meilleurs scores RSI extremite)
- Target = retour a BB middle au lieu du prix moyen

Objectif : passer de 615 trades a ~80-120.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import bollinger_bands, rsi, adx
import config


# ETFs leverages/inverses a exclure
LEVERAGED_ETFS = {
    "SQQQ", "TQQQ", "SOXL", "SOXS", "UVXY", "SVXY", "SPXU", "SPXS",
    "UPRO", "TZA", "TNA", "LABU", "LABD", "NUGT", "DUST", "JNUG", "JDST",
    "FAS", "FAZ", "ERX", "ERY", "TECL", "TECS", "CURE", "DRIP", "GUSH",
    "UCO", "SCO", "BOIL", "KOLD", "UDOW", "SDOW", "FNGU", "FNGD",
    "BULZ", "BERZ", "WEBL", "WEBS", "YINN", "YANG", "QLD", "QID",
    "SSO", "SDS", "DDM", "DXD", "MVV", "MZZ", "UWM", "TWM",
    "NAIL", "DRV", "DPST", "BNKU",
    "VXX", "VIXY", "SVOL", "VIXM",
}


class MeanReversionV2Strategy(BaseStrategy):
    name = "Mean Reversion V2 BB+RSI"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 3.0,          # V2b: bandes plus larges = signaux plus extremes
        rsi_period: int = 7,
        rsi_long: float = 12,          # V2b: RSI encore plus extreme
        rsi_short: float = 88,         # V2b: RSI encore plus extreme
        adx_max: float = 25,           # V2b: exiger range plus plat
        stop_pct: float = 0.015,       # V2b: stop 1.5% pour laisser respirer
        min_price: float = 10.0,
        vol_multiplier: float = 2.0,   # V2b: volume 2x requis
        max_trades_per_day: int = 2,   # V2b: 2 trades max par jour
        min_bb_width_pct: float = 0.02,  # V2b: BB spread min 2% du prix
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_long = rsi_long
        self.rsi_short = rsi_short
        self.adx_max = adx_max
        self.stop_pct = stop_pct
        self.min_price = min_price
        self.vol_multiplier = vol_multiplier
        self.max_trades_per_day = max_trades_per_day
        self.min_bb_width_pct = min_bb_width_pct

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK or len(df) < 30:
                continue

            # ── Filtre : exclure ETFs leverages ──
            if ticker in LEVERAGED_ETFS:
                continue

            # ── Filtre prix minimum ──
            if df.iloc[0]["open"] < self.min_price:
                continue

            df = df.copy()

            # Indicateurs
            upper, middle, lower = bollinger_bands(df["close"], self.bb_period, self.bb_std)
            df["bb_upper"] = upper
            df["bb_middle"] = middle
            df["bb_lower"] = lower
            df["rsi"] = rsi(df["close"], self.rsi_period)
            df["adx"] = adx(df, 14)

            # Volume ratio (rolling 20 barres)
            df["vol_avg"] = df["volume"].rolling(20, min_periods=5).mean()
            df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

            # ── Tracker premier touch des BB ──
            # On ne veut que la PREMIERE fois que le prix touche la bande
            touched_lower = False
            touched_upper = False

            # Scanner apres warmup (10:00 pour laisser les indicateurs chauffer)
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

                # Filtre volume : confirmation institutionnelle
                if pd.isna(bar.get("vol_ratio")) or bar["vol_ratio"] < self.vol_multiplier:
                    continue

                # V2b: Filtre BB width — bandes doivent etre assez larges
                bb_width = (bar["bb_upper"] - bar["bb_lower"]) / bar["bb_middle"] if bar["bb_middle"] > 0 else 0
                if bb_width < self.min_bb_width_pct:
                    continue

                # LONG : premier touch de la bande basse + RSI extreme
                if bar["close"] <= bar["bb_lower"] and bar["rsi"] < self.rsi_long:
                    if not touched_lower:
                        touched_lower = True
                        # Score = distance au seuil RSI (plus extreme = meilleur)
                        score = self.rsi_long - bar["rsi"]
                        candidates.append({
                            "signal": Signal(
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
                                    "vol_ratio": round(bar["vol_ratio"], 1),
                                },
                            ),
                            "score": score,
                        })
                        signal_found = True

                # SHORT : premier touch de la bande haute + RSI extreme
                elif bar["close"] >= bar["bb_upper"] and bar["rsi"] > self.rsi_short:
                    if not touched_upper:
                        touched_upper = True
                        score = bar["rsi"] - self.rsi_short
                        candidates.append({
                            "signal": Signal(
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
                                    "vol_ratio": round(bar["vol_ratio"], 1),
                                },
                            ),
                            "score": score,
                        })
                        signal_found = True

                # Tracker les touches (meme sans signal)
                if bar["close"] <= bar["bb_lower"]:
                    touched_lower = True
                if bar["close"] >= bar["bb_upper"]:
                    touched_upper = True

        # ── Trier par score (RSI le plus extreme) et prendre les meilleurs ──
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
