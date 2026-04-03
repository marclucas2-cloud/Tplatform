"""
FUT-005 : Micro Gold (MGC) Trend Following

Edge:
Gold momentum time-series is well documented in academic literature.
Gold acts as a crisis hedge (negative correlation with equities) and
benefits from DXY weakness. Trend-following on gold captures persistent
moves driven by macro flows (inflation expectations, central bank
buying, risk-off sentiment). DXY confirmation filter improves signal
quality by aligning gold trend with dollar weakness/strength.

Signal:
  - LONG: EMA(10) > EMA(30) AND gold > EMA(10) AND DXY weakening (DXY < EMA20)
  - SHORT: EMA(10) < EMA(30) AND gold < EMA(10) AND DXY strengthening (DXY > EMA20)
  - Timeframe: Daily bars

Risk:
  - Stop: 2.5 ATR(14) daily
  - Take profit: 4 ATR(14) (1.6:1 R/R)
  - Sizing: 1-2 contracts per signal
  - Holding: 5-20 days (swing)

Instrument:
  - MGC (Micro Gold), multiplier 10, ~$1,000 margin
  - Proxy: GLD ETF for backtesting (tracks gold closely)
  - DXY proxy: UUP ETF (Invesco DB US Dollar Index Bullish Fund)

Costs:
  - Commission: $1.25/contract RT (CME micro)
  - Slippage: ~$0.10/oz = ~$1/contract

Filters:
  - No trade during FOMC days (gold volatility spike = random)
  - Min bars requirement for indicator warmup

Walk-forward expectations:
  - Target Sharpe OOS: 0.8-1.8
  - Min 50% OOS windows profitable
  - ~40-60 trades/year
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


# ─── Constants ────────────────────────────────────────────────────────────────

# Proxy tickers for backtesting
GOLD_TICKER = "GLD"        # Gold proxy (MGC not available on Alpaca)
DXY_TICKER = "UUP"         # Dollar index proxy

# EMA periods (daily)
EMA_FAST = 10
EMA_SLOW = 30
DXY_EMA = 20

# Risk
ATR_PERIOD = 14
STOP_ATR_MULT = 2.5        # Stop = 2.5 ATR
TP_ATR_MULT = 4.0          # TP = 4.0 ATR (1.6:1 R/R)

# Instrument specs
MGC_MULTIPLIER = 10         # $10 per 0.1 gold point
MGC_MARGIN = 1000.0         # ~$1,000 margin per contract
MGC_COMMISSION_RT = 1.25    # $1.25 round-trip per contract

# FOMC dates 2026 — skip trading on these days
FOMC_DATES = {
    "2026-01-28", "2026-01-29",
    "2026-03-17", "2026-03-18",
    "2026-05-05", "2026-05-06",
    "2026-06-16", "2026-06-17",
    "2026-07-28", "2026-07-29",
    "2026-09-15", "2026-09-16",
    "2026-10-27", "2026-10-28",
    "2026-12-15", "2026-12-16",
}


class FuturesMGCTrendStrategy(BaseStrategy):
    """
    Micro Gold (MGC) Trend Following — EMA crossover + DXY confirmation.

    Goes long gold when EMA(10) > EMA(30), price > EMA(10), and
    the US dollar is weakening (DXY < EMA20). Reverses for shorts.
    FOMC days are filtered out to avoid random volatility spikes.

    Uses GLD as proxy for gold price and UUP as proxy for DXY.
    """

    name = "FUT-005 MGC Trend"

    def __init__(
        self,
        ema_fast: int = EMA_FAST,
        ema_slow: int = EMA_SLOW,
        dxy_ema: int = DXY_EMA,
        atr_period: int = ATR_PERIOD,
        stop_atr_mult: float = STOP_ATR_MULT,
        tp_atr_mult: float = TP_ATR_MULT,
        max_trades_per_day: int = 1,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.dxy_ema = dxy_ema
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return [GOLD_TICKER, DXY_TICKER]

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Compute Average True Range on daily bars."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, min_periods=period).mean()

    @staticmethod
    def _is_fomc_day(date) -> bool:
        """Check if date is an FOMC meeting day."""
        date_str = str(date)[:10]
        return date_str in FOMC_DATES

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate trend-following signals for gold (MGC proxy via GLD).

        Requires both GLD and UUP data. DXY confirmation via UUP:
        - DXY weakening (UUP < EMA20) confirms gold LONG
        - DXY strengthening (UUP > EMA20) confirms gold SHORT

        Args:
            data: {ticker: DataFrame} with OHLCV columns
            date: current trading date

        Returns:
            list[Signal] — at most max_trades_per_day signals
        """
        if GOLD_TICKER not in data:
            return []

        # ── FOMC filter ──
        if self._is_fomc_day(date):
            return []

        df_gold = data[GOLD_TICKER]

        # Need enough bars for EMA slow + warmup
        min_bars = self.ema_slow + self.atr_period + 5
        if len(df_gold) < min_bars:
            return []

        df_gold = df_gold.copy()

        # ── Compute gold indicators ──
        df_gold["ema_fast"] = df_gold["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df_gold["ema_slow"] = df_gold["close"].ewm(span=self.ema_slow, adjust=False).mean()
        df_gold["atr"] = self._compute_atr(df_gold, self.atr_period)

        # ── Compute DXY (UUP) indicators if available ──
        dxy_weakening = None
        if DXY_TICKER in data:
            df_dxy = data[DXY_TICKER]
            if len(df_dxy) >= self.dxy_ema + 5:
                df_dxy = df_dxy.copy()
                df_dxy["ema_dxy"] = df_dxy["close"].ewm(span=self.dxy_ema, adjust=False).mean()

                # Align indices
                common_idx = df_gold.index.intersection(df_dxy.index)
                if len(common_idx) > 0:
                    last_common = common_idx[-1]
                    if last_common in df_dxy.index:
                        dxy_price = df_dxy.loc[last_common, "close"]
                        dxy_ema_val = df_dxy.loc[last_common, "ema_dxy"]
                        if pd.notna(dxy_price) and pd.notna(dxy_ema_val):
                            dxy_weakening = dxy_price < dxy_ema_val

        signals = []

        for ts, bar in df_gold.iterrows():
            if len(signals) >= self.max_trades_per_day:
                break

            # Skip NaN rows (warmup)
            if pd.isna(bar["ema_fast"]) or pd.isna(bar["ema_slow"]) or pd.isna(bar["atr"]):
                continue

            atr_val = bar["atr"]
            if atr_val <= 0:
                continue

            price = bar["close"]
            ema_f = bar["ema_fast"]
            ema_s = bar["ema_slow"]

            stop_distance = self.stop_atr_mult * atr_val
            tp_distance = self.tp_atr_mult * atr_val

            # ── Build DXY context for metadata ──
            gold_dxy_corr = None
            regime = "unknown"

            # ── LONG signal: EMA fast > EMA slow, price > EMA fast, DXY weakening ──
            if ema_f > ema_s and price > ema_f:
                # DXY confirmation: skip if DXY strengthening (not weakening)
                if dxy_weakening is not None and not dxy_weakening:
                    continue

                regime = "gold_bull_dxy_weak"
                entry_price = price
                stop_loss = entry_price - stop_distance
                take_profit = entry_price + tp_distance

                signals.append(Signal(
                    action="LONG",
                    ticker=GOLD_TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "instrument": "MGC (Micro Gold)",
                        "proxy": GOLD_TICKER,
                        "ema_fast": round(ema_f, 2),
                        "ema_slow": round(ema_s, 2),
                        "atr": round(atr_val, 4),
                        "dxy_weakening": dxy_weakening,
                        "regime": regime,
                        "multiplier": MGC_MULTIPLIER,
                        "margin_per_contract": MGC_MARGIN,
                        "commission_rt": MGC_COMMISSION_RT,
                        "cost_rt_pct": round(MGC_COMMISSION_RT / (price * MGC_MULTIPLIER) * 100, 4) if price > 0 else 0,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 2),
                        "expected_holding_days": "5-20",
                    },
                ))

            # ── SHORT signal: EMA fast < EMA slow, price < EMA fast, DXY strengthening ──
            elif ema_f < ema_s and price < ema_f:
                # DXY confirmation: skip if DXY weakening (not strengthening)
                if dxy_weakening is not None and dxy_weakening:
                    continue

                regime = "gold_bear_dxy_strong"
                entry_price = price
                stop_loss = entry_price + stop_distance
                take_profit = entry_price - tp_distance

                signals.append(Signal(
                    action="SHORT",
                    ticker=GOLD_TICKER,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "instrument": "MGC (Micro Gold)",
                        "proxy": GOLD_TICKER,
                        "ema_fast": round(ema_f, 2),
                        "ema_slow": round(ema_s, 2),
                        "atr": round(atr_val, 4),
                        "dxy_weakening": dxy_weakening,
                        "regime": regime,
                        "multiplier": MGC_MULTIPLIER,
                        "margin_per_contract": MGC_MARGIN,
                        "commission_rt": MGC_COMMISSION_RT,
                        "cost_rt_pct": round(MGC_COMMISSION_RT / (price * MGC_MULTIPLIER) * 100, 4) if price > 0 else 0,
                        "rr_ratio": round(self.tp_atr_mult / self.stop_atr_mult, 2),
                        "expected_holding_days": "5-20",
                    },
                ))

        return signals[:self.max_trades_per_day]
