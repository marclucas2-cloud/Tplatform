"""
Strategie 1 V2 : Opening Range Breakout (ORB) 5-Min — Filtre "Stock in Play" STRICT

Ameliorations vs V1 :
- Gap d'ouverture > 3% obligatoire (vs aucun filtre dans V1)
- Volume premiere barre > 3x moyenne 20j (vs 1.5x)
- Prix > $10 (exclure penny stocks)
- Exclure les ETFs leverages/inverses (SQQQ, TQQQ, SOXL, UVXY, etc.)
- Max 3 trades/jour (les 3 meilleurs scores gap*volume)
- R:R minimum 1.5 (pas de trades ou le range < 0.3%)
- Tout le reste identique a V1

Objectif : passer de 615 trades a ~50-100 de haute qualite.
"""
import pandas as pd
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import orb_range, volume_ratio
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
    # Volatility products
    "VXX", "VIXY", "SVOL", "VIXM",
}


class ORB5MinV2Strategy(BaseStrategy):
    name = "ORB 5-Min V2 (Stock in Play)"

    def __init__(
        self,
        rr_ratio: float = 2.0,
        min_gap_pct: float = 3.0,
        vol_multiplier: float = 3.0,
        min_price: float = 10.0,
        max_trades_per_day: int = 3,
        min_range_pct: float = 0.3,
    ):
        self.rr_ratio = rr_ratio
        self.min_gap_pct = min_gap_pct
        self.vol_multiplier = vol_multiplier
        self.min_price = min_price
        self.max_trades_per_day = max_trades_per_day
        self.min_range_pct = min_range_pct / 100  # convert to decimal
        self._prev_day_data = {}  # ticker -> {close, avg_vol}

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            # ── Filtre : exclure ETFs leverages/inverses ──
            if ticker in LEVERAGED_ETFS:
                continue

            # ── Filtre prix minimum ──
            if df.iloc[0]["open"] < self.min_price:
                continue

            # ── Calculer le range des 5 premieres minutes ──
            orb_bars = df.between_time("09:30", "09:34")
            if len(orb_bars) < 1:
                continue

            orb_high = orb_bars["high"].max()
            orb_low = orb_bars["low"].min()
            orb_vol = orb_bars["volume"].sum()
            orb_range_size = orb_high - orb_low
            mid_price = (orb_high + orb_low) / 2

            if orb_range_size <= 0 or mid_price <= 0:
                continue

            # ── Filtre R:R minimum : range doit etre > min_range_pct du prix ──
            range_pct = orb_range_size / mid_price
            if range_pct < self.min_range_pct:
                continue

            # ── Filtre gap d'ouverture > 3% ──
            today_open = df.iloc[0]["open"]
            prev = self._prev_day_data.get(ticker)
            # Stocker les donnees du jour pour le prochain
            self._prev_day_data[ticker] = {
                "close": df.iloc[-1]["close"],
                "avg_vol": df["volume"].mean(),
            }
            if prev is None:
                continue

            gap_pct = abs((today_open - prev["close"]) / prev["close"]) * 100
            if gap_pct < self.min_gap_pct:
                continue

            gap_direction = "up" if today_open > prev["close"] else "down"

            # ── Filtre volume premiere barre > 3x moyenne ──
            if prev["avg_vol"] > 0:
                vol_ratio = orb_vol / (prev["avg_vol"] * len(orb_bars)) if len(orb_bars) > 0 else 0
            else:
                vol_ratio = 0

            if vol_ratio < self.vol_multiplier:
                continue

            # ── Score pour classement (gap * vol_ratio) ──
            score = gap_pct * vol_ratio

            # ── Scanner les barres apres le range pour breakout ──
            post_orb = df.between_time("09:35", "15:55")

            for ts, bar in post_orb.iterrows():
                # LONG breakout
                if bar["close"] > orb_high and bar["volume"] > 0:
                    risk = orb_high - orb_low
                    candidates.append({
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=orb_high,
                            stop_loss=orb_low,
                            take_profit=orb_high + risk * self.rr_ratio,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "orb_range": round(orb_range_size, 2),
                                "gap_pct": round(gap_pct, 2),
                                "gap_direction": gap_direction,
                                "vol_ratio": round(vol_ratio, 1),
                                "score": round(score, 1),
                            },
                        ),
                        "score": score,
                    })
                    break  # Un seul signal par ticker par jour

                # SHORT breakdown
                if bar["close"] < orb_low and bar["volume"] > 0:
                    risk = orb_high - orb_low
                    candidates.append({
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=orb_low,
                            stop_loss=orb_high,
                            take_profit=orb_low - risk * self.rr_ratio,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "orb_range": round(orb_range_size, 2),
                                "gap_pct": round(gap_pct, 2),
                                "gap_direction": gap_direction,
                                "vol_ratio": round(vol_ratio, 1),
                                "score": round(score, 1),
                            },
                        ),
                        "score": score,
                    })
                    break

        # ── Trier par score et prendre les max_trades_per_day meilleurs ──
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
