"""
Strategie : Semi Earnings Chain Reaction

EDGE : La supply chain semi est sequentielle. Quand un leader (NVDA, AMD)
gap > 1.5% (proxy earnings/news), les followers (SMCI, AMAT, MRVL) rattrapent
en 2-4h avec un retard significatif.

Regles :
- Leaders : NVDA, AMD, AVGO, TSM, ASML
- Followers : SMCI, AMAT, LRCX, KLAC, MRVL, MU, DELL
- Entree : Leader gap > 1.5%, follower gap < 1%
- Follower commence a bouger dans la direction du leader
- Stop : 1.0%, Target : 2.0%
- Skip si le leader fade > 60% de son gap dans la 1ere heure
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


class SemiEarningsChainStrategy(BaseStrategy):
    name = "Semi Earnings Chain Reaction"

    LEADERS = ["NVDA", "AMD", "AVGO", "TSM", "ASML"]
    FOLLOWERS = ["SMCI", "AMAT", "LRCX", "KLAC", "MRVL", "MU", "DELL"]

    def __init__(
        self,
        leader_min_gap_pct: float = 1.0,
        follower_max_gap_pct: float = 1.5,
        leader_fade_threshold: float = 0.60,
        stop_pct: float = 0.008,
        target_pct: float = 0.015,
        breakout_lookback: int = 2,
        check_time: tuple = (10, 30),
    ):
        self.leader_min_gap_pct = leader_min_gap_pct
        self.follower_max_gap_pct = follower_max_gap_pct
        self.leader_fade_threshold = leader_fade_threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.breakout_lookback = breakout_lookback
        self.check_time = dt_time(*check_time)
        self._prev_closes: dict[str, float] = {}

    def get_required_tickers(self) -> list[str]:
        return self.LEADERS + self.FOLLOWERS + ["SPY"]

    def _rth(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter to RTH only."""
        return df.between_time("09:30", "16:00")

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # Compute gaps for all tickers using RTH opens
        gaps = {}
        rth_data = {}
        for ticker in self.LEADERS + self.FOLLOWERS:
            if ticker not in data:
                continue
            rth = self._rth(data[ticker])
            if rth.empty:
                continue
            rth_data[ticker] = rth

            today_open = rth.iloc[0]["open"]

            if ticker in self._prev_closes:
                prev_close = self._prev_closes[ticker]
                if prev_close > 0:
                    gaps[ticker] = ((today_open - prev_close) / prev_close) * 100
            # Update prev close from RTH close
            self._prev_closes[ticker] = rth.iloc[-1]["close"]

        # Find leaders with big gaps
        active_leaders = []
        for leader in self.LEADERS:
            if leader not in gaps:
                continue
            if abs(gaps[leader]) >= self.leader_min_gap_pct:
                active_leaders.append((leader, gaps[leader]))

        if not active_leaders:
            return signals

        # Pick the leader with the biggest gap
        active_leaders.sort(key=lambda x: abs(x[1]), reverse=True)
        leader_ticker, leader_gap = active_leaders[0]
        leader_direction = "LONG" if leader_gap > 0 else "SHORT"

        # Check if leader fades in the first hour
        if leader_ticker in rth_data:
            leader_rth = rth_data[leader_ticker]
            first_hour = leader_rth[leader_rth.index.time <= dt_time(10, 30)]
            if len(first_hour) >= 2:
                leader_open = leader_rth.iloc[0]["open"]
                leader_current = first_hour.iloc[-1]["close"]
                prev_c = self._prev_closes.get(leader_ticker, leader_open)
                leader_gap_dollars = leader_open - prev_c
                if leader_gap_dollars != 0:
                    fade_pct = (leader_open - leader_current) / leader_gap_dollars
                    if fade_pct > self.leader_fade_threshold:
                        return signals

        # Scan followers for chain reaction
        candidates = []
        for follower in self.FOLLOWERS:
            if follower not in rth_data or follower not in gaps:
                continue

            follower_gap = gaps[follower]
            if abs(follower_gap) > self.follower_max_gap_pct:
                continue

            fdf = rth_data[follower]
            if len(fdf) < 5:
                continue

            bars_to_check = fdf[fdf.index.time <= self.check_time]
            if len(bars_to_check) < self.breakout_lookback + 1:
                continue

            current_close = bars_to_check.iloc[-1]["close"]
            lookback_bars = bars_to_check.iloc[-(self.breakout_lookback + 1):-1]

            if leader_direction == "LONG":
                prev_high = lookback_bars["high"].max()
                if current_close <= prev_high:
                    continue
            else:
                prev_low = lookback_bars["low"].min()
                if current_close >= prev_low:
                    continue

            follower_ret = ((current_close - fdf.iloc[0]["open"]) / fdf.iloc[0]["open"]) * 100
            lag = abs(leader_gap) - abs(follower_ret)

            candidates.append({
                "ticker": follower,
                "lag": lag,
                "follower_gap": follower_gap,
                "follower_ret": follower_ret,
                "entry_bar": bars_to_check.iloc[-1],
                "entry_ts": bars_to_check.index[-1],
            })

        if not candidates:
            return signals

        candidates.sort(key=lambda x: x["lag"], reverse=True)
        best = candidates[0]

        entry_price = best["entry_bar"]["close"]

        if leader_direction == "LONG":
            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.target_pct)
        else:
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

        signals.append(Signal(
            action=leader_direction,
            ticker=best["ticker"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=best["entry_ts"],
            metadata={
                "strategy": self.name,
                "leader": leader_ticker,
                "leader_gap": round(leader_gap, 2),
                "follower_gap": round(best["follower_gap"], 2),
                "lag": round(best["lag"], 2),
            },
        ))

        return signals
