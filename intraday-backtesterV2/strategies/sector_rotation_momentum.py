"""
Strategie : Sector Rotation Momentum

Edge structurel :
Les flux TWAP institutionnels creent un momentum sectoriel intraday qui
persiste pendant plusieurs heures. Si un secteur surperforme significativement
le marche (SPY) en premiere heure, les flux continuent l'apres-midi car les
gros ordres TWAP ne sont pas termines.

On prend une paire long/short : long le leader sectoriel, short le laggard.

Regles :
- Tickers : Sector ETFs (XLK, XLF, XLE, XLV, XLI, XLP, XLU, XLC, XLRE)
  + benchmark SPY
- Calcul du momentum premiere heure (9:30-10:30 ET) relatif a SPY
- LONG : meilleur sector ETF vs SPY (> +0.3%)
- SHORT : pire sector ETF vs SPY (< -0.3%)
- Une seule paire long/short par jour
- Stop : 1.5x ATR(14) en 5M
- Target : 2x le risque (R:R 1:2)
- Filtres : dispersion sectorielle > 0.3%, SPY ADX > 15
- Frequence : 0-1 paire/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx
import config


# Sector ETFs de l'univers
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE"]


class SectorRotationMomentumStrategy(BaseStrategy):
    name = "Sector Rotation Momentum"

    def __init__(
        self,
        min_relative_perf: float = 0.003,
        atr_stop_multiplier: float = 1.5,
        rr_ratio: float = 2.0,
        min_spy_adx: float = 15.0,
        atr_period: int = 14,
    ):
        self.min_relative_perf = min_relative_perf  # 0.3%
        self.atr_stop_multiplier = atr_stop_multiplier
        self.rr_ratio = rr_ratio
        self.min_spy_adx = min_spy_adx
        self.atr_period = atr_period

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── SPY est obligatoire comme benchmark ──
        if "SPY" not in data:
            return signals

        spy_df = data["SPY"]
        if len(spy_df) < 30:
            return signals

        # ── Filtre : SPY ADX > 15 (marche directionnel) ──
        spy_adx_series = adx(spy_df.copy(), period=self.atr_period)
        # Prendre l'ADX vers 10:30 (fin de la premiere heure)
        spy_first_hour_end = spy_df.between_time("10:25", "10:30")
        if spy_first_hour_end.empty:
            return signals

        # Trouver l'ADX le plus recent avant 10:30 (anti-lookahead)
        ref_ts = spy_first_hour_end.index[-1]
        adx_idx = spy_adx_series.index.get_indexer([ref_ts], method="pad")
        if adx_idx[0] < 1:
            return signals
        spy_adx_val = spy_adx_series.iloc[adx_idx[0] - 1]
        if pd.isna(spy_adx_val) or spy_adx_val < self.min_spy_adx:
            return signals

        # ── Calculer le momentum premiere heure de SPY (9:30-10:30) ──
        spy_morning = spy_df.between_time("09:30", "10:30")
        if len(spy_morning) < 5:
            return signals

        spy_open = spy_morning.iloc[0]["open"]
        spy_close_1h = spy_morning.iloc[-1]["close"]
        if spy_open <= 0:
            return signals
        spy_return = (spy_close_1h - spy_open) / spy_open

        # ── Calculer le momentum relatif de chaque sector ETF ──
        sector_perfs = {}

        for etf in SECTOR_ETFS:
            if etf not in data:
                continue

            etf_df = data[etf]
            etf_morning = etf_df.between_time("09:30", "10:30")
            if len(etf_morning) < 5:
                continue

            etf_open = etf_morning.iloc[0]["open"]
            etf_close_1h = etf_morning.iloc[-1]["close"]
            if etf_open <= 0:
                continue

            etf_return = (etf_close_1h - etf_open) / etf_open
            relative_perf = etf_return - spy_return
            sector_perfs[etf] = relative_perf

        if len(sector_perfs) < 3:
            return signals

        # ── Identifier le best et le worst sector ──
        best_etf = max(sector_perfs, key=sector_perfs.get)
        worst_etf = min(sector_perfs, key=sector_perfs.get)
        best_perf = sector_perfs[best_etf]
        worst_perf = sector_perfs[worst_etf]

        # ── Filtre : dispersion suffisante (> 0.3% de chaque cote) ──
        if best_perf < self.min_relative_perf or worst_perf > -self.min_relative_perf:
            return signals

        # ── Fenetre d'entree : 10:30-13:00 ET ──
        # On entre sur la premiere barre apres 10:30

        # ── LONG sur le leader sectoriel ──
        if best_etf in data:
            long_signal = self._create_entry_signal(
                data[best_etf], best_etf, "LONG", best_perf, spy_adx_val, sector_perfs
            )
            if long_signal is not None:
                signals.append(long_signal)

        # ── SHORT sur le laggard sectoriel ──
        if worst_etf in data:
            short_signal = self._create_entry_signal(
                data[worst_etf], worst_etf, "SHORT", worst_perf, spy_adx_val, sector_perfs
            )
            if short_signal is not None:
                signals.append(short_signal)

        return signals

    def _create_entry_signal(
        self,
        df: pd.DataFrame,
        ticker: str,
        action: str,
        relative_perf: float,
        spy_adx: float,
        all_perfs: dict,
    ) -> Signal | None:
        """
        Cree un signal d'entree pour un ETF dans la fenetre 10:30-13:00.
        Retourne None si les conditions ne sont pas remplies.
        """
        entry_window = df.between_time("10:30", "13:00")
        if entry_window.empty:
            return None

        # Prendre la premiere barre de la fenetre comme point d'entree
        entry_bar = entry_window.iloc[0]
        ts = entry_window.index[0]
        entry_price = entry_bar["close"]

        if entry_price <= 0:
            return None

        # ── Calculer ATR(14) pour le stop ──
        atr_series = self._compute_atr_series(df, self.atr_period)
        atr_idx = atr_series.index.get_indexer([ts], method="pad")
        if atr_idx[0] < 1:
            return None
        # Anti-lookahead : utiliser la valeur de la barre precedente
        current_atr = atr_series.iloc[atr_idx[0] - 1]
        if pd.isna(current_atr) or current_atr <= 0:
            return None

        risk = self.atr_stop_multiplier * current_atr

        if action == "LONG":
            stop_loss = entry_price - risk
            take_profit = entry_price + risk * self.rr_ratio
        else:  # SHORT
            stop_loss = entry_price + risk
            take_profit = entry_price - risk * self.rr_ratio

        return Signal(
            action=action,
            ticker=ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=ts,
            metadata={
                "strategy": self.name,
                "relative_perf_pct": round(relative_perf * 100, 2),
                "spy_adx": round(spy_adx, 1),
                "sector_perfs": {k: round(v * 100, 2) for k, v in sorted(
                    all_perfs.items(), key=lambda x: x[1], reverse=True
                )},
                "atr": round(current_atr, 4),
            },
        )

    @staticmethod
    def _compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Retourne la serie ATR complete."""
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()
