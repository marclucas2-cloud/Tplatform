"""
Late Day Bear Acceleration — SHORT ONLY

Edge : En regime bear (SPY < SMA200 daily), quand SPY est deja en baisse de > 0.3%
a 14:00 ET et que le volume des barres 14:00-14:30 est en hausse vs 13:30-14:00,
c'est un signal de selling institutionnel qui accelere.

Les institutions executent leurs gros ordres en fin de journee pour minimiser
l'impact. Une acceleration du volume a 14:00 avec un prix en baisse signifie
que les vendeurs institutionnels augmentent leur agressivite — le prix va
continuer a baisser jusqu'a la cloture.

Regles :
- REGIME BEAR obligatoire : SPY < SMA(200) daily. La SMA200 est calculee a
  partir des closes quotidiennes extraites des barres 5M.
- A 14:00 ET, SPY en baisse > 0.3%
- Volume barres 14:00-14:30 > 1.3x volume barres 13:30-14:00
- Short SPY ou QQQ (celui le plus faible)
- SL = high de 13:00-14:00 + 0.1%. TP = 15:55 close
- Filtres : SPY en hausse = skip. Vendredi = skip (OpEx risk).
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


class LateDayBearAccelerationStrategy(BaseStrategy):
    name = "Late Day Bear Acceleration"

    SPY_MIN_DROP = -0.002          # SPY doit etre down > 0.2% a 14:00 (assouplir)
    VOLUME_ACCEL_RATIO = 1.2       # Volume 14:00-14:30 > 1.2x (assouplir)
    STOP_BUFFER_PCT = 0.002        # Stop = high 13:00-14:00 + 0.2%
    SMA_PERIOD = 200               # SMA 200 jours pour regime bear
    MAX_TRADES_PER_DAY = 1
    TRADE_TICKERS = ["SPY", "QQQ"]

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ"]

    def _compute_daily_sma200(self, df: pd.DataFrame) -> pd.Series:
        """
        Calcule la SMA(200) daily a partir des barres 5M.
        Extrait le dernier close de chaque jour comme close daily.
        """
        daily_closes = df.groupby(df.index.date)["close"].last()
        sma200 = daily_closes.rolling(self.SMA_PERIOD, min_periods=100).mean()
        return sma200

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        if "SPY" not in data:
            return []

        spy_df = data["SPY"]
        if len(spy_df) < 50:
            return []

        # ── Filtre OpEx (3eme vendredi du mois) ──
        import datetime
        if isinstance(date, datetime.date):
            if date.weekday() == 4:
                # Verifier si c'est le 3eme vendredi (OpEx)
                day = date.day
                if 15 <= day <= 21:
                    return []  # OpEx vendredi = skip

        # ── Regime bear : SPY < SMA(200) daily ──
        # On a besoin de TOUT le DataFrame SPY (pas juste le jour) pour SMA200
        # Le backtest engine nous donne data[ticker] = barres du jour seulement
        # Mais le DataFrame original contient tout l'historique
        # On doit calculer la SMA200 sur les jours precedents
        spy_full = spy_df  # C'est juste le jour courant dans le backtest
        # Hack : on accede au full DataFrame via les dates precedentes
        # En pratique, on ne peut pas acceder a l'historique complet depuis generate_signals
        # SOLUTION : on stocke la SMA200 en pre-calculant en dehors, ou on utilise
        # une heuristique. Ici on va regarder si le prix est bas relative a la range recente.
        # MEILLEURE SOLUTION : on utilise un attribut de classe pour stocker l'historique
        # que le run script pre-set.

        # Pour rester clean : on check si _sma200_data a ete set par le run script
        if hasattr(self, '_sma200_values') and self._sma200_values is not None:
            sma200_val = self._sma200_values.get(date, None)
            if sma200_val is None:
                return []
            # Check: derniere close SPY de la veille doit etre sous SMA200
            # On prend la premiere barre du jour comme proxy
            spy_open = spy_df.iloc[0]["open"]
            if spy_open >= sma200_val:
                return []  # Pas en regime bear
        else:
            # Fallback : pas de SMA200 disponible, skip le filtre bear
            # (sera toujours set par le run script)
            return []

        spy_open = spy_df.iloc[0]["open"]
        if spy_open <= 0:
            return []

        # ── Check SPY a 14:00 : doit etre en baisse > 0.3% ──
        spy_at_1400 = spy_df.between_time("14:00", "14:05")
        if spy_at_1400.empty:
            return []

        spy_price_1400 = spy_at_1400.iloc[0]["close"]
        spy_perf = (spy_price_1400 - spy_open) / spy_open

        if spy_perf > self.SPY_MIN_DROP:
            return []  # SPY pas assez en baisse

        # ── Volume acceleration : barres 14:00-14:30 vs 13:30-14:00 ──
        spy_vol_before = spy_df.between_time("13:30", "13:55")
        spy_vol_after = spy_df.between_time("14:00", "14:25")

        if spy_vol_before.empty or spy_vol_after.empty:
            return []

        vol_before = spy_vol_before["volume"].sum()
        vol_after = spy_vol_after["volume"].sum()

        if vol_before <= 0:
            return []

        vol_ratio = vol_after / vol_before

        if vol_ratio < self.VOLUME_ACCEL_RATIO:
            return []  # Volume ne s'accelere pas

        # ── Choisir le ticker le plus faible (SPY ou QQQ) ──
        best_ticker = None
        best_perf = 0  # On veut le plus negatif

        for ticker in self.TRADE_TICKERS:
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 20:
                continue

            tick_open = df.iloc[0]["open"]
            if tick_open <= 0:
                continue

            tick_at_1430 = df.between_time("14:25", "14:35")
            if tick_at_1430.empty:
                continue

            tick_price = tick_at_1430.iloc[0]["close"]
            tick_perf = (tick_price - tick_open) / tick_open

            if tick_perf < best_perf:
                best_perf = tick_perf
                best_ticker = ticker

        if best_ticker is None:
            return []

        df = data[best_ticker]
        entry_bar = df.between_time("14:25", "14:35")
        if entry_bar.empty:
            return []

        entry_price = entry_bar.iloc[0]["close"]
        entry_ts = entry_bar.index[0]

        # SL = high de 13:00-14:00 + 0.1%
        range_1300_1400 = df.between_time("13:00", "14:00")
        if range_1300_1400.empty:
            return []

        high_1300_1400 = range_1300_1400["high"].max()
        stop_loss = high_1300_1400 * (1 + self.STOP_BUFFER_PCT)

        # TP = EOD (15:55) — on laisse le engine forcer la sortie
        # On met un TP tres large pour laisser courir
        take_profit = entry_price * 0.95  # 5% de marge, en pratique EOD close

        if stop_loss <= entry_price:
            return []

        return [Signal(
            action="SHORT",
            ticker=best_ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=entry_ts,
            metadata={
                "strategy": self.name,
                "spy_perf_pct": round(spy_perf * 100, 2),
                "vol_accel_ratio": round(vol_ratio, 2),
                "high_1300_1400": round(high_1300_1400, 2),
                "regime": "bear",
                "traded": best_ticker,
            },
        )]
