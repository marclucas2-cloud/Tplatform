"""
Strategie : Overnight Sector Winner

Edge structurel :
Le secteur qui surperforme SPY dans la journee a tendance a gapper up
le lendemain matin. Les flux institutionnels sont persistants sur 24h :
les achats de la journee se prolongent en after-hours et pre-market.

Regles :
- A 15:45-15:50, calculer la performance intraday de chaque ETF sectoriel vs SPY
- Acheter l'ETF qui surperforme SPY de > 0.5%
- Entree a la derniere barre avant 15:55
- Skip vendredi (weekend risk)
- Max 1 position
- Stop : 3%, TP : 1.5%
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal


# ── Parametres ──
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE"]
BENCHMARK = "SPY"
OUTPERFORMANCE_MIN = 0.005    # Secteur doit battre SPY de > 0.5%
STOP_PCT = 0.03               # Stop 3%
TP_PCT = 0.015                # TP 1.5%
MAX_TRADES_PER_DAY = 1


class OvernightSectorWinnerStrategy(BaseStrategy):
    name = "Overnight Sector Winner"

    def __init__(
        self,
        outperformance_min: float = OUTPERFORMANCE_MIN,
        stop_pct: float = STOP_PCT,
        tp_pct: float = TP_PCT,
    ):
        self.outperformance_min = outperformance_min
        self.stop_pct = stop_pct
        self.tp_pct = tp_pct

    def get_required_tickers(self) -> list[str]:
        return SECTOR_ETFS + [BENCHMARK]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── Skip vendredi (weekend risk) ──
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        if weekday == 4:  # Vendredi
            return signals

        # ── SPY requis pour le benchmark ──
        if BENCHMARK not in data:
            return signals

        df_spy = data[BENCHMARK]
        if len(df_spy) < 10:
            return signals

        # ── Performance intraday SPY ──
        spy_open = df_spy.iloc[0]["open"]
        if spy_open <= 0:
            return signals

        # ── Barres d'evaluation : 15:45-15:50 ──
        spy_eval = df_spy.between_time("15:45", "15:54")
        if spy_eval.empty:
            return signals

        spy_last_close = spy_eval.iloc[-1]["close"]
        spy_perf = (spy_last_close - spy_open) / spy_open

        # ── Evaluer chaque ETF sectoriel ──
        candidates = []

        for etf in SECTOR_ETFS:
            if etf not in data:
                continue

            df_etf = data[etf]
            if len(df_etf) < 10:
                continue

            etf_open = df_etf.iloc[0]["open"]
            if etf_open <= 0:
                continue

            # Barres de fin de journee
            etf_eval = df_etf.between_time("15:45", "15:54")
            if etf_eval.empty:
                continue

            etf_last_close = etf_eval.iloc[-1]["close"]
            etf_perf = (etf_last_close - etf_open) / etf_open

            # Surperformance vs SPY
            relative_perf = etf_perf - spy_perf

            if relative_perf > self.outperformance_min:
                candidates.append({
                    "ticker": etf,
                    "relative_perf": relative_perf,
                    "entry_price": etf_last_close,
                    "timestamp": etf_eval.index[-1],
                    "etf_perf": etf_perf,
                })

        if not candidates:
            return signals

        # ── Prendre le meilleur secteur ──
        candidates.sort(key=lambda c: c["relative_perf"], reverse=True)
        best = candidates[0]

        signal_found = False

        # ── Iterer barre par barre sur les barres de fin de journee du winner ──
        df_winner = data[best["ticker"]]
        late_bars = df_winner.between_time("15:45", "15:54")

        for ts, bar in late_bars.iterrows():
            if signal_found:
                break

            entry_price = bar["close"]
            if entry_price <= 0:
                continue

            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.tp_pct)

            signals.append(Signal(
                action="LONG",
                ticker=best["ticker"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "relative_perf_pct": round(best["relative_perf"] * 100, 3),
                    "etf_perf_pct": round(best["etf_perf"] * 100, 3),
                    "spy_perf_pct": round(spy_perf * 100, 3),
                    "weekday": weekday,
                    "entry_type": "overnight_sector_winner",
                },
            ))
            signal_found = True

        return signals
