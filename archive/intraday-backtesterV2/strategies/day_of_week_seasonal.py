"""
Strategie 11 : Day-of-Week Seasonal

Edge structurel :
Anomalie academique documentee : les lundis ont historiquement un biais negatif
(le "Monday effect") et les vendredis un biais positif (position squaring avant
le weekend). De plus, le debut de mois (jours 1-3) a un biais haussier du aux
flux de pension funds et 401k.

Regles :
- LUNDI : biais SHORT. Si prix a 10:00 < VWAP et RSI < 45, SHORT SPY/QQQ.
  Stop 0.5%, target 0.3%.
- VENDREDI : biais LONG. Si prix a 10:00 > VWAP et RSI > 55, LONG SPY/QQQ.
  Stop 0.5%, target 0.3%.
- DEBUT DE MOIS (jours 1-3) : biais LONG, meme logique que vendredi mais
  sur stocks individuels aussi. Volume > 1.2x moyenne.
- Filtres : skip si VIX proxy > seuil (ATR SPY 20j > 2%), skip si gap > 1%.
- Timing : entree vers 10:00 ET, exit fin de journee (15:55).
- Frequence : 0-3 trades/jour (seulement lundi, vendredi, ou debut de mois).
- Un seul signal par ticker par jour.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap, rsi
import config


# ── Parametres ──
STOP_PCT = 0.005           # 0.5% stop-loss
TARGET_PCT = 0.003         # 0.3% take-profit
RSI_LONG_THRESHOLD = 55    # RSI min pour entrees LONG (vendredi / debut mois)
RSI_SHORT_THRESHOLD = 45   # RSI max pour entrees SHORT (lundi)
VOL_MULTIPLIER_BOM = 1.2   # Volume multiplier pour debut de mois
ATR_HIGH_VOL_PCT = 0.02    # ATR SPY > 2% = haute volatilite → pas de seasonal
GAP_MAX_PCT = 0.01         # Gap d'ouverture max 1%

# Tickers cibles
ETF_TICKERS = ["SPY", "QQQ"]
STOCK_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"]
ALL_TICKERS = ETF_TICKERS + STOCK_TICKERS


class DayOfWeekSeasonalStrategy(BaseStrategy):
    name = "Day-of-Week Seasonal"

    def __init__(
        self,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        rsi_long: float = RSI_LONG_THRESHOLD,
        rsi_short: float = RSI_SHORT_THRESHOLD,
        vol_bom_multiplier: float = VOL_MULTIPLIER_BOM,
        atr_high_vol: float = ATR_HIGH_VOL_PCT,
        gap_max: float = GAP_MAX_PCT,
    ):
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.rsi_long = rsi_long
        self.rsi_short = rsi_short
        self.vol_bom_multiplier = vol_bom_multiplier
        self.atr_high_vol = atr_high_vol
        self.gap_max = gap_max

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── Determiner le jour de la semaine et le jour du mois ──
        # date peut etre un datetime.date ou datetime.datetime
        if hasattr(date, "weekday"):
            weekday = date.weekday()  # 0=Lundi, 4=Vendredi
            day_of_month = date.day
        else:
            weekday = pd.Timestamp(date).weekday()
            day_of_month = pd.Timestamp(date).day

        is_monday = weekday == 0
        is_friday = weekday == 4
        is_bom = day_of_month <= 3  # Debut de mois (jours 1-3)

        # ── Si ni lundi, ni vendredi, ni debut de mois → pas de signal ──
        if not is_monday and not is_friday and not is_bom:
            return signals

        # ── Filtre haute volatilite : ATR proxy via SPY ──
        if not self._is_low_vol_regime(data):
            return signals

        # ── Filtre gap d'ouverture trop grand sur SPY ──
        if self._has_large_gap(data):
            return signals

        # ── Determiner les tickers candidats et le biais ──
        # Lundi : SHORT sur ETFs seulement
        # Vendredi : LONG sur ETFs seulement
        # Debut de mois : LONG sur ETFs + stocks individuels
        candidates = []

        if is_monday:
            candidates.append(("SHORT", ETF_TICKERS, False))

        if is_friday:
            candidates.append(("LONG", ETF_TICKERS, False))

        if is_bom:
            # Le biais debut de mois est LONG, sur ETFs et stocks
            candidates.append(("LONG", ALL_TICKERS, True))  # True = filtre volume

        traded_tickers = set()

        for bias, tickers, require_volume_filter in candidates:
            for ticker in tickers:
                if ticker in traded_tickers:
                    continue
                if ticker not in data:
                    continue

                df = data[ticker]
                if len(df) < 20:
                    continue

                df = df.copy()

                # ── Calculer VWAP et RSI ──
                df["vwap_calc"] = calc_vwap(df)
                df["rsi_calc"] = rsi(df["close"], period=14)
                df["vol_avg_20"] = df["volume"].rolling(20).mean()

                # ── Chercher la barre autour de 10:00 ET ──
                around_10am = df.between_time("09:55", "10:05")
                if around_10am.empty:
                    continue

                # Prendre la barre la plus proche de 10:00
                entry_bar_idx = 0
                for idx in range(len(around_10am)):
                    if around_10am.index[idx].time() >= pd.Timestamp("10:00").time():
                        entry_bar_idx = idx
                        break

                entry_bar = around_10am.iloc[entry_bar_idx]
                ts = around_10am.index[entry_bar_idx]

                # ── Valeurs indicateurs (anti-lookahead : on utilise les donnees deja connues) ──
                vwap_val = entry_bar["vwap_calc"]
                rsi_val = entry_bar["rsi_calc"]
                vol_avg = entry_bar["vol_avg_20"]

                if pd.isna(vwap_val) or pd.isna(rsi_val):
                    continue

                price = entry_bar["close"]

                # ── Filtre volume pour debut de mois ──
                if require_volume_filter:
                    if pd.isna(vol_avg) or vol_avg <= 0:
                        continue
                    if entry_bar["volume"] < self.vol_bom_multiplier * vol_avg:
                        continue

                # ── Conditions d'entree selon le biais ──
                if bias == "LONG":
                    # Prix > VWAP et RSI > seuil
                    if price <= vwap_val or rsi_val <= self.rsi_long:
                        continue

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=price,
                        stop_loss=price * (1 - self.stop_pct),
                        take_profit=price * (1 + self.target_pct),
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "bias": "LONG",
                            "reason": "friday" if is_friday else "bom",
                            "weekday": weekday,
                            "day_of_month": day_of_month,
                            "rsi": round(rsi_val, 1),
                            "price_vs_vwap_pct": round(
                                (price - vwap_val) / vwap_val * 100, 3
                            ),
                        },
                    ))
                    traded_tickers.add(ticker)

                elif bias == "SHORT":
                    # Prix < VWAP et RSI < seuil
                    if price >= vwap_val or rsi_val >= self.rsi_short:
                        continue

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=price,
                        stop_loss=price * (1 + self.stop_pct),
                        take_profit=price * (1 - self.target_pct),
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "bias": "SHORT",
                            "reason": "monday",
                            "weekday": weekday,
                            "day_of_month": day_of_month,
                            "rsi": round(rsi_val, 1),
                            "price_vs_vwap_pct": round(
                                (price - vwap_val) / vwap_val * 100, 3
                            ),
                        },
                    ))
                    traded_tickers.add(ticker)

        return signals

    def _is_low_vol_regime(self, data: dict[str, pd.DataFrame]) -> bool:
        """
        Proxy VIX : si l'ATR 20 barres de SPY > 2% du prix moyen,
        on considere que c'est un regime de haute vol → pas de seasonal fiable.
        """
        if "SPY" not in data:
            return True  # Pas de SPY = on ne peut pas filtrer, on laisse passer

        df_spy = data["SPY"]
        if len(df_spy) < 21:
            return True

        # Calculer l'ATR sur les 20 dernieres barres
        high = df_spy["high"]
        low = df_spy["low"]
        close = df_spy["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_20 = tr.rolling(20).mean().iloc[-1]
        avg_price = close.iloc[-20:].mean()

        if pd.isna(atr_20) or avg_price <= 0:
            return True

        atr_pct = atr_20 / avg_price
        return atr_pct <= self.atr_high_vol

    def _has_large_gap(self, data: dict[str, pd.DataFrame]) -> bool:
        """
        Verifie si le gap d'ouverture de SPY depasse le seuil.
        Un gap > 1% signifie un event override → pas de seasonal.
        On compare l'open de la 1ere barre au close de la derniere barre
        disponible (qui est le close de la veille si les donnees sont du meme jour).
        """
        if "SPY" not in data:
            return False

        df_spy = data["SPY"]
        if len(df_spy) < 2:
            return False

        # On estime le gap en comparant open de la 1ere barre vs close de la 2e barre
        # (le mieux qu'on puisse faire avec des donnees intraday d'un seul jour)
        # En pratique, on regarde si le 1er prix est tres eloigne du VWAP apres 30 min
        first_bar_open = df_spy.iloc[0]["open"]
        # Utiliser les premieres barres pour estimer le "previous close"
        # Si le prix ouvre tres loin du range typique, c'est un gap
        morning = df_spy.between_time("09:30", "09:35")
        if morning.empty:
            return False

        day_open = morning.iloc[0]["open"]
        # Estimer "previous close" : on prend le prix moyen des 5 premieres minutes
        # et on verifie l'ecart entre open et le range des premieres barres
        early_bars = df_spy.between_time("09:30", "09:45")
        if len(early_bars) < 2:
            return False

        # Le gap est estime par la volatilite de l'open :
        # si le range des 15 premieres minutes > 1%, c'est probablement un gap day
        open_range = (early_bars["high"].max() - early_bars["low"].min()) / day_open
        return open_range > self.gap_max
