"""
Strategie : Opening Gap Fill

Edge structurel :
Les petits gaps d'ouverture (0.3-1.0%) sur les actions liquides tendent a
se "remplir" (revenir au close de la veille) dans les 2 premieres heures.
Les gros gaps (>2%) ne fill PAS — ils continuent. On ne trade que les petits gaps.

Regles :
- Gap d'ouverture entre 0.3% et 1.0% (exclusion des gros gaps)
- Prix > $10, pas d'ETFs leverages
- Attendre 9:35 pour confirmer la direction (pas de trade au market open)
- FADE le gap : SHORT si gap up, LONG si gap down
- Stop : 0.6% au-dela du prix d'ouverture (extension du gap)
- Target : close de la veille (gap fill complet)
- Max 3 trades/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
    # ETFs passifs (gaps non informatifs)
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO",
}

MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
GAP_MIN_PCT = 0.3         # Gap minimum 0.3%
GAP_MAX_PCT = 1.0          # Gap maximum 1.0%
STOP_EXTENSION_PCT = 0.006 # Stop 0.6% au-dela de l'open
CONFIRMATION_BARS = 2      # Attendre 2 barres de confirmation


class OpeningGapFillStrategy(BaseStrategy):
    name = "Opening Gap Fill"

    def __init__(
        self,
        gap_min_pct: float = GAP_MIN_PCT,
        gap_max_pct: float = GAP_MAX_PCT,
        stop_extension: float = STOP_EXTENSION_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.gap_min_pct = gap_min_pct
        self.gap_max_pct = gap_max_pct
        self.stop_extension = stop_extension
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 10:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # ── Close de la veille ──
            day_bars = df[df.index.date == date]
            if len(day_bars) < 5:
                continue

            prev_day_bars = df[df.index.date < date]
            if prev_day_bars.empty:
                continue

            prev_close = prev_day_bars.iloc[-1]["close"]
            if prev_close <= 0:
                continue

            today_open = day_bars.iloc[0]["open"]
            gap_pct = ((today_open - prev_close) / prev_close) * 100

            # Filtre : gap dans la range 0.3%-1.0%
            if abs(gap_pct) < self.gap_min_pct or abs(gap_pct) > self.gap_max_pct:
                continue

            gap_direction = "UP" if gap_pct > 0 else "DOWN"

            # ── Chercher confirmation entre 9:35 et 11:30 ──
            confirmation_bars = day_bars.between_time("09:35", "11:30")
            if len(confirmation_bars) < CONFIRMATION_BARS:
                continue

            signal_found = False

            for idx, (ts, bar) in enumerate(confirmation_bars.iterrows()):
                if signal_found:
                    break

                # Attendre au moins 2 barres de confirmation
                if idx < CONFIRMATION_BARS:
                    continue

                entry_price = bar["close"]

                if gap_direction == "UP":
                    # Gap up → FADE = SHORT, target = prev_close (gap fill)
                    # Confirmer que le prix commence a redescendre
                    if bar["close"] < bar["open"]:  # Barre baissiere = debut du fill
                        stop_loss = today_open * (1 + self.stop_extension)
                        take_profit = prev_close

                        risk = stop_loss - entry_price
                        reward = entry_price - take_profit
                        if risk > 0 and reward > 0 and risk / entry_price < 0.02:
                            candidates.append({
                                "score": abs(gap_pct) * (reward / risk),
                                "signal": Signal(
                                    action="SHORT",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "gap_pct": round(gap_pct, 2),
                                        "prev_close": round(prev_close, 2),
                                        "today_open": round(today_open, 2),
                                    },
                                ),
                            })
                            signal_found = True

                else:
                    # Gap down → FADE = LONG, target = prev_close (gap fill)
                    if bar["close"] > bar["open"]:  # Barre haussiere = debut du fill
                        stop_loss = today_open * (1 - self.stop_extension)
                        take_profit = prev_close

                        risk = entry_price - stop_loss
                        reward = take_profit - entry_price
                        if risk > 0 and reward > 0 and risk / entry_price < 0.02:
                            candidates.append({
                                "score": abs(gap_pct) * (reward / risk),
                                "signal": Signal(
                                    action="LONG",
                                    ticker=ticker,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    timestamp=ts,
                                    metadata={
                                        "strategy": self.name,
                                        "gap_pct": round(gap_pct, 2),
                                        "prev_close": round(prev_close, 2),
                                        "today_open": round(today_open, 2),
                                    },
                                ),
                            })
                            signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
