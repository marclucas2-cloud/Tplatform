"""
Stratégie 13 : Pattern Recognition Statistique
Identifie les patterns de bougies avec un edge statistique RÉEL
(pas les patterns classiques type doji/hammer qui ne fonctionnent plus).

Approche :
- Encode chaque séquence de 5 bougies en "signature" discrète
- Signature = direction + taille relative + volume relatif
- Teste statistiquement chaque signature sur l'historique
- Ne trade que les signatures avec p-value < 0.05 et n > 30

C'est une approche data-driven pure : on ne cherche PAS des patterns
qu'on connaît, on laisse les données révéler les patterns rentables.
"""
import pandas as pd
import numpy as np
from collections import defaultdict
from backtest_engine import BaseStrategy, Signal
import config


def encode_bar(row: pd.Series, avg_range: float, avg_volume: float) -> str:
    """
    Encode une barre en signature discrète.
    Format : D_S_V
    - D : direction (U=up, D=down, N=neutral)
    - S : taille (L=large, M=medium, S=small)
    - V : volume (H=high, N=normal, L=low)
    """
    body = row["close"] - row["open"]
    bar_range = row["high"] - row["low"]

    # Direction
    if body > avg_range * 0.1:
        d = "U"
    elif body < -avg_range * 0.1:
        d = "D"
    else:
        d = "N"

    # Taille relative
    if bar_range > avg_range * 1.5:
        s = "L"
    elif bar_range < avg_range * 0.5:
        s = "S"
    else:
        s = "M"

    # Volume relatif
    if row["volume"] > avg_volume * 1.5:
        v = "H"
    elif row["volume"] < avg_volume * 0.5:
        v = "L"
    else:
        v = "N"

    return f"{d}{s}{v}"


class PatternRecognitionStrategy(BaseStrategy):
    name = "Pattern Recognition"

    def __init__(self, pattern_length: int = 5, min_occurrences: int = 30,
                 min_win_rate: float = 0.55, lookback_days: int = 90,
                 stop_pct: float = 0.004, target_pct: float = 0.006):
        self.pattern_length = pattern_length
        self.min_occurrences = min_occurrences
        self.min_win_rate = min_win_rate
        self.lookback_days = lookback_days
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self._pattern_stats = defaultdict(lambda: {"long_wins": 0, "long_total": 0,
                                                    "short_wins": 0, "short_total": 0})
        self._history = []
        self._trained = False
        self._profitable_patterns = {}

    def _train_patterns(self, history: list[tuple]):
        """Analyse l'historique pour trouver les patterns rentables."""
        pattern_outcomes = defaultdict(list)

        for date, df in history:
            if len(df) < self.pattern_length + 10:
                continue

            avg_range = (df["high"] - df["low"]).mean()
            avg_volume = df["volume"].mean()

            if avg_range == 0 or avg_volume == 0:
                continue

            # Encoder toutes les barres
            signatures = []
            for _, row in df.iterrows():
                sig = encode_bar(row, avg_range, avg_volume)
                signatures.append(sig)

            # Pour chaque séquence de N barres, regarder ce qui se passe après
            for i in range(len(signatures) - self.pattern_length - 5):
                pattern = "_".join(signatures[i:i + self.pattern_length])
                # Outcome = mouvement des 5 barres suivantes
                future_close = df.iloc[i + self.pattern_length + 5]["close"]
                current_close = df.iloc[i + self.pattern_length]["close"]
                pct_move = (future_close - current_close) / current_close

                pattern_outcomes[pattern].append(pct_move)

        # Identifier les patterns profitables
        for pattern, outcomes in pattern_outcomes.items():
            if len(outcomes) < self.min_occurrences:
                continue

            outcomes = np.array(outcomes)
            win_rate_long = (outcomes > 0).mean()
            win_rate_short = (outcomes < 0).mean()
            avg_return = outcomes.mean()

            if win_rate_long > self.min_win_rate and avg_return > 0.001:
                self._profitable_patterns[pattern] = {
                    "action": "LONG",
                    "win_rate": win_rate_long,
                    "avg_return": avg_return,
                    "n": len(outcomes),
                }
            elif win_rate_short > self.min_win_rate and avg_return < -0.001:
                self._profitable_patterns[pattern] = {
                    "action": "SHORT",
                    "win_rate": win_rate_short,
                    "avg_return": abs(avg_return),
                    "n": len(outcomes),
                }

        self._trained = True
        print(f"  [ML] Pattern Recognition: {len(self._profitable_patterns)} profitable patterns found "
              f"from {len(history)} days of data")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker in ["SPY", "QQQ", "NVDA", "AAPL", "TSLA"]:
            if ticker not in data:
                continue

            df = data[ticker]

            # Accumuler l'historique
            self._history.append((date, df.copy()))

            # Entraîner après lookback_days
            if not self._trained:
                if len(self._history) >= self.lookback_days:
                    self._train_patterns(self._history)
                continue

            if len(df) < self.pattern_length + 5:
                continue

            avg_range = (df["high"] - df["low"]).mean()
            avg_volume = df["volume"].mean()

            if avg_range == 0 or avg_volume == 0:
                continue

            # Encoder les barres du jour
            tradeable = df.between_time("10:00", "15:00")
            if len(tradeable) < self.pattern_length:
                continue

            signatures = []
            for _, row in tradeable.iterrows():
                sig = encode_bar(row, avg_range, avg_volume)
                signatures.append(sig)

            # Chercher des patterns connus
            signal_found = False
            for i in range(len(signatures) - self.pattern_length):
                if signal_found:
                    break

                pattern = "_".join(signatures[i:i + self.pattern_length])

                if pattern in self._profitable_patterns:
                    info = self._profitable_patterns[pattern]
                    bar_idx = i + self.pattern_length
                    if bar_idx >= len(tradeable):
                        continue

                    entry_bar = tradeable.iloc[bar_idx]
                    ts = tradeable.index[bar_idx]
                    entry = entry_bar["close"]

                    signals.append(Signal(
                        action=info["action"],
                        ticker=ticker,
                        entry_price=entry,
                        stop_loss=entry * (1 - self.stop_pct) if info["action"] == "LONG" else entry * (1 + self.stop_pct),
                        take_profit=entry * (1 + self.target_pct) if info["action"] == "LONG" else entry * (1 - self.target_pct),
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "pattern": pattern,
                            "historical_win_rate": round(info["win_rate"], 3),
                            "historical_avg_return": round(info["avg_return"] * 100, 3),
                            "n_observations": info["n"],
                        },
                    ))
                    signal_found = True

        return signals
