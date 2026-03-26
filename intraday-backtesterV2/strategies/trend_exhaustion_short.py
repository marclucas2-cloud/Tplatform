"""
Trend Exhaustion Short — SHORT ONLY

Edge : Les stocks qui montent > 3% depuis l'open sont souvent des chasses aux stops
et momentum trades du matin. Quand le volume des 6 dernieres barres 5M baisse
significativement vs les 6 barres precedentes, le momentum s'epuise.

C'est un signal purement prix + volume :
- Le move est reel (> 3% depuis l'open)
- Mais les acheteurs se tarissent (volume en chute)
- Le prix va retracer une partie du move

PAS d'indicateurs techniques (RSI, BB) — uniquement prix + volume brut.

Regles :
- TIMING : 11:00-14:30 ET seulement (matin = trop tot, close = trop tard)
- Scanner l'univers : stocks avec move > 3% depuis l'open
- Volume des 6 dernieres barres 5M < 70% du volume des 6 barres precedentes
- Shorter ce stock
- SL = high du jour + 0.2%. TP = retracement 50% du move depuis l'open. EOD sinon.
- Filtres : Volume global (barres du jour) < ADV estime = skip. Max 2 trades/jour.
- Exclure les leveraged ETFs
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# Leveraged / inverse ETFs a exclure
EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "PSQ", "SH", "SDS",
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO",
}


class TrendExhaustionShortStrategy(BaseStrategy):
    name = "Trend Exhaustion Short"

    MOVE_THRESHOLD = 0.025         # Stock doit avoir monte > 2.5% depuis l'open
    VOLUME_DECAY_RATIO = 0.65      # Volume recent < 65% du volume precedent (plus strict = meilleur filtre)
    LOOKBACK_BARS = 6              # 6 barres de 5 min = 30 min
    STOP_BUFFER_PCT = 0.003        # Stop = HOD + 0.3% (un peu plus large)
    RETRACEMENT_PCT = 0.40         # TP = retracement 40% du move (plus conservateur)
    MIN_PRICE = 15.0               # Prix minimum $15
    MAX_TRADES_PER_DAY = 2
    MIN_DAY_VOLUME = 200_000       # Volume minimum journee (filtre liquidite)
    MAX_MOVE = 0.08                # Skip si move > 8% (probable news/earnings)

    def get_required_tickers(self) -> list[str]:
        from universe import PERMANENT_TICKERS, SECTOR_MAP
        tickers = list(PERMANENT_TICKERS)
        for components in SECTOR_MAP.values():
            tickers.extend(components[:5])
        return list(set(tickers))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        # ── Filtre : SPY ne doit pas etre en forte baisse (> -1%) ──
        # En marche baissier, les montees individuelles sont souvent des short squeezes
        # qui ont plus de force et ne se retracent pas facilement
        if "SPY" in data:
            spy_df = data["SPY"]
            spy_day = spy_df[spy_df.index.date == date]
            if len(spy_day) > 10:
                spy_open = spy_day.iloc[0]["open"]
                spy_noon = spy_day.between_time("11:00", "11:30")
                if not spy_noon.empty and spy_open > 0:
                    spy_perf = (spy_noon.iloc[0]["close"] - spy_open) / spy_open
                    if spy_perf < -0.01:
                        return []  # Marche trop baissier, skip

        for ticker, df in data.items():
            if ticker in EXCLUDE:
                continue
            if len(df) < 30:
                continue

            # Barres du jour
            day_bars = df[df.index.date == date]
            if len(day_bars) < 20:  # Il faut assez de barres (au moins ~12 = 1h)
                continue

            today_open = day_bars.iloc[0]["open"]
            if today_open <= 0 or today_open < self.MIN_PRICE:
                continue

            # Volume total du jour — filtre de liquidite
            day_volume = day_bars["volume"].sum()
            if day_volume < self.MIN_DAY_VOLUME:
                continue

            # Scanner les barres entre 11:00 et 14:30 ET
            tradeable = day_bars.between_time("11:00", "14:30")
            if len(tradeable) < self.LOOKBACK_BARS * 2:
                continue

            signal_found = False
            for i in range(self.LOOKBACK_BARS * 2, len(tradeable)):
                if signal_found:
                    break

                ts = tradeable.index[i]
                bar = tradeable.iloc[i]
                current_price = bar["close"]

                # ── Move > 2.5% depuis l'open (mais pas > 8% = news) ──
                move_pct = (current_price - today_open) / today_open
                if move_pct < self.MOVE_THRESHOLD:
                    continue
                if move_pct > self.MAX_MOVE:
                    continue  # Probable news/earnings, skip

                # ── Volume decay : 6 dernieres barres vs 6 barres precedentes ──
                recent_6 = tradeable.iloc[i - self.LOOKBACK_BARS:i]
                prev_6 = tradeable.iloc[i - self.LOOKBACK_BARS * 2:i - self.LOOKBACK_BARS]

                recent_vol = recent_6["volume"].sum()
                prev_vol = prev_6["volume"].sum()

                if prev_vol <= 0:
                    continue

                vol_ratio = recent_vol / prev_vol

                if vol_ratio >= self.VOLUME_DECAY_RATIO:
                    continue  # Volume ne decline pas assez

                # ── Confirmation : la barre actuelle doit etre rouge (close < open) ──
                if bar["close"] >= bar["open"]:
                    continue  # Pas de signe d'essoufflement dans le prix

                # ── Signal SHORT ──
                hod = day_bars.loc[:ts, "high"].max()
                stop_loss = hod * (1 + self.STOP_BUFFER_PCT)

                # TP = retracement 50% du move
                move_total = current_price - today_open
                take_profit = current_price - (move_total * self.RETRACEMENT_PCT)

                risk = stop_loss - current_price
                if risk <= 0:
                    continue

                reward = current_price - take_profit
                if reward <= 0:
                    continue

                candidates.append({
                    "score": move_pct * (1 - vol_ratio),  # Plus le move est grand et le vol decay fort, meilleur le signal
                    "signal": Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=current_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "move_pct": round(move_pct * 100, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "hod": round(hod, 2),
                            "today_open": round(today_open, 2),
                        },
                    ),
                })
                signal_found = True

        # Trier par score et limiter
        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.MAX_TRADES_PER_DAY]:
            signals.append(c["signal"])

        return signals
