"""Macro top-1 ETF rotation — long-only monthly.

Research 2026-04-24 (target_alpha_us_sectors_and_new_assets mission).

Edge: rotation mensuelle vers le top-1 ETF macro sur 60d momentum. Pas
d'alpha spectaculaire isole, mais decorrele du desk futures+crypto et
long-only (zero short borrow complexity).

Universe (8 ETFs macro cross-asset, tous Alpaca US / tres liquides):
  SPY (equity US)     TLT (long bonds)    GLD (gold)       DBC (commodities)
  UUP (USD)           IEF (medium bonds)  HYG (high yield) QQQ (tech)

Backtest 2018-2026 (ChatGPT config LB=60 HD=21):
  - Sharpe 0.676, DD -5.94%, WF 4/5 (ratio 0.80)
  - 97 rebalances sur 2088 jours = ~12/an
  - Long-only, 1 ETF detenu a la fois

Sensitivity grid (12 configs LB={30,60,90,120} x HD={10,21,42}):
  - 9/12 Sharpe > 0.5
  - 9/12 WF ratio >= 0.80
  - Config retenue: LB=60, HD=21 (proche academiques, cadence raisonnable)

Rules:
  At each rebalance day (hold_days cooldown depuis dernier rebal):
    1. Compute 60d cumulative return pour chaque ETF
    2. Pick top-1 (meilleur momentum)
    3. Si different de current position -> flip (close old, open new)

  Paper: simulation locale (pas d'ordres broker). Journal JSONL dedie
  log chaque cycle (signal_emit / hold / exit / rebalance_date).

Pas de stop-loss (rotation monthly), pas de TP. Exit = prochain rebal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


UNIVERSE = ["SPY", "TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]
DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_HOLD_DAYS = 21


@dataclass
class Top1Decision:
    """Outcome of one cycle: signal + metadata for journal."""
    action: str                # "rebalance" | "hold" | "no_signal"
    target_symbol: str | None
    previous_symbol: str | None
    lookback_returns: dict[str, float]
    top3: list[tuple[str, float]]
    rebalance_due: bool
    reason: str


class MacroTop1Rotation:
    """Long-only top-1 momentum rotation across 8 macro ETFs."""

    UNIVERSE = UNIVERSE

    def __init__(
        self,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        hold_days: int = DEFAULT_HOLD_DAYS,
    ) -> None:
        self.lookback_days = lookback_days
        self.hold_days = hold_days

    @property
    def name(self) -> str:
        return "macro_top1_rotation"

    @property
    def asset_class(self) -> str:
        return "etf_us"

    @property
    def broker(self) -> str:
        return "alpaca"

    def compute_cumret(self, prices: pd.DataFrame) -> pd.Series:
        """Return: cumulative return on the last lookback_days for each ETF.
        prices: DataFrame index=date, cols=symbols, values=close.
        Returns Series indexed by symbol.
        """
        if prices.empty or len(prices) <= self.lookback_days:
            return pd.Series(dtype=float)
        window = prices.tail(self.lookback_days + 1)
        rets = window.pct_change().dropna()
        cum = (1.0 + rets).prod() - 1.0
        return cum.dropna()

    def should_rebalance(
        self,
        now_date: pd.Timestamp,
        last_rebal_date: pd.Timestamp | None,
    ) -> bool:
        """Rebalance if no prior rebal OR >= hold_days business days since last."""
        if last_rebal_date is None:
            return True
        bdays = np.busday_count(
            last_rebal_date.date(), now_date.date(),
        )
        return int(bdays) >= self.hold_days

    def decide(
        self,
        prices: pd.DataFrame,
        now_date: pd.Timestamp,
        last_rebal_date: pd.Timestamp | None,
        current_symbol: str | None,
    ) -> Top1Decision:
        """Main decision function. Called by runner at each cycle.

        Returns a Top1Decision — the runner takes care of journaling & state.
        """
        missing = [s for s in self.UNIVERSE if s not in prices.columns]
        if missing:
            return Top1Decision(
                action="no_signal",
                target_symbol=None,
                previous_symbol=current_symbol,
                lookback_returns={},
                top3=[],
                rebalance_due=False,
                reason=f"missing_symbols:{','.join(missing)}",
            )

        due = self.should_rebalance(now_date, last_rebal_date)
        if not due:
            return Top1Decision(
                action="hold",
                target_symbol=current_symbol,
                previous_symbol=current_symbol,
                lookback_returns={},
                top3=[],
                rebalance_due=False,
                reason=(
                    f"within_hold_window_{self.hold_days}bdays_since_"
                    f"{last_rebal_date.date() if last_rebal_date else 'start'}"
                ),
            )

        cum = self.compute_cumret(prices[self.UNIVERSE])
        if cum.empty or cum.isna().all():
            return Top1Decision(
                action="no_signal",
                target_symbol=None,
                previous_symbol=current_symbol,
                lookback_returns={},
                top3=[],
                rebalance_due=True,
                reason="insufficient_history_for_lookback",
            )

        ranks = cum.sort_values(ascending=False)
        top_sym = str(ranks.index[0])
        top3 = [(str(s), float(r)) for s, r in ranks.head(3).items()]

        return Top1Decision(
            action="rebalance",
            target_symbol=top_sym,
            previous_symbol=current_symbol,
            lookback_returns={str(k): float(v) for k, v in cum.items()},
            top3=top3,
            rebalance_due=True,
            reason=(
                "rebalance_fire"
                if top_sym != current_symbol
                else "rebalance_confirms_current"
            ),
        )

    def get_parameters(self) -> dict[str, Any]:
        return {
            "lookback_days": self.lookback_days,
            "hold_days": self.hold_days,
            "universe": self.UNIVERSE,
        }
