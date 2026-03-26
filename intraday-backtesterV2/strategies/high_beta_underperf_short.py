"""
High-Beta Underperformance Short — SHORT ONLY

Edge : Quand le marche (SPY) baisse de > 0.5% le matin, les stocks high-beta
qui sont encore FLAT ou en hausse sont "en retard". Le beta implique qu'ils
devraient baisser plus que SPY — s'ils ne l'ont pas encore fait, c'est parce
que le selling n'a pas encore atteint ces noms.

La convergence vers la baisse du marche est quasi-mecanique :
- Les fonds quant rééquilibrent leur beta exposure
- Les market makers ajustent les prix des options
- Les retail traders paniquent en retard

On identifie le stock high-beta le plus en retard et on le short.

Regles :
- A 10:30 ET, SPY doit etre en baisse > 0.5%
- Parmi les high-beta (TSLA, COIN, MARA, AMD, NVDA), identifier ceux qui sont
  FLAT (move < 0.2%) ou en HAUSSE
- Shorter celui qui est le plus "en retard" (le plus haut vs son beta implicite)
- SL = high du jour + 0.3%. TP = 2x le risque ou EOD
- Si TOUS les high-beta sont deja en baisse proportionnelle = skip
- Max 1 trade/jour
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# Beta approximatifs historiques vs SPY
HIGH_BETA_STOCKS = {
    "MARA": 4.0,
    "COIN": 3.0,
    "TSLA": 2.0,
    "NVDA": 1.8,
    "AMD": 1.5,
}

SIGNAL_TIME = dt_time(10, 30)


class HighBetaUnderperfShortStrategy(BaseStrategy):
    name = "High-Beta Underperf Short"

    SPY_MIN_DROP = -0.003          # SPY doit etre down > 0.3% (assouplir = plus de signaux)
    FLAT_THRESHOLD = 0.005         # Stock considere "en retard" si move < 0.5% (assouplir)
    STOP_BUFFER_PCT = 0.005        # Stop = HOD + 0.5% (plus large pour volatilite high-beta)
    MAX_TRADES_PER_DAY = 1

    def get_required_tickers(self) -> list[str]:
        return ["SPY"] + list(HIGH_BETA_STOCKS.keys())

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        if "SPY" not in data:
            return []

        spy_df = data["SPY"]
        if len(spy_df) < 10:
            return []

        spy_open = spy_df.iloc[0]["open"]
        if spy_open <= 0:
            return []

        # Scanner les barres 10:00-12:00 pour trouver le regime SPY down
        spy_window = spy_df.between_time("10:00", "12:00")
        if spy_window.empty:
            return []

        candidates = []
        signal_found = False

        for check_idx in range(len(spy_window)):
            if signal_found:
                break

            check_ts = spy_window.index[check_idx]
            spy_bar = spy_window.iloc[check_idx]
            spy_perf = (spy_bar["close"] - spy_open) / spy_open

            # SPY doit etre en baisse significative
            if spy_perf > self.SPY_MIN_DROP:
                continue

            # Scanner les high-beta pour trouver ceux en retard
            for ticker, beta in HIGH_BETA_STOCKS.items():
                if ticker not in data:
                    continue

                df = data[ticker]
                if len(df) < 10:
                    continue

                stock_open = df.iloc[0]["open"]
                if stock_open <= 0:
                    continue

                stock_at_check = df[df.index <= check_ts]
                if stock_at_check.empty:
                    continue

                stock_bar = stock_at_check.iloc[-1]
                stock_perf = (stock_bar["close"] - stock_open) / stock_open

                expected_perf = spy_perf * beta
                lag = stock_perf - expected_perf

                if stock_perf > -self.FLAT_THRESHOLD:
                    day_bars = df[df.index.date == date]
                    if day_bars.empty:
                        continue
                    hod = day_bars.loc[:check_ts, "high"].max()

                    candidates.append({
                        "ticker": ticker,
                        "beta": beta,
                        "stock_perf": stock_perf,
                        "expected_perf": expected_perf,
                        "lag": lag,
                        "price": stock_bar["close"],
                        "hod": hod,
                        "timestamp": stock_at_check.index[-1],
                    })
                    signal_found = True

        if not candidates:
            return []

        # Prendre le stock le plus en retard (lag le plus eleve)
        candidates.sort(key=lambda c: c["lag"], reverse=True)
        best = candidates[0]

        entry_price = best["price"]
        stop_loss = best["hod"] * (1 + self.STOP_BUFFER_PCT)
        risk = stop_loss - entry_price
        if risk <= 0:
            return []

        # TP = 2x le risque
        take_profit = entry_price - (2 * risk)
        if take_profit <= 0:
            return []

        return [Signal(
            action="SHORT",
            ticker=best["ticker"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=best["timestamp"],
            metadata={
                "strategy": self.name,
                "spy_perf_pct": round(spy_perf * 100, 2),
                "stock_perf_pct": round(best["stock_perf"] * 100, 2),
                "expected_perf_pct": round(best["expected_perf"] * 100, 2),
                "lag_pct": round(best["lag"] * 100, 2),
                "beta": best["beta"],
                "hod": round(best["hod"], 2),
            },
        )]
