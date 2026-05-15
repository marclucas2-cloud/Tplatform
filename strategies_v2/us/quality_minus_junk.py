"""Quality-Minus-Junk sector-neutral V1 for US single-stock research.

This is a research strategy layered on top of
`core.backtest.us_equity_event_driven.EventDrivenBacktester`.

Point-in-time caveat:
Free fundamentals are not true PIT. If EDGAR filing dates are available, the
strategy uses them as `available_date`. Otherwise it applies a conservative
proxy: quarterly facts become visible only after the 90-day lag boundary, and
annual facts after the 120-day lag boundary. This prevents using restated
yfinance-style fundamentals at dates when they were not knowable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from core.backtest.us_equity_event_driven import SignalContext, SignalIntent


REQUIRED_COLUMNS = {
    "period_end",
    "total_revenue",
    "cost_of_revenue",
    "total_assets",
    "total_debt",
    "total_stockholder_equity",
    "eps",
}


@dataclass(frozen=True)
class QMJConfig:
    gross_notional: float = 20_000.0
    long_notional: float = 10_000.0
    short_notional: float = 10_000.0
    min_quarters: int = 8
    quintile: float = 0.20
    hold_days: int = 21
    quarterly_lag_days: int = 90
    annual_lag_days: int = 120
    profitability_weight: float = 1.0 / 3.0
    safety_weight: float = 1.0 / 3.0
    stability_weight: float = 1.0 / 3.0


class QualityMinusJunkStrategy:
    """Monthly sector-neutral Quality-Minus-Junk long/short portfolio."""

    name = "us_qmj_v1"
    broker = "alpaca"
    asset_class = "equity"

    def __init__(
        self,
        fundamentals: Mapping[str, pd.DataFrame],
        sector_map: Mapping[str, str],
        config: QMJConfig | None = None,
        membership_source: str = "current_sp500_snapshot",
        pit_method: str = "filing_dates_or_lag_proxy",
    ):
        self.config = config or QMJConfig()
        self.sector_map = dict(sector_map)
        self.membership_source = membership_source
        self.pit_method = pit_method
        self.fundamentals = {
            symbol: self._prepare_fundamentals(symbol, df)
            for symbol, df in fundamentals.items()
            if symbol in self.sector_map
        }
        self._metric_history = {
            symbol: self._build_metric_history(symbol, df)
            for symbol, df in self.fundamentals.items()
        }

    def signal_function(self, ctx: SignalContext) -> Iterable[SignalIntent]:
        """Return trade intents only on first trading day of a new month."""
        if not self.is_rebalance_day(ctx.as_of, ctx.prior_date):
            return []
        targets = self.build_target_portfolio(ctx.as_of)
        return [
            SignalIntent(
                symbol=row.symbol,
                side=row.side,
                weight=float(row.notional) / self.config.gross_notional,
                hold_days=self.config.hold_days,
                reason=f"qmj sector={row.sector} score={row.composite_score:.4f}",
            )
            for row in targets.itertuples(index=False)
        ]

    @staticmethod
    def is_rebalance_day(as_of: pd.Timestamp, prior_date: pd.Timestamp) -> bool:
        as_of = pd.Timestamp(as_of)
        prior_date = pd.Timestamp(prior_date)
        return (as_of.year, as_of.month) != (prior_date.year, prior_date.month)

    def build_target_portfolio(self, as_of: str | pd.Timestamp) -> pd.DataFrame:
        scores = self.compute_scores(as_of)
        if scores.empty:
            return self._empty_targets()

        total_eligible = len(scores)
        rows: list[dict] = []
        for sector, group in scores.groupby("sector", sort=True):
            group = group.sort_values("composite_score", ascending=False)
            if len(group) < 2:
                continue
            n_leg = max(1, int(np.floor(len(group) * self.config.quintile)))
            n_leg = min(n_leg, len(group) // 2)
            if n_leg <= 0:
                continue

            sector_weight = len(group) / total_eligible
            long_per_name = self.config.long_notional * sector_weight / n_leg
            short_per_name = self.config.short_notional * sector_weight / n_leg
            longs = group.head(n_leg)
            shorts = group.tail(n_leg)

            for _, row in longs.iterrows():
                rows.append(self._target_row(row, "BUY", long_per_name))
            for _, row in shorts.iterrows():
                rows.append(self._target_row(row, "SELL", short_per_name))

        if not rows:
            return self._empty_targets()
        return pd.DataFrame(rows).sort_values(["sector", "side", "symbol"]).reset_index(drop=True)

    def compute_scores(self, as_of: str | pd.Timestamp) -> pd.DataFrame:
        raw = self.compute_raw_metrics(as_of)
        if raw.empty:
            return raw

        scored = raw.copy()
        scored["profitability_z"] = self._sector_zscore(scored, "profitability")
        scored["safety_z"] = -self._sector_zscore(scored, "debt_to_equity")
        scored["stability_z"] = -self._sector_zscore(scored, "eps_volatility")

        cfg = self.config
        weight_sum = cfg.profitability_weight + cfg.safety_weight + cfg.stability_weight
        scored["composite_score"] = (
            cfg.profitability_weight * scored["profitability_z"]
            + cfg.safety_weight * scored["safety_z"]
            + cfg.stability_weight * scored["stability_z"]
        ) / weight_sum
        return scored.sort_values("composite_score", ascending=False).reset_index(drop=True)

    def compute_raw_metrics(self, as_of: str | pd.Timestamp) -> pd.DataFrame:
        as_of_ts = pd.Timestamp(as_of).normalize()
        rows = []
        for symbol, history in self._metric_history.items():
            if history.empty:
                continue
            effective_dates = history["effective_date"].to_numpy(dtype="datetime64[ns]")
            idx = int(np.searchsorted(effective_dates, np.datetime64(as_of_ts), side="right") - 1)
            if idx >= 0:
                rows.append(history.iloc[idx].drop(labels=["effective_date"]).to_dict())
        return pd.DataFrame(rows)

    def metadata(self) -> dict:
        return {
            "strategy_id": self.name,
            "membership_source": self.membership_source,
            "pit_method": self.pit_method,
            "fundamental_lag": {
                "quarterly_days": self.config.quarterly_lag_days,
                "annual_days": self.config.annual_lag_days,
                "visibility_rule": "available_date < as_of_date",
                "note": "PIT proxy via filing lag or EDGAR filed date; not a true commercial PIT database.",
            },
            "score_weights": {
                "profitability": self.config.profitability_weight,
                "safety": self.config.safety_weight,
                "stability": self.config.stability_weight,
            },
        }

    def _prepare_fundamentals(self, symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
        df = frame.copy()
        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"{symbol}: missing QMJ fundamental columns {sorted(missing)}")

        df["period_end"] = pd.to_datetime(df["period_end"]).dt.normalize()
        if "available_date" in df.columns:
            df["available_date"] = pd.to_datetime(df["available_date"]).dt.normalize()
        elif "filed_date" in df.columns:
            df["available_date"] = pd.to_datetime(df["filed_date"]).dt.normalize()
        else:
            period_type = df.get("period_type", "Q")
            if not isinstance(period_type, pd.Series):
                period_type = pd.Series([period_type] * len(df), index=df.index)
            lag_days = np.where(
                period_type.astype(str).str.upper().str.startswith("A"),
                self.config.annual_lag_days,
                self.config.quarterly_lag_days,
            )
            df["available_date"] = df["period_end"] + pd.to_timedelta(lag_days, unit="D")

        df["gross_profit"] = pd.to_numeric(df["total_revenue"], errors="coerce") - pd.to_numeric(
            df["cost_of_revenue"], errors="coerce"
        )
        numeric_cols = [
            "gross_profit",
            "total_assets",
            "total_debt",
            "total_stockholder_equity",
            "eps",
        ]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values(["available_date", "period_end"]).reset_index(drop=True)

    def _build_metric_history(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for available_date in sorted(df["available_date"].dropna().unique()):
            as_of = pd.Timestamp(available_date).normalize() + pd.Timedelta(days=1)
            known = self._known_fundamentals(df, as_of)
            metric = self._metrics_from_known(symbol, known)
            if metric is not None:
                metric["effective_date"] = as_of
                rows.append(metric)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).drop_duplicates("effective_date", keep="last").sort_values("effective_date").reset_index(drop=True)

    def _metrics_from_known(self, symbol: str, known: pd.DataFrame) -> dict | None:
        if len(known) < self.config.min_quarters:
            return None

        gross_profit = self._latest_series(known, "gross_profit").tail(4)
        assets = self._latest_series(known, "total_assets").tail(1)
        debt = self._latest_series(known, "total_debt").tail(1)
        equity = self._latest_series(known, "total_stockholder_equity").tail(1)
        eps = self._latest_series(known, "eps").tail(8)

        if len(gross_profit) < 4 or len(eps) < self.config.min_quarters:
            return None
        if assets.empty or equity.empty or debt.empty:
            return None

        total_assets = float(assets.iloc[-1])
        stockholder_equity = float(equity.iloc[-1])
        if not np.isfinite(total_assets) or total_assets <= 0:
            return None
        if not np.isfinite(stockholder_equity) or stockholder_equity <= 0:
            return None

        debt_to_equity = float(debt.iloc[-1]) / stockholder_equity
        eps_vol = float(eps.std(ddof=0))
        if not np.isfinite(debt_to_equity) or not np.isfinite(eps_vol):
            return None

        return {
            "symbol": symbol,
            "sector": self.sector_map[symbol],
            "profitability": float(gross_profit.sum()) / total_assets,
            "debt_to_equity": debt_to_equity,
            "eps_volatility": eps_vol,
            "n_quarters": int(len(known)),
            "latest_period_end": known["period_end"].max(),
            "latest_available_date": known["available_date"].max(),
        }

    @staticmethod
    def _known_fundamentals(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
        known = df.loc[df["available_date"] < as_of].copy()
        if known.empty:
            return known
        return (
            known.sort_values(["period_end", "available_date"])
            .groupby("period_end", as_index=False)
            .tail(1)
            .sort_values("period_end")
            .reset_index(drop=True)
        )

    @staticmethod
    def _latest_series(df: pd.DataFrame, column: str) -> pd.Series:
        return df.dropna(subset=[column]).sort_values("period_end")[column]

    @staticmethod
    def _sector_zscore(df: pd.DataFrame, column: str) -> pd.Series:
        def _z(group: pd.Series) -> pd.Series:
            std = group.std(ddof=0)
            if not np.isfinite(std) or std == 0:
                return pd.Series(0.0, index=group.index)
            return (group - group.mean()) / std

        return df.groupby("sector", group_keys=False)[column].apply(_z)

    @staticmethod
    def _target_row(row: pd.Series, side: str, notional: float) -> dict:
        return {
            "symbol": row["symbol"],
            "sector": row["sector"],
            "side": side,
            "notional": float(notional),
            "weight": float(notional),
            "composite_score": float(row["composite_score"]),
            "profitability_z": float(row["profitability_z"]),
            "safety_z": float(row["safety_z"]),
            "stability_z": float(row["stability_z"]),
        }

    @staticmethod
    def _empty_targets() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "symbol",
                "sector",
                "side",
                "notional",
                "weight",
                "composite_score",
                "profitability_z",
                "safety_z",
                "stability_z",
            ]
        )
