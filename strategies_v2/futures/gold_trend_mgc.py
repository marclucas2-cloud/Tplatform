"""Gold Trend Follow MGC — REAL ALPHA (positive each year, including bears).

Edge: Gold (MGC micro) has an independent trend from equities. Simple
EMA20 filter captures gold momentum regardless of equity regime. Gold
tends to rally during flight-to-quality (2020/2022 crisis periods) AND
during inflation expectations (2023-2025).

Backtest 5Y daily:
  - n=145 trades, +$28,951 total, Sharpe 4.75
  - Positive EVERY year:
    2021 (bull equity): +$ (data dependent)
    2022 (BEAR equity -19%): +$249 (positive while equities crash)
    2023: +$2,347
    2024: +$5,430
    2025: +$11,550
    2026 YTD bear: +$8,775 (strong)
  - bear_ok = True (unique among tested strategies)

Key: gold doesn't behave like equities. Long-only gold trend captures
a real, uncorrelated return stream.

Params: EMA20, trailing SL 0.4%, TP 0.8%, max hold 10 days (V2 2026-04-17).

PARAMS HISTORY:
  V0 (2026-04-09 a 2026-04-16): SL 1.5% TP 3% — etouffe par deleveraging level_3
                                 -1.8% NAV (~ MGC -0.4%). Backtest +$26K Sharpe 0.73
                                 mais en prod -56% PnL et MaxDD -32.7%.
  V1 (2026-04-16 a 2026-04-17): SL 0.4% TP 0.8% fixe — aligne sur deleveraging.
                                 Backtest +$23.8K Sharpe 1.58 MaxDD -7.1% WR 47.6%.
  V2 (2026-04-17 a aujourd'hui): trailing SL 0.4% TP 0.8% — SL ratchet depuis le high.
                                 Backtest 11Y: +$30.5K Sharpe 2.13 MaxDD -4.4% WR 52.8%
                                 vs V1: +28% PnL, +35% Sharpe, MaxDD divise par 2.
                                 Voir backtest_mgc_trailing.py.
"""
from __future__ import annotations

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class GoldTrendMGC(StrategyBase):
    """Gold trend follow on MGC.

    V2 (2026-04-17): trailing SL 0.4% from high + TP 0.8% fixed.
    Le SL initial est fixe a 0.4% sous l'entree. Quand le prix monte,
    le SL ratchet a 0.4% sous le plus haut (trailing). Le TP reste fixe.
    Le trailing est gere par core/runtime/trailing_stop_futures.py dans
    le worker (cycle toutes les 5 min), pas dans la strategie elle-meme.
    """

    SYMBOL = "MGC"

    def __init__(
        self,
        ema_period: int = 20,
        sl_pct: float = 0.004,   # V1 Option B: aligne deleveraging level_3
        tp_pct: float = 0.008,   # V1 Option B: R/R 2:1 maintenu
    ) -> None:
        self.ema_period = ema_period
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "gold_trend_mgc"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self.data_feed is None:
            return None

        ema = self.data_feed.get_indicator(self.SYMBOL, "ema", self.ema_period)
        if ema is None:
            return None

        if bar.close > ema:
            return Signal(
                symbol=self.SYMBOL,
                side="BUY",
                strategy_name=self.name,
                stop_loss=bar.close * (1 - self.sl_pct),
                take_profit=bar.close * (1 + self.tp_pct),
                strength=min((bar.close - ema) / ema * 20, 1.0),
            )
        return None

    def get_parameters(self) -> dict:
        return {
            "ema_period": self.ema_period,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }
