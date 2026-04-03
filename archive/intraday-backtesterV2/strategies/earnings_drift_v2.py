"""
Strategie 3 V2 : Post-Earnings Momentum Drift (PEAD) — Large caps only

Ameliorations vs V1 :
- Prix minimum $20 (exclure small/micro caps)
- Volume minimum 1M/jour
- Exclure tous les ETFs (benchmarks, secteurs, leverages)
- Gap minimum 5% au lieu de 3% (plus selectif)
- Volume ratio minimum 5x au lieu de 3x
- Ne trader QUE les longs (les shorts sur les misses sont plus risques)
- Max 2 trades/jour

Objectif : passer de 179 trades bruites a ~20-40 de haute qualite sur mega/large caps.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# TOUS les ETFs a exclure (pas seulement leverages, TOUS)
EXCLUDED_ETFS = {
    # Benchmarks
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "VEA", "VWO", "EFA",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE", "XLB", "XLY",
    # Cross-asset
    "TLT", "GLD", "SLV", "USO", "UNG", "TIP", "LQD", "HYG", "AGG", "BND",
    # Leveraged/Inverse
    "SQQQ", "TQQQ", "SOXL", "SOXS", "UVXY", "SVXY", "SPXU", "SPXS",
    "UPRO", "TZA", "TNA", "LABU", "LABD", "NUGT", "DUST",
    "FAS", "FAZ", "ERX", "ERY", "TECL", "TECS", "CURE", "DRIP", "GUSH",
    "UCO", "SCO", "BOIL", "KOLD", "UDOW", "SDOW", "FNGU", "FNGD",
    "QLD", "QID", "SSO", "SDS", "DDM", "DXD",
    "VXX", "VIXY", "SVOL", "VIXM",
    # Crypto ETFs
    "BITO", "GBTC", "ETHE",
    # Other common ETFs
    "ARKK", "ARKG", "ARKF", "ARKW", "XBI", "IBB", "KWEB", "EEM", "FXI",
    "GDX", "GDXJ", "SLV", "PPLT", "PALL",
    # COIN, MARA, MSTR are NOT ETFs - they are stocks (crypto proxies)
}


class EarningsDriftV2Strategy(BaseStrategy):
    name = "Earnings Drift V2 (Large Caps)"

    def __init__(
        self,
        min_gap_pct: float = 4.0,          # V2d: sweet spot entre V2b(5%) et V2c(3%)
        min_vol_ratio: float = 2.0,        # V2e: encore un poil assoupli
        entry_delay_minutes: int = 30,
        stop_pct: float = 0.015,           # V2d: stop 1.5% pour laisser respirer
        target_pct: float = 0.025,         # V2d: target 2.5% plus ambitieux
        min_price: float = 12.0,           # V2d: $12 pour filtrer les plus petites
        min_daily_volume: int = 300_000,   # V2d: 300K ok
        max_trades_per_day: int = 3,
    ):
        self.min_gap_pct = min_gap_pct
        self.min_vol_ratio = min_vol_ratio
        self.entry_delay = entry_delay_minutes
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.min_price = min_price
        self.min_daily_volume = min_daily_volume
        self.max_trades_per_day = max_trades_per_day
        self._prev_day_data = {}  # ticker -> {close, avg_vol, total_vol}

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            # ── Exclure TOUS les ETFs ──
            if ticker in EXCLUDED_ETFS:
                continue

            if len(df) < 20:
                continue

            # ── Filtre prix minimum $20 ──
            today_open = df.iloc[0]["open"]
            if today_open < self.min_price:
                continue

            # ── Filtre volume minimum 1M/jour ──
            daily_vol = df["volume"].sum()
            if daily_vol < self.min_daily_volume:
                continue

            first_hour_vol = df.between_time("09:30", "10:30")["volume"].sum() if not df.between_time("09:30", "10:30").empty else 0

            # Verifier si on a les donnees de la veille
            prev = self._prev_day_data.get(ticker)
            self._prev_day_data[ticker] = {
                "close": df.iloc[-1]["close"],
                "avg_vol": df["volume"].mean(),
                "total_vol": df["volume"].sum(),
            }

            if prev is None:
                continue

            # Gap d'ouverture
            gap_pct = ((today_open - prev["close"]) / prev["close"]) * 100

            # ── V2 : LONGS SEULEMENT ──
            if gap_pct < self.min_gap_pct:
                continue

            # Volume anormal (proxy pour earnings day)
            if prev["avg_vol"] == 0 or prev["total_vol"] == 0:
                continue
            vol_ratio = first_hour_vol / (prev["total_vol"] * 0.15)  # Normaliser premiere heure

            if vol_ratio < self.min_vol_ratio:
                continue

            # C'est probablement un jour d'earnings !
            # Attendre 30 min apres l'ouverture
            entry_bars = df.between_time("10:00", "10:15")
            if entry_bars.empty:
                continue

            entry_bar = entry_bars.iloc[0]
            ts = entry_bars.index[0]

            # Verifier que le momentum continue (pas de fade)
            first_30min = df.between_time("09:30", "10:00")
            if first_30min.empty:
                continue

            first_30_move = (first_30min.iloc[-1]["close"] - first_30min.iloc[0]["open"]) / first_30min.iloc[0]["open"]

            # Le mouvement des 30 premieres minutes doit etre positif (on ne trade que les longs)
            if first_30_move < 0:
                continue  # Gap up mais fade → pas de drift

            entry = entry_bar["close"]

            # Score : gap * vol_ratio pour classement
            score = gap_pct * vol_ratio

            candidates.append({
                "signal": Signal(
                    action="LONG",
                    ticker=ticker,
                    entry_price=entry,
                    stop_loss=entry * (1 - self.stop_pct),
                    take_profit=entry * (1 + self.target_pct),
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "gap_pct": round(gap_pct, 2),
                        "vol_ratio": round(vol_ratio, 1),
                        "first_30min_move": round(first_30_move * 100, 3),
                        "price": round(entry, 2),
                        "score": round(score, 1),
                    },
                ),
                "score": score,
            })

        # ── Trier par score et prendre les max_trades_per_day meilleurs ──
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
