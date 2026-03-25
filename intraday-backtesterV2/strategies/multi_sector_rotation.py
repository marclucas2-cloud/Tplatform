"""
Strategie : Multi-Sector Rotation

EDGE : Les flux institutionnels sectoriels prennent 2-4h. En detectant
la rotation a 10:30, on surfe le flow le reste de la journee. On va
LONG le stock qui surperforme le plus dans le top secteur et SHORT
le stock qui sous-performe le plus dans le bottom secteur.

Regles :
- 10 Sector ETFs comme signaux
- A 10:30, calculer le return de chaque ETF depuis 9:30
- LONG : stock avec la plus forte surperformance dans le TOP secteur
- SHORT : stock avec la pire sous-performance dans le BOTTOM secteur
- Stop : 0.6%, Target : 1.2%
- Skip si tous les secteurs vont dans la meme direction (pas de rotation)
- Skip si spread leader-laggard < 0.5%
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# Mapping sector ETF -> top components
SECTOR_COMPONENTS = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ADBE", "ORCL", "INTC", "QCOM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "USB"],
    "XLE": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "DVN"],
    "XLV": ["UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY"],
    "XLC": ["META", "GOOGL", "NFLX", "DIS", "CMCSA", "T", "VZ", "EA"],
    "XLI": ["CAT", "GE", "HON", "UNP", "RTX", "DE", "BA", "LMT", "UPS"],
    "XLP": ["PG", "KO", "PEP", "COST", "WMT", "PM", "CL", "MO"],
    "XLU": ["NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC"],
    "XLRE": ["PLD", "AMT", "CCI", "EQIX", "PSA", "SPG", "O"],
    "XLB": ["LIN", "APD", "ECL", "SHW", "FCX", "NEM", "NUE"],
}

SECTOR_ETFS = list(SECTOR_COMPONENTS.keys())


class MultiSectorRotationStrategy(BaseStrategy):
    name = "Multi-Sector Rotation"

    def __init__(
        self,
        min_sector_return_pct: float = 0.3,
        min_spread_pct: float = 0.7,
        stop_pct: float = 0.007,
        target_pct: float = 0.012,
        check_time: tuple = (10, 30),
    ):
        self.min_sector_return_pct = min_sector_return_pct
        self.min_spread_pct = min_spread_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.check_time = dt_time(*check_time)

    def get_required_tickers(self) -> list[str]:
        tickers = list(SECTOR_ETFS) + ["SPY"]
        for components in SECTOR_COMPONENTS.values():
            tickers.extend(components)
        return list(set(tickers))

    def _rth(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.between_time("09:30", "16:00")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # Compute return for each sector ETF at check_time
        sector_returns = {}
        for etf in SECTOR_ETFS:
            if etf not in data:
                continue
            ret = self._return_at_time(self._rth(data[etf]))
            if ret is not None:
                sector_returns[etf] = ret

        if len(sector_returns) < 4:
            return signals

        # Check if all sectors move in the same direction
        returns_list = list(sector_returns.values())
        all_positive = all(r > self.min_sector_return_pct for r in returns_list)
        all_negative = all(r < -self.min_sector_return_pct for r in returns_list)
        if all_positive or all_negative:
            return signals

        # Sort sectors by return
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1])
        bottom_etf, bottom_ret = sorted_sectors[0]
        top_etf, top_ret = sorted_sectors[-1]

        spread = top_ret - bottom_ret
        if spread < self.min_spread_pct:
            return signals

        # LONG: best outperformer in top sector
        long_signal = self._find_best_stock(data, top_etf, top_ret, direction="LONG")
        if long_signal:
            signals.append(long_signal)

        # SHORT: worst underperformer in bottom sector
        short_signal = self._find_best_stock(data, bottom_etf, bottom_ret, direction="SHORT")
        if short_signal:
            signals.append(short_signal)

        return signals

    def _find_best_stock(
        self, data: dict, sector_etf: str, sector_ret: float, direction: str
    ) -> Signal | None:
        components = SECTOR_COMPONENTS.get(sector_etf, [])
        if not components:
            return None

        candidates = []
        for ticker in components:
            if ticker not in data:
                continue

            df = self._rth(data[ticker])
            if len(df) < 5:
                continue

            stock_ret = self._return_at_time(df)
            if stock_ret is None:
                continue

            alpha = stock_ret - sector_ret

            bars_check = df[df.index.time <= self.check_time]
            if bars_check.empty:
                continue

            # VWAP check
            if "vwap" in df.columns and df["vwap"].notna().any():
                current_price = bars_check.iloc[-1]["close"]
                current_vwap = bars_check.iloc[-1].get("vwap", current_price)
                if current_vwap and not pd.isna(current_vwap):
                    if direction == "LONG" and current_price < current_vwap:
                        continue
                    if direction == "SHORT" and current_price > current_vwap:
                        continue

            candidates.append({
                "ticker": ticker,
                "alpha": alpha,
                "stock_ret": stock_ret,
                "df": df,
                "entry_bar": bars_check.iloc[-1],
                "entry_ts": bars_check.index[-1],
            })

        if not candidates:
            return None

        if direction == "LONG":
            candidates.sort(key=lambda x: x["alpha"], reverse=True)
        else:
            candidates.sort(key=lambda x: x["alpha"])

        best = candidates[0]
        entry_price = best["entry_bar"]["close"]

        if direction == "LONG":
            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.target_pct)
        else:
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

        return Signal(
            action=direction,
            ticker=best["ticker"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=best["entry_ts"],
            metadata={
                "strategy": self.name,
                "sector_etf": sector_etf,
                "sector_ret": round(sector_ret, 3),
                "stock_ret": round(best["stock_ret"], 3),
                "alpha": round(best["alpha"], 3),
                "direction": direction,
            },
        )

    def _return_at_time(self, df: pd.DataFrame) -> float | None:
        if df.empty:
            return None
        stock_open = df.iloc[0]["open"]
        bars_check = df[df.index.time <= self.check_time]
        if bars_check.empty or stock_open == 0:
            return None
        return ((bars_check.iloc[-1]["close"] - stock_open) / stock_open) * 100
