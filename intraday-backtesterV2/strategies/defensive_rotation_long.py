"""
Strategie : Defensive Rotation Long

Edge structurel :
Dans un marche baissier, les secteurs defensifs (utilities, staples, healthcare)
surperforment le marche. Les flux institutionnels "risk-off" poussent ces ETFs
a la hausse meme quand SPY baisse. On achete le defensif le plus fort quand il
surperforme SPY de > 0.3% ET est au-dessus de son VWAP.

Regles :
- Tickers : XLU (utilities), XLP (staples), XLV (healthcare)
- A partir de 10:00 : si le defensif surperforme SPY de > 0.3% ET est > VWAP → BUY
- Acheter le plus fort des 3 (meilleur surperformance)
- Stop : 0.5% sous l'entree
- Target : 1.0% au-dessus ou EOD
- Max 1 trade/jour
- Fenetre : 10:00-15:00
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap


# ── Parametres ──
DEFENSIVE_TICKERS = ["XLU", "XLP", "XLV"]
BENCHMARK = "SPY"
MIN_OUTPERFORMANCE_PCT = 0.003   # Doit surperformer SPY de > 0.3%
STOP_PCT = 0.005                  # Stop 0.5%
TARGET_PCT = 0.010                # Target 1.0%
MAX_TRADES_PER_DAY = 1
MIN_BARS = 20


class DefensiveRotationLongStrategy(BaseStrategy):
    name = "Defensive Rotation Long"

    def __init__(
        self,
        min_outperformance: float = MIN_OUTPERFORMANCE_PCT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
    ):
        self.min_outperformance = min_outperformance
        self.stop_pct = stop_pct
        self.target_pct = target_pct

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "XLU", "XLP", "XLV"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── SPY requis pour comparaison ──
        if BENCHMARK not in data:
            return signals

        df_spy = data[BENCHMARK]
        if len(df_spy) < MIN_BARS:
            return signals

        spy_open = df_spy.iloc[0]["open"]
        if spy_open <= 0:
            return signals

        # ── Calculer la performance SPY depuis l'open barre par barre ──
        spy_perf = df_spy["close"] / spy_open - 1.0

        # ── Preparer les defensifs ──
        defensive_data = {}
        for ticker in DEFENSIVE_TICKERS:
            if ticker not in data:
                continue
            df_def = data[ticker]
            if len(df_def) < MIN_BARS:
                continue

            def_open = df_def.iloc[0]["open"]
            if def_open <= 0:
                continue

            # VWAP du defensif
            df_vwap = vwap(df_def)

            # Performance depuis l'open
            def_perf = df_def["close"] / def_open - 1.0

            defensive_data[ticker] = {
                "df": df_def,
                "perf": def_perf,
                "vwap": df_vwap,
                "open": def_open,
            }

        if not defensive_data:
            return signals

        # ── Scanner barre par barre de 10:00 a 15:00 ──
        signal_found = False

        # Utiliser le premier defensif disponible pour definir la fenetre
        ref_ticker = list(defensive_data.keys())[0]
        ref_df = defensive_data[ref_ticker]["df"]
        tradeable_bars = ref_df.between_time("10:00", "15:00")

        for ts, _ in tradeable_bars.iterrows():
            if signal_found:
                break

            # ── Evaluer chaque defensif ──
            candidates = []

            # Performance SPY a ce timestamp
            if ts not in spy_perf.index:
                continue
            spy_perf_val = spy_perf.loc[ts]
            if pd.isna(spy_perf_val):
                continue

            for ticker, d in defensive_data.items():
                if ts not in d["perf"].index:
                    continue
                if ts not in d["vwap"].index:
                    continue

                def_perf_val = d["perf"].loc[ts]
                vwap_val = d["vwap"].loc[ts]
                price = d["df"].loc[ts, "close"] if ts in d["df"].index else np.nan

                if any(pd.isna(v) for v in [def_perf_val, vwap_val, price]):
                    continue
                if price <= 0 or vwap_val <= 0:
                    continue

                # ── Condition 1 : surperforme SPY de > 0.3% ──
                outperformance = def_perf_val - spy_perf_val
                if outperformance < self.min_outperformance:
                    continue

                # ── Condition 2 : prix au-dessus du VWAP ──
                if price <= vwap_val:
                    continue

                candidates.append({
                    "ticker": ticker,
                    "price": price,
                    "outperformance": outperformance,
                    "ts": ts,
                })

            if not candidates:
                continue

            # ── Acheter le plus fort (meilleur surperformance) ──
            candidates.sort(key=lambda c: c["outperformance"], reverse=True)
            best = candidates[0]

            entry_price = best["price"]
            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.target_pct)

            signals.append(Signal(
                action="LONG",
                ticker=best["ticker"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=best["ts"],
                metadata={
                    "strategy": self.name,
                    "outperformance_pct": round(best["outperformance"] * 100, 2),
                    "spy_perf_pct": round(spy_perf_val * 100, 2),
                    "defensive_ticker": best["ticker"],
                },
            ))
            signal_found = True

        return signals
