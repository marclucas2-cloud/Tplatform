"""
Strategie : Yield Curve Steepener -> Bank Alpha

EDGE : Quand TLT baisse (taux longs montent) et SHY est stable, la courbe
se pentifie. Les banques montent avec un retard de 30-60 min car les
desks equity reagissent plus lentement que les desks taux.

Regles :
- Signaux : TLT, SHY, IEF. Trades : banques
- Timing : 9:35-14:00 ET
- LONG banques : TLT return < -0.2% a 10:00, |SHY return| < 0.15%
- XLF return < 0.25% (les banques n'ont pas reagi)
- Stop : 0.6%, Target : 1.2%
- Skip si TLT et SHY bougent dans la meme direction > 0.3%
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


class YieldCurveBankStrategy(BaseStrategy):
    name = "Yield Curve -> Bank Alpha"

    SIGNAL_TICKERS = ["TLT", "SHY", "IEF"]
    BANK_TICKERS = ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "FITB", "KEY", "SCHW"]

    def __init__(
        self,
        tlt_min_drop_pct: float = -0.12,
        shy_max_move_pct: float = 0.15,
        xlf_max_follow_pct: float = 0.35,
        stop_pct: float = 0.006,
        target_pct: float = 0.012,
        check_time: tuple = (10, 0),
        max_entry_time: tuple = (14, 0),
    ):
        self.tlt_min_drop_pct = tlt_min_drop_pct
        self.shy_max_move_pct = shy_max_move_pct
        self.xlf_max_follow_pct = xlf_max_follow_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.check_time = dt_time(*check_time)
        self.max_entry_time = dt_time(*max_entry_time)

    def get_required_tickers(self) -> list[str]:
        return self.SIGNAL_TICKERS + self.BANK_TICKERS + ["XLF", "SPY"]

    def _rth(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter to RTH only."""
        return df.between_time("09:30", "16:00")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        if "TLT" not in data or "SHY" not in data:
            return signals

        tlt_df = self._rth(data["TLT"])
        shy_df = self._rth(data["SHY"])

        if len(tlt_df) < 5 or len(shy_df) < 5:
            return signals

        tlt_ret = self._return_at_time(tlt_df)
        shy_ret = self._return_at_time(shy_df)

        if tlt_ret is None or shy_ret is None:
            return signals

        # Steepening: TLT drops (long rates up), SHY stable -> LONG banks
        is_steepening = tlt_ret < self.tlt_min_drop_pct and abs(shy_ret) < self.shy_max_move_pct

        # Flattening: TLT rises (long rates down), SHY stable -> SHORT banks
        is_flattening = tlt_ret > abs(self.tlt_min_drop_pct) and abs(shy_ret) < self.shy_max_move_pct

        if not is_steepening and not is_flattening:
            return signals

        # Parallel shift filter
        if tlt_ret * shy_ret > 0 and abs(tlt_ret) > 0.3 and abs(shy_ret) > 0.3:
            return signals

        # XLF lag check
        if "XLF" in data:
            xlf_ret = self._return_at_time(self._rth(data["XLF"]))
            if xlf_ret is not None:
                if is_steepening and xlf_ret > self.xlf_max_follow_pct:
                    return signals
                if is_flattening and xlf_ret < -self.xlf_max_follow_pct:
                    return signals

        direction = "LONG" if is_steepening else "SHORT"

        # Scan bank stocks for the best lag
        candidates = []
        for ticker in self.BANK_TICKERS:
            if ticker not in data:
                continue

            bdf = self._rth(data[ticker])
            if len(bdf) < 5:
                continue

            stock_ret = self._return_at_time(bdf)
            if stock_ret is None:
                continue

            if direction == "LONG":
                lag = -stock_ret
                if stock_ret > self.xlf_max_follow_pct:
                    continue
            else:
                lag = stock_ret
                if stock_ret < -self.xlf_max_follow_pct:
                    continue

            bars_check = bdf[bdf.index.time <= self.check_time]
            if bars_check.empty:
                continue

            candidates.append({
                "ticker": ticker,
                "lag": lag,
                "stock_ret": stock_ret,
                "df": bdf,
                "entry_bar": bars_check.iloc[-1],
                "entry_ts": bars_check.index[-1],
            })

        if not candidates:
            return signals

        candidates.sort(key=lambda x: x["lag"], reverse=True)
        best = candidates[0]

        if best["entry_ts"].time() > self.max_entry_time:
            return signals

        entry_price = best["entry_bar"]["close"]

        if direction == "LONG":
            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.target_pct)
        else:
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

        signals.append(Signal(
            action=direction,
            ticker=best["ticker"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=best["entry_ts"],
            metadata={
                "strategy": self.name,
                "tlt_ret": round(tlt_ret, 3),
                "shy_ret": round(shy_ret, 3),
                "stock_ret": round(best["stock_ret"], 3),
                "lag": round(best["lag"], 3),
                "signal": "steepening" if direction == "LONG" else "flattening",
            },
        ))

        return signals

    def _return_at_time(self, df: pd.DataFrame) -> float | None:
        if df.empty:
            return None
        stock_open = df.iloc[0]["open"]
        bars_check = df[df.index.time <= self.check_time]
        if bars_check.empty or stock_open == 0:
            return None
        return ((bars_check.iloc[-1]["close"] - stock_open) / stock_open) * 100
