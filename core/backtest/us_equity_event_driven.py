"""
Event-driven US single-stock backtester for Phase B research.

This module is deliberately isolated from live runtime code. It models daily
US equity long/short research execution with these invariants:

* signals are computed from history ending at close[t-1]
* orders are executed at open[t]
* Alpaca paper-like costs are charged explicitly
* short borrow and locate failures are approximated because free data does not
  contain borrow/locate availability

Known data limitations are surfaced in BacktestOutput.metrics["metadata"] so
walk-forward manifests can copy them verbatim later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


DATA_LIMITATIONS = [
    "Universe backtest = current S&P 500 / Russell 1000 members; survivorship bias is active.",
    "No delisted equities are present; bankruptcies and delisted losers are missing.",
    "Earnings timestamps from yfinance/free sources can be imprecise; BMO/AMC noise is expected.",
    "Pre-2018 coverage is degraded for some symbols; yfinance backfills prices and corporate actions.",
    "Borrow cost and locate availability are unavailable in yfinance; fixed proxies are used.",
    "Free adjusted-price data can still contain dividend/corporate-action restatement errors.",
]


REGIME_WINDOWS = {
    "bull": [
        ("2017", "2017-01-01", "2017-12-31", "OK"),
        ("2019", "2019-01-01", "2019-12-31", "OK"),
        ("2021", "2021-01-01", "2021-12-31", "OK"),
        ("2023_2024", "2023-01-01", "2024-12-31", "OK"),
    ],
    "bear": [
        ("2018Q4", "2018-10-01", "2018-12-31", "OK"),
        ("2020Q1", "2020-01-01", "2020-03-31", "OK"),
        ("2022", "2022-01-01", "2022-12-31", "OK"),
    ],
    "sideways": [
        ("2011", "2011-01-01", "2011-12-31", "DEGRADED"),
        ("2015_2016", "2015-01-01", "2016-12-31", "DEGRADED"),
    ],
}


@dataclass(frozen=True)
class CostsConfig:
    """Free-data approximation of US equity L/S research costs."""

    commission_usd: float = 0.0
    slippage_pct: float = 0.0002
    borrow_rate_annual: float = 0.015
    locate_fail_rate: float = 0.03
    trading_days_per_year: int = 252


@dataclass(frozen=True)
class SignalIntent:
    """One desired trade generated from history ending at close[t-1]."""

    symbol: str
    side: str
    weight: float
    hold_days: int = 5
    reason: str = ""


@dataclass(frozen=True)
class SignalContext:
    """Context passed to strategy functions.

    `history` excludes the execution date. On date t, the latest row available
    to the strategy is t-1, so close[t] cannot leak into same-day entry logic.
    """

    as_of: pd.Timestamp
    prior_date: pd.Timestamp
    history: Mapping[str, pd.DataFrame]
    earnings_history: Mapping[str, pd.DataFrame]
    metadata: Mapping[str, object]


@dataclass
class OpenPosition:
    symbol: str
    side: str
    qty: int
    entry_date: pd.Timestamp
    entry_price: float
    entry_notional: float
    entry_index: int
    hold_days: int
    slippage_entry: float
    reason: str = ""
    dividend_cash: float = 0.0
    borrow_cost: float = 0.0
    reg_sho_warning: bool = False


@dataclass
class BacktestOutput:
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: dict
    events: pd.DataFrame = field(default_factory=pd.DataFrame)


class EventDrivenBacktester:
    """Daily event-driven backtester for US single-stock long/short research."""

    def __init__(
        self,
        universe: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        capital: float,
        costs_config: CostsConfig | Mapping[str, float] | None = None,
        seed: int = 42,
        universe_membership_source: str = "current",
    ):
        if capital <= 0:
            raise ValueError("capital must be positive")
        self.universe = list(universe)
        self.start = pd.Timestamp(start).normalize()
        self.end = pd.Timestamp(end).normalize()
        self.capital = float(capital)
        self.costs = self._coerce_costs(costs_config)
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.universe_membership_source = universe_membership_source
        self.metadata = self._build_metadata()

    def run(
        self,
        price_data: Mapping[str, pd.DataFrame],
        signal_func: Callable[[SignalContext], Iterable[SignalIntent | Mapping[str, object]]],
        earnings_data: Mapping[str, pd.DataFrame] | None = None,
    ) -> BacktestOutput:
        prepared = self._prepare_price_data(price_data)
        earnings = self._prepare_earnings_data(earnings_data or {})
        calendar = self._build_calendar(prepared)

        if len(calendar) < 2:
            raise ValueError("at least two trading dates are required")

        positions: list[OpenPosition] = []
        trades: list[dict] = []
        events: list[dict] = []
        equity_rows: list[dict] = []
        realized_net_pnl = 0.0

        for i in range(1, len(calendar)):
            current_date = calendar[i]
            prior_date = calendar[i - 1]

            realized_today = 0.0
            positions, closed = self._exit_due_positions(
                positions=positions,
                prepared=prepared,
                date=current_date,
                date_index=i,
                reason="max_hold",
            )
            for trade in closed:
                trades.append(trade)
                realized_today += trade["net_pnl"]
            realized_net_pnl += realized_today

            self._apply_dividends_and_borrow(positions, prepared, current_date, events)

            context = SignalContext(
                as_of=current_date,
                prior_date=prior_date,
                history=self._history_views(prepared, i),
                earnings_history=self._earnings_views(earnings, prior_date),
                metadata=self.metadata,
            )
            intents = [self._coerce_signal(intent) for intent in signal_func(context)]
            for intent in intents:
                if self._has_open_position(positions, intent.symbol, intent.side):
                    continue
                opened = self._try_open_position(intent, prepared, current_date, i, events)
                if opened is not None:
                    positions.append(opened)

            equity_rows.append(
                {
                    "date": current_date,
                    "equity": self.capital + realized_net_pnl + self._open_unrealized_pnl(positions, prepared, current_date),
                    "realized_net_pnl": realized_net_pnl,
                    "open_positions": len(positions),
                }
            )

        if positions:
            last_date = calendar[-1]
            positions, closed = self._exit_due_positions(
                positions=positions,
                prepared=prepared,
                date=last_date,
                date_index=len(calendar) - 1,
                reason="end_of_data",
                force=True,
            )
            for trade in closed:
                trades.append(trade)

        trades_df = pd.DataFrame(trades)
        equity_df = pd.DataFrame(equity_rows).set_index("date") if equity_rows else pd.DataFrame()
        events_df = pd.DataFrame(events)
        metrics = self._compute_metrics(trades_df, equity_df)
        return BacktestOutput(trades=trades_df, equity_curve=equity_df, metrics=metrics, events=events_df)

    def _build_metadata(self) -> dict:
        survivorship_bias_active = self.universe_membership_source.lower() in {"current", "sp500_current", "r1000_current"}
        warnings = []
        if survivorship_bias_active:
            warnings.append("survivorship bias active")
        return {
            "data_limitations": list(DATA_LIMITATIONS),
            "survivorship_bias_active": survivorship_bias_active,
            "warnings": warnings,
            "borrow_cost_methodology": (
                "Fixed 1.5% annualized short-notional proxy. IBKR documents that daily short "
                "sale cost depends on stock borrow fee rates and collateral; free yfinance data "
                "does not provide those rates, so this is intentionally conservative/noisy."
            ),
            "locate_fail_rate": self.costs.locate_fail_rate,
            "seed": self.seed,
            "engine": "us_equity_event_driven_phase_b",
        }

    @staticmethod
    def _coerce_costs(costs_config: CostsConfig | Mapping[str, float] | None) -> CostsConfig:
        if costs_config is None:
            return CostsConfig()
        if isinstance(costs_config, CostsConfig):
            return costs_config
        return CostsConfig(**dict(costs_config))

    def _prepare_price_data(self, price_data: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        missing = sorted(set(self.universe) - set(price_data))
        if missing:
            raise ValueError(f"missing price data for: {missing}")

        prepared: dict[str, pd.DataFrame] = {}
        for symbol in self.universe:
            df = price_data[symbol].copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                raise ValueError(f"{symbol}: index must be a DatetimeIndex")
            df.index = pd.to_datetime(df.index).normalize()
            df = df.sort_index().loc[self.start : self.end]
            required = {"open", "high", "low", "close"}
            absent = required - set(df.columns)
            if absent:
                raise ValueError(f"{symbol}: missing required columns {sorted(absent)}")
            for col in ["open", "high", "low", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            if "dividend" not in df.columns:
                df["dividend"] = 0.0
            if "split_factor" not in df.columns:
                df["split_factor"] = 1.0
            df["dividend"] = pd.to_numeric(df["dividend"], errors="coerce").fillna(0.0)
            df["split_factor"] = pd.to_numeric(df["split_factor"], errors="coerce").fillna(1.0)
            prepared[symbol] = df.dropna(subset=["open", "close"])
        return prepared

    def _prepare_earnings_data(self, earnings_data: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        prepared: dict[str, pd.DataFrame] = {}
        for symbol, df in earnings_data.items():
            event_df = df.copy()
            if not isinstance(event_df.index, pd.DatetimeIndex):
                if "date" not in event_df.columns:
                    raise ValueError(f"{symbol}: earnings data requires DatetimeIndex or date column")
                event_df.index = pd.to_datetime(event_df["date"])
            event_df.index = pd.to_datetime(event_df.index).normalize()
            prepared[symbol] = event_df.sort_index()
        return prepared

    @staticmethod
    def _build_calendar(prepared: Mapping[str, pd.DataFrame]) -> pd.DatetimeIndex:
        dates = pd.DatetimeIndex([])
        for df in prepared.values():
            dates = dates.union(df.index)
        return dates.sort_values()

    @staticmethod
    def _history_views(prepared: Mapping[str, pd.DataFrame], stop_index: int) -> dict[str, pd.DataFrame]:
        return {symbol: df.iloc[:stop_index].copy() for symbol, df in prepared.items()}

    @staticmethod
    def _earnings_views(earnings: Mapping[str, pd.DataFrame], prior_date: pd.Timestamp) -> dict[str, pd.DataFrame]:
        return {symbol: df.loc[df.index <= prior_date].copy() for symbol, df in earnings.items()}

    @staticmethod
    def _coerce_signal(intent: SignalIntent | Mapping[str, object]) -> SignalIntent:
        if isinstance(intent, SignalIntent):
            signal = intent
        else:
            signal = SignalIntent(
                symbol=str(intent["symbol"]),
                side=str(intent["side"]),
                weight=float(intent.get("weight", 0.0)),
                hold_days=int(intent.get("hold_days", 5)),
                reason=str(intent.get("reason", "")),
            )
        side = signal.side.upper()
        if side not in {"BUY", "SELL", "LONG", "SHORT"}:
            raise ValueError(f"unsupported side: {signal.side}")
        normalized = "BUY" if side in {"BUY", "LONG"} else "SELL"
        if signal.weight <= 0:
            raise ValueError("signal weight must be positive")
        if signal.hold_days <= 0:
            raise ValueError("hold_days must be positive")
        return SignalIntent(signal.symbol, normalized, signal.weight, signal.hold_days, signal.reason)

    @staticmethod
    def _has_open_position(positions: Sequence[OpenPosition], symbol: str, side: str) -> bool:
        return any(pos.symbol == symbol and pos.side == side for pos in positions)

    def _try_open_position(
        self,
        intent: SignalIntent,
        prepared: Mapping[str, pd.DataFrame],
        date: pd.Timestamp,
        date_index: int,
        events: list[dict],
    ) -> OpenPosition | None:
        if intent.symbol not in prepared or date not in prepared[intent.symbol].index:
            return None
        open_price = float(prepared[intent.symbol].loc[date, "open"])
        if not np.isfinite(open_price) or open_price <= 0:
            return None

        if intent.side == "SELL" and self.rng.random() < self.costs.locate_fail_rate:
            events.append(
                {
                    "date": date,
                    "symbol": intent.symbol,
                    "event": "locate_failed",
                    "side": intent.side,
                    "reason": intent.reason,
                }
            )
            return None

        target_notional = self.capital * intent.weight
        qty = int(np.floor(target_notional / open_price))
        if qty <= 0:
            events.append(
                {
                    "date": date,
                    "symbol": intent.symbol,
                    "event": "too_small",
                    "side": intent.side,
                    "reason": intent.reason,
                }
            )
            return None

        entry_notional = qty * open_price
        return OpenPosition(
            symbol=intent.symbol,
            side=intent.side,
            qty=qty,
            entry_date=date,
            entry_price=open_price,
            entry_notional=entry_notional,
            entry_index=date_index,
            hold_days=intent.hold_days,
            slippage_entry=entry_notional * self.costs.slippage_pct + self.costs.commission_usd,
            reason=intent.reason,
        )

    def _exit_due_positions(
        self,
        positions: Sequence[OpenPosition],
        prepared: Mapping[str, pd.DataFrame],
        date: pd.Timestamp,
        date_index: int,
        reason: str,
        force: bool = False,
    ) -> tuple[list[OpenPosition], list[dict]]:
        remaining: list[OpenPosition] = []
        closed: list[dict] = []
        for pos in positions:
            hold_days = max(0, date_index - pos.entry_index)
            due = force or hold_days >= pos.hold_days
            if not due or date not in prepared[pos.symbol].index:
                remaining.append(pos)
                continue
            exit_price = float(prepared[pos.symbol].loc[date, "open"])
            exit_notional = pos.qty * exit_price
            slippage_exit = exit_notional * self.costs.slippage_pct + self.costs.commission_usd
            price_pnl = (exit_price - pos.entry_price) * pos.qty
            if pos.side == "SELL":
                price_pnl *= -1
            gross_pnl = price_pnl + pos.dividend_cash
            costs = pos.slippage_entry + slippage_exit + pos.borrow_cost
            net_pnl = gross_pnl - costs
            closed.append(
                {
                    "entry_date": pos.entry_date,
                    "exit_date": date,
                    "symbol": pos.symbol,
                    "side": "long" if pos.side == "BUY" else "short",
                    "qty": pos.qty,
                    "entry_price": pos.entry_price,
                    "exit_price": exit_price,
                    "entry_notional": pos.entry_notional,
                    "exit_notional": exit_notional,
                    "price_pnl": price_pnl,
                    "dividend_cash": pos.dividend_cash,
                    "gross_pnl": gross_pnl,
                    "slippage_cost": pos.slippage_entry + slippage_exit,
                    "borrow_cost": pos.borrow_cost,
                    "costs": costs,
                    "net_pnl": net_pnl,
                    "hold_days": hold_days,
                    "exit_reason": reason,
                    "reg_sho_warning": pos.reg_sho_warning,
                    "reason": pos.reason,
                }
            )
        return remaining, closed

    def _apply_dividends_and_borrow(
        self,
        positions: Sequence[OpenPosition],
        prepared: Mapping[str, pd.DataFrame],
        date: pd.Timestamp,
        events: list[dict],
    ) -> None:
        for pos in positions:
            if date not in prepared[pos.symbol].index:
                continue
            row = prepared[pos.symbol].loc[date]
            dividend = float(row.get("dividend", 0.0) or 0.0)
            if dividend:
                dividend_cash = dividend * pos.qty
                if pos.side == "SELL":
                    dividend_cash *= -1
                pos.dividend_cash += dividend_cash
                events.append(
                    {
                        "date": date,
                        "symbol": pos.symbol,
                        "event": "dividend",
                        "side": pos.side,
                        "cash": dividend_cash,
                    }
                )
            if pos.side == "SELL":
                short_notional = pos.qty * float(row["close"])
                pos.borrow_cost += short_notional * self.costs.borrow_rate_annual / self.costs.trading_days_per_year
                hold_calendar_days = (date - pos.entry_date).days
                if hold_calendar_days > 13 and not pos.reg_sho_warning:
                    pos.reg_sho_warning = True
                    events.append(
                        {
                            "date": date,
                            "symbol": pos.symbol,
                            "event": "reg_sho_closeout_warning",
                            "side": pos.side,
                            "hold_calendar_days": hold_calendar_days,
                        }
                    )

    @staticmethod
    def _open_unrealized_pnl(
        positions: Sequence[OpenPosition],
        prepared: Mapping[str, pd.DataFrame],
        date: pd.Timestamp,
    ) -> float:
        total = 0.0
        for pos in positions:
            if date not in prepared[pos.symbol].index:
                continue
            close_price = float(prepared[pos.symbol].loc[date, "close"])
            price_pnl = (close_price - pos.entry_price) * pos.qty
            if pos.side == "SELL":
                price_pnl *= -1
            total += price_pnl + pos.dividend_cash - pos.borrow_cost - pos.slippage_entry
        return total

    def _compute_metrics(self, trades: pd.DataFrame, equity: pd.DataFrame) -> dict:
        if equity.empty:
            returns = pd.Series(dtype=float)
            max_drawdown = 0.0
        else:
            returns = equity["equity"].pct_change().dropna()
            max_drawdown = self._max_drawdown(equity["equity"])

        gross_abs = float(trades["gross_pnl"].abs().sum()) if not trades.empty else 0.0
        costs = float(trades["costs"].sum()) if not trades.empty else 0.0
        borrow = float(trades["borrow_cost"].sum()) if not trades.empty else 0.0
        wins = trades.loc[trades["net_pnl"] > 0, "net_pnl"] if not trades.empty else pd.Series(dtype=float)
        losses = trades.loc[trades["net_pnl"] < 0, "net_pnl"] if not trades.empty else pd.Series(dtype=float)
        profit_factor = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else (float("inf") if wins.sum() > 0 else 0.0)

        return {
            "sharpe_net": self._annualized_sharpe(returns),
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "hit_rate": float((trades["net_pnl"] > 0).mean()) if not trades.empty else 0.0,
            "n_trades": int(len(trades)),
            "avg_hold": float(trades["hold_days"].mean()) if not trades.empty else 0.0,
            "total_cost_pct": float(costs / gross_abs) if gross_abs else 0.0,
            "ratio_borrow_cost_to_gross": float(borrow / gross_abs) if gross_abs else 0.0,
            "total_net_pnl": float(trades["net_pnl"].sum()) if not trades.empty else 0.0,
            "regime_breakdown": self._regime_breakdown(equity),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def _regime_breakdown(cls, equity: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for regime, windows in REGIME_WINDOWS.items():
            for label, start, end, quality in windows:
                if equity.empty:
                    sub = pd.DataFrame()
                else:
                    sub = equity.loc[(equity.index >= pd.Timestamp(start)) & (equity.index <= pd.Timestamp(end))]
                returns = sub["equity"].pct_change().dropna() if not sub.empty else pd.Series(dtype=float)
                rows.append(
                    {
                        "regime": regime,
                        "window": label,
                        "start": start,
                        "end": end,
                        "data_quality": quality,
                        "observations": int(len(sub)),
                        "sharpe": cls._annualized_sharpe(returns),
                        "profit_factor": cls._daily_profit_factor(sub),
                        "max_drawdown": cls._max_drawdown(sub["equity"]) if not sub.empty else 0.0,
                    }
                )
        return pd.DataFrame(rows)

    @staticmethod
    def _annualized_sharpe(returns: pd.Series) -> float:
        if returns.empty:
            return 0.0
        std = returns.std(ddof=1)
        if not np.isfinite(std) or std == 0:
            return 0.0
        return float(np.sqrt(252) * returns.mean() / std)

    @staticmethod
    def _daily_profit_factor(equity: pd.DataFrame) -> float:
        if equity.empty or "equity" not in equity:
            return 0.0
        pnl = equity["equity"].diff().dropna()
        gains = pnl[pnl > 0].sum()
        losses = pnl[pnl < 0].sum()
        if abs(losses) == 0:
            return float("inf") if gains > 0 else 0.0
        return float(gains / abs(losses))

    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        if equity.empty:
            return 0.0
        running_max = equity.cummax()
        drawdown = equity / running_max - 1.0
        return float(drawdown.min())
