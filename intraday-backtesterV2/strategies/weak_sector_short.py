"""
Strategie : Weak Sector Short

Edge structurel :
Les rotations sectorielles creent des divergences entre secteurs.
Quand un secteur cyclique sous-performe SPY, les composants les plus faibles
de ce secteur sont vendus en premier par les institutions (flight to quality).
On short le composant le plus faible du secteur le plus faible.

Regles :
- Secteur ETFs : XLK, XLF, XLE, XLV, XLI (cycliques)
- A 10:30+ : trouver le secteur ETF sous-performant SPY de > 0.5%
- Dans ce secteur, trouver le composant sous-performant le secteur ETF de > 0.3%
- SHORT ce composant
- Stop : 1.0%, Target : 1.5%
- Max 1 trade/jour
- Timing : 10:30-15:00 ET
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# ── Parametres ──
SECTOR_UNDERPERF_THRESHOLD = -0.005  # Secteur ETF sous-performe SPY de > 0.5%
COMPONENT_UNDERPERF_THRESHOLD = -0.003  # Composant sous-performe ETF de > 0.3%
STOP_PCT = 0.010                     # Stop 1.0%
TARGET_PCT = 0.015                   # Target 1.5%
MIN_PRICE = 15.0

# Secteurs cycliques a surveiller
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI"]


class WeakSectorShortStrategy(BaseStrategy):
    name = "Weak Sector Short"

    def __init__(
        self,
        sector_underperf: float = SECTOR_UNDERPERF_THRESHOLD,
        component_underperf: float = COMPONENT_UNDERPERF_THRESHOLD,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        min_price: float = MIN_PRICE,
    ):
        self.sector_underperf = sector_underperf
        self.component_underperf = component_underperf
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.min_price = min_price

    def get_required_tickers(self) -> list[str]:
        from universe import SECTOR_MAP
        tickers = ["SPY"] + SECTOR_ETFS
        for etf in SECTOR_ETFS:
            if etf in SECTOR_MAP:
                tickers.extend(SECTOR_MAP[etf])
        return list(set(tickers))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        from universe import SECTOR_MAP

        signals = []

        # ── SPY doit etre present ──
        if "SPY" not in data:
            return signals

        df_spy = data["SPY"]
        if len(df_spy) < 20:
            return signals

        spy_open = df_spy.iloc[0]["open"]
        if spy_open <= 0:
            return signals

        # ── Iterer barres SPY 10:30-15:00 ──
        spy_tradeable = df_spy.between_time("10:30", "15:00")
        if spy_tradeable.empty:
            return signals

        signal_found = False

        for ts, spy_bar in spy_tradeable.iterrows():
            if signal_found:
                break

            spy_perf = (spy_bar["close"] - spy_open) / spy_open

            # ── Chercher le secteur le plus faible vs SPY ──
            weakest_sector = None
            weakest_sector_underperf = 0

            for etf in SECTOR_ETFS:
                if etf not in data:
                    continue

                df_etf = data[etf]
                if len(df_etf) < 10:
                    continue

                etf_open = df_etf.iloc[0]["open"]
                if etf_open <= 0:
                    continue

                # Barre du secteur au meme moment
                etf_bars_at_ts = df_etf[df_etf.index <= ts]
                if etf_bars_at_ts.empty:
                    continue
                etf_bar = etf_bars_at_ts.iloc[-1]
                etf_perf = (etf_bar["close"] - etf_open) / etf_open

                # Sous-performance relative vs SPY
                relative_perf = etf_perf - spy_perf

                if relative_perf < self.sector_underperf and relative_perf < weakest_sector_underperf:
                    weakest_sector = etf
                    weakest_sector_underperf = relative_perf

            if weakest_sector is None:
                continue

            # ── Dans le secteur faible, trouver le composant le plus faible ──
            if weakest_sector not in SECTOR_MAP:
                continue

            components = SECTOR_MAP[weakest_sector]

            # Performance du secteur ETF pour comparer
            df_sector = data[weakest_sector]
            sector_bars_at_ts = df_sector[df_sector.index <= ts]
            if sector_bars_at_ts.empty:
                continue
            sector_perf = (sector_bars_at_ts.iloc[-1]["close"] - df_sector.iloc[0]["open"]) / df_sector.iloc[0]["open"]

            weakest_component = None
            weakest_comp_underperf = 0
            weakest_comp_price = 0
            weakest_comp_ts = None

            for comp_ticker in components:
                if comp_ticker not in data:
                    continue

                df_comp = data[comp_ticker]
                if len(df_comp) < 10:
                    continue

                comp_open = df_comp.iloc[0]["open"]
                if comp_open < self.min_price:
                    continue

                # Barre du composant au meme moment
                comp_bars_at_ts = df_comp[df_comp.index <= ts]
                if comp_bars_at_ts.empty:
                    continue
                comp_bar = comp_bars_at_ts.iloc[-1]
                comp_perf = (comp_bar["close"] - comp_open) / comp_open

                # Sous-performance relative vs secteur ETF
                comp_relative = comp_perf - sector_perf

                if comp_relative < self.component_underperf and comp_relative < weakest_comp_underperf:
                    weakest_component = comp_ticker
                    weakest_comp_underperf = comp_relative
                    weakest_comp_price = comp_bar["close"]
                    weakest_comp_ts = comp_bars_at_ts.index[-1]

            if weakest_component is None:
                continue

            # ── Signal SHORT sur le composant le plus faible ──
            entry_price = weakest_comp_price
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action="SHORT",
                ticker=weakest_component,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=weakest_comp_ts,
                metadata={
                    "strategy": self.name,
                    "weak_sector": weakest_sector,
                    "sector_vs_spy": round(weakest_sector_underperf * 100, 2),
                    "comp_vs_sector": round(weakest_comp_underperf * 100, 2),
                    "spy_perf": round(spy_perf * 100, 2),
                },
            ))
            signal_found = True

        return signals
