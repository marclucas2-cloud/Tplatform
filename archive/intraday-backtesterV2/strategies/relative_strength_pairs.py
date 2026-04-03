"""
Stratégie 9 : Relative Strength Pairs (10:30-15:55 ET)

Edge structurel :
Contrairement aux pairs convergentes classiques (qui échouent en intraday à
cause des coûts), cette stratégie fait du MOMENTUM relatif : long le leader,
short le laggard. Le momentum relatif intraday persiste car les flux
institutionnels sont lents (TWAP). Si NVDA surperforme AMD depuis l'open,
le flux continue.

Règles :
- Mesure du momentum relatif 9:30-10:30. Entrée 10:30-13:00. Exit 15:55.
- Pour chaque paire (A, B) : si return_A - return_B > 0.5% → LONG A + SHORT B.
  Si < -0.5% → SHORT A + LONG B.
- Ne prendre que la paire avec le plus grand spread.
- Stop : 1.5x ATR moyen de la paire.
- Target : 2x le risque, ou exit à 15:55.
- Filtres : |spread| >= 0.5%, pas de move sectoriel (2 stocks même direction > 2%),
  corrélation 20-jours >= 0.5.
- Fréquence : 0-2 paires/jour (chaque paire = 2 signaux : 1 LONG + 1 SHORT).
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


class RelativeStrengthPairsStrategy(BaseStrategy):
    name = "Relative Strength Pairs"

    # Paires pré-définies : secteurs corrélés
    PAIRS = [
        ("NVDA", "AMD"),      # GPU leaders
        ("XOM", "CVX"),       # Oil majors
        ("JPM", "BAC"),       # Banks
        ("GOOGL", "META"),    # Advertising tech
        ("AAPL", "MSFT"),     # Mega cap tech
        ("COIN", "MARA"),     # Crypto proxies
    ]

    def __init__(
        self,
        min_spread_pct: float = 0.5,
        max_same_direction_pct: float = 2.0,
        min_correlation: float = 0.5,
        atr_stop_multiplier: float = 1.5,
        risk_reward_ratio: float = 2.0,
        correlation_lookback: int = 20,
        max_pairs_per_day: int = 2,
    ):
        self.min_spread_pct = min_spread_pct / 100  # Convertir en décimal
        self.max_same_direction_pct = max_same_direction_pct / 100
        self.min_correlation = min_correlation
        self.atr_stop_multiplier = atr_stop_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.correlation_lookback = correlation_lookback
        self.max_pairs_per_day = max_pairs_per_day

    def get_required_tickers(self) -> list[str]:
        tickers = set()
        for a, b in self.PAIRS:
            tickers.add(a)
            tickers.add(b)
        tickers.add("SPY")
        return sorted(tickers)

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        pair_candidates = []

        for ticker_a, ticker_b in self.PAIRS:
            if ticker_a not in data or ticker_b not in data:
                continue

            df_a = data[ticker_a]
            df_b = data[ticker_b]

            if len(df_a) < 30 or len(df_b) < 30:
                continue

            # ── Open du jour pour chaque ticker ──
            open_bars_a = df_a.between_time("09:30", "09:31")
            open_bars_b = df_b.between_time("09:30", "09:31")

            if open_bars_a.empty or open_bars_b.empty:
                continue

            open_a = open_bars_a.iloc[0]["open"]
            open_b = open_bars_b.iloc[0]["open"]

            if open_a <= 0 or open_b <= 0:
                continue

            # ── Momentum relatif à 10:30 ──
            # On utilise la dernière barre AVANT 10:30 (anti-lookahead)
            bars_a_1030 = df_a.between_time("10:25", "10:35")
            bars_b_1030 = df_b.between_time("10:25", "10:35")

            if bars_a_1030.empty or bars_b_1030.empty:
                continue

            # Utiliser la première barre de la fenêtre (la plus proche de 10:25-10:30)
            price_a_1030 = bars_a_1030.iloc[0]["close"]
            price_b_1030 = bars_b_1030.iloc[0]["close"]

            return_a = (price_a_1030 - open_a) / open_a
            return_b = (price_b_1030 - open_b) / open_b
            spread = return_a - return_b

            # ── Filtre 1 : spread suffisant (>= 0.5%) ──
            if abs(spread) < self.min_spread_pct:
                continue

            # ── Filtre 2 : pas de move sectoriel (les 2 dans la même direction > 2%) ──
            # Si les deux bougent fort dans la même direction, c'est un move de secteur
            if (return_a > self.max_same_direction_pct and return_b > self.max_same_direction_pct):
                continue
            if (return_a < -self.max_same_direction_pct and return_b < -self.max_same_direction_pct):
                continue

            # ── Filtre 3 : corrélation 20 barres >= 0.5 ──
            # Aligner les timestamps pour le calcul de corrélation
            common_idx = df_a.index.intersection(df_b.index)
            if len(common_idx) < self.correlation_lookback:
                continue

            # Corrélation sur les returns (pas les prix) — anti-spurious correlation
            returns_a = df_a.loc[common_idx, "close"].pct_change().dropna()
            returns_b = df_b.loc[common_idx, "close"].pct_change().dropna()

            if len(returns_a) < self.correlation_lookback:
                continue

            # Corrélation sur les N dernières barres avant 10:30
            returns_a_pre = returns_a[returns_a.index <= bars_a_1030.index[0]]
            returns_b_pre = returns_b[returns_b.index <= bars_b_1030.index[0]]

            # Aligner après filtrage
            common_pre = returns_a_pre.index.intersection(returns_b_pre.index)
            if len(common_pre) < self.correlation_lookback:
                continue

            corr = returns_a_pre.loc[common_pre].tail(self.correlation_lookback).corr(
                returns_b_pre.loc[common_pre].tail(self.correlation_lookback)
            )

            if pd.isna(corr) or corr < self.min_correlation:
                continue

            # ── Calculer l'ATR moyen de la paire pour le stop ──
            atr_a = self._compute_atr(df_a)
            atr_b = self._compute_atr(df_b)

            if atr_a is None or atr_b is None:
                continue

            pair_candidates.append({
                "ticker_a": ticker_a,
                "ticker_b": ticker_b,
                "spread": spread,
                "abs_spread": abs(spread),
                "return_a": return_a,
                "return_b": return_b,
                "correlation": round(corr, 3),
                "atr_a": atr_a,
                "atr_b": atr_b,
                "price_a": price_a_1030,
                "price_b": price_b_1030,
                "entry_ts": bars_a_1030.index[0],  # Timestamp de l'entrée
            })

        # ── Trier par spread le plus large, prendre la meilleure paire ──
        pair_candidates.sort(key=lambda x: x["abs_spread"], reverse=True)
        pair_candidates = pair_candidates[:self.max_pairs_per_day]

        for c in pair_candidates:
            # Trouver un timestamp d'entrée valide dans la fenêtre 10:30-13:00
            # On utilise le timestamp de la barre à 10:30 déjà récupéré
            entry_ts = c["entry_ts"]

            if c["spread"] > 0:
                # A surperforme B → LONG A (leader), SHORT B (laggard)
                # C'est du momentum relatif : on suit le leader
                long_ticker, short_ticker = c["ticker_a"], c["ticker_b"]
                long_price, short_price = c["price_a"], c["price_b"]
                long_atr, short_atr = c["atr_a"], c["atr_b"]
            else:
                # B surperforme A → LONG B (leader), SHORT A (laggard)
                long_ticker, short_ticker = c["ticker_b"], c["ticker_a"]
                long_price, short_price = c["price_b"], c["price_a"]
                long_atr, short_atr = c["atr_b"], c["atr_a"]

            # ── LONG le leader ──
            long_stop_distance = long_atr * self.atr_stop_multiplier
            signals.append(Signal(
                action="LONG",
                ticker=long_ticker,
                entry_price=long_price,
                stop_loss=long_price - long_stop_distance,
                take_profit=long_price + long_stop_distance * self.risk_reward_ratio,
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "pair": f"{c['ticker_a']}/{c['ticker_b']}",
                    "spread_pct": round(c["spread"] * 100, 2),
                    "return_a_pct": round(c["return_a"] * 100, 2),
                    "return_b_pct": round(c["return_b"] * 100, 2),
                    "correlation": c["correlation"],
                    "role": "leader",
                },
            ))

            # ── SHORT le laggard ──
            short_stop_distance = short_atr * self.atr_stop_multiplier
            signals.append(Signal(
                action="SHORT",
                ticker=short_ticker,
                entry_price=short_price,
                stop_loss=short_price + short_stop_distance,
                take_profit=short_price - short_stop_distance * self.risk_reward_ratio,
                timestamp=entry_ts,
                metadata={
                    "strategy": self.name,
                    "pair": f"{c['ticker_a']}/{c['ticker_b']}",
                    "spread_pct": round(c["spread"] * 100, 2),
                    "return_a_pct": round(c["return_a"] * 100, 2),
                    "return_b_pct": round(c["return_b"] * 100, 2),
                    "correlation": c["correlation"],
                    "role": "laggard",
                },
            ))

        return signals

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> float | None:
        """
        Calcule l'ATR en dollars (pas en pourcentage).
        Retourne None si pas assez de données.
        """
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

        if pd.isna(atr):
            return None

        return atr
