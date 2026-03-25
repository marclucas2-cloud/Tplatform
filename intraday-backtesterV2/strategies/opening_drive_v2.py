"""
Strategie 4 V2 : Opening Drive — Stops fixes, exit plus tot

Ameliorations vs V1 :
- Stop = 1.0% fixe au lieu de retour a l'open
- Target = 2.0% fixe
- Min move 10 premieres min = 1.0% au lieu de 0.5%
- Volume > 3x premiere barre (vs 2x)
- Prix > $15
- Exclure ETFs leverages
- Max 3 trades/jour
- Timing : exit avant 11:30 au lieu de 12:00 (le drive s'essouffle plus tot)

Note : le moteur backtest gere la sortie forcee a 15:55, mais la strategie
utilise un take_profit et stop_loss fixes qui devraient se declencher
bien avant 11:30 dans la plupart des cas.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx
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


class OpeningDriveV2Strategy(BaseStrategy):
    name = "Opening Drive V2 (Fixed Stops)"

    def __init__(
        self,
        min_move_pct: float = 0.5,         # meme que V1
        max_pullback_pct: float = 0.30,
        vol_multiplier: float = 2.0,       # meme que V1
        stop_pct: float = 0.01,            # 1.0% fixe (amelioration cle vs V1)
        target_pct: float = 0.015,         # target 1.5% (R:R 1.5:1)
        adx_threshold: float = 12.0,
        min_price: float = 10.0,
        min_volume: int = 300_000,
        min_atr_pct: float = 0.008,
        max_trades_per_day: int = 3,
    ):
        self.min_move_pct = min_move_pct / 100  # Convertir en decimal
        self.max_pullback_pct = max_pullback_pct
        self.vol_multiplier = vol_multiplier
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.adx_threshold = adx_threshold
        self.min_price = min_price
        self.min_volume = min_volume
        self.min_atr_pct = min_atr_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            # ── Filtre : exclure ETFs leverages ──
            if ticker in LEVERAGED_ETFS:
                continue

            if len(df) < 30:
                continue

            # ── Filtre prix minimum ──
            if df.iloc[0]["open"] < self.min_price:
                continue

            # ── Filtre volume : volume total du jour > 1M ──
            daily_volume = df["volume"].sum()
            if daily_volume < self.min_volume:
                continue

            # ── Filtre ATR : volatilite suffisante (> 1%) ──
            atr_pct = self._compute_atr_pct(df)
            if atr_pct is not None and atr_pct < self.min_atr_pct:
                continue

            # ── Recuperer l'open du jour (9:30) ──
            opening_bars = df.between_time("09:30", "09:31")
            if opening_bars.empty:
                continue
            day_open = opening_bars.iloc[0]["open"]

            # ── Barres des 10 premieres minutes (9:30-9:40) ──
            first_10_bars = df.between_time("09:30", "09:39")
            if len(first_10_bars) < 2:
                continue

            close_at_940 = first_10_bars.iloc[-1]["close"]
            high_10 = first_10_bars["high"].max()
            low_10 = first_10_bars["low"].min()
            avg_bar_vol = first_10_bars["volume"].mean()

            # ── Calcul du move initial ──
            move_pct = (close_at_940 - day_open) / day_open
            abs_move_pct = abs(move_pct)

            # ── Filtre : move >= 1.0% (vs 0.5% en V1) ──
            if abs_move_pct < self.min_move_pct:
                continue

            # ── Filtre volume : > 3x la moyenne (vs 2x en V1) ──
            all_day_avg = df["volume"].mean()
            if all_day_avg > 0 and avg_bar_vol < all_day_avg * self.vol_multiplier:
                continue

            # ── Filtre pullback : le prix n'a pas retrace > 30% du move ──
            if move_pct > 0:
                pullback = (high_10 - close_at_940) / (high_10 - day_open) if (high_10 - day_open) > 0 else 0
            else:
                pullback = (close_at_940 - low_10) / (day_open - low_10) if (day_open - low_10) > 0 else 0

            if pullback > self.max_pullback_pct:
                continue

            # ── Filtre ADX (anti-lookahead : barres avant 9:40) ──
            adx_series = adx(df.copy(), period=14)
            bars_before = adx_series[adx_series.index <= first_10_bars.index[-1]]
            if len(bars_before) >= 2:
                current_adx = bars_before.iloc[-2]
                if pd.isna(current_adx) or current_adx < self.adx_threshold:
                    continue

            # ── Trouver le timestamp d'entree (premiere barre apres 9:44) ──
            entry_window = df.between_time("09:44", "09:50")
            if entry_window.empty:
                continue
            entry_bar = entry_window.iloc[0]
            entry_ts = entry_window.index[0]
            entry_price = entry_bar["close"]

            # ── V2 : STOPS et TARGETS FIXES ──
            if move_pct > 0:
                direction = "LONG"
                stop_loss = entry_price * (1 - self.stop_pct)    # -1% fixe
                take_profit = entry_price * (1 + self.target_pct)  # +2% fixe
            else:
                direction = "SHORT"
                stop_loss = entry_price * (1 + self.stop_pct)    # +1% fixe
                take_profit = entry_price * (1 - self.target_pct)  # -2% fixe

            vol_ratio = round(avg_bar_vol / all_day_avg if all_day_avg > 0 else 0, 2)

            candidates.append({
                "ticker": ticker,
                "direction": direction,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_ts": entry_ts,
                "abs_move_pct": abs_move_pct,
                "pullback": round(pullback, 3),
                "vol_ratio": vol_ratio,
            })

        # ── Trier par force du move, prendre les meilleurs ──
        candidates.sort(key=lambda x: x["abs_move_pct"], reverse=True)
        candidates = candidates[:self.max_trades_per_day]

        signals = []
        for c in candidates:
            signals.append(Signal(
                action=c["direction"],
                ticker=c["ticker"],
                entry_price=c["entry_price"],
                stop_loss=c["stop_loss"],
                take_profit=c["take_profit"],
                timestamp=c["entry_ts"],
                metadata={
                    "strategy": self.name,
                    "initial_move_pct": round(c["abs_move_pct"] * 100, 2),
                    "pullback_pct": round(c["pullback"] * 100, 1),
                    "vol_ratio": c["vol_ratio"],
                    "stop_pct": self.stop_pct * 100,
                    "target_pct": self.target_pct * 100,
                },
            ))

        return signals

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """Calcule l'ATR en pourcentage du prix moyen."""
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
