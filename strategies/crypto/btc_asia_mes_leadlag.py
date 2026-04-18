"""BTC Asia session MES lead-lag — paper-only strategy (T3-A2 validated).

Source de validation:
  - scripts/research/backtest_t3a_mes_btc_leadlag.py (2024-04 -> 2026-03, 489 days)
  - docs/research/wf_reports/T3A-02_mes_btc_asia_leadlag.md
  - docs/research/wf_reports/INT-B_discovery_batch.md
    -> Sharpe +1.07, MaxDD -7.7%, WF 4/5 OOS PASS, MC P(DD>30%) 0% -> VALIDATED

Thesis:
  Late US equity futures tone (MES 15:00-21:59 UTC) propagates into the
  following BTC Asia session (00:00-07:59 UTC next day). Edge is fragile
  without threshold and volatility filters.

Logic (preserves backtest):
  1. Daily features from hourly data:
     - mes_sig[day] = sum of MES 1H returns during 15:00-21:59 UTC of `day`
     - mes_vol[day] = std of all MES 1H returns of `day`
     - btc_asia_ret[day] = BTC last_close / first_open - 1 in 00:00-07:59 UTC of `day`
  2. Trade a day D using features from D-1 (shift(1)):
     - pos_thr = quantile_70(|mes_sig|, rolling_window)
     - vol_thr = quantile_80(mes_vol, rolling_window)
     - signal = +1 if mes_sig[D-1] >= +pos_thr and mes_vol[D-1] <= vol_thr
     - signal = -1 if mes_sig[D-1] <= -pos_thr and mes_vol[D-1] <= vol_thr
     - else signal = 0
  3. Execution (backtest): BTC spot/perp entry at 00:00 UTC open, exit at 07:59
     UTC close. Notional = $10,000 USDT. Cost 0.10% round trip.

Runtime approach (paper_only):
  - Log-only retrospective cycle at 08:15 UTC weekday
  - Computes signal for the Asia session that just closed (yesterday UTC)
  - Simulates entry at yesterday 00:00 UTC open / exit at yesterday 07:59 UTC close
  - Journals to data/state/btc_asia_mes_leadlag/paper_journal.jsonl
  - ZERO real orders. 30j minimum d'observation avant decision live.

Caveats runtime:
  - Short sells : le backtest "q70_v80" utilise mode=both (long+short). Binance
    France ne supporte pas facilement le short crypto. Pour le live, la variante
    long_only q80_v80 (Sharpe +1.08, WF 4/5) pourrait etre preferable.
  - Data freshness : MES_1H_YF2Y + BTCUSDT_1h doivent etre fresh (<3j sinon
    skip). Observe 2026-04-18: BTCUSDT_1h parquet stale ~20j, fix ops requis.
  - Rolling quantile: le backtest utilise quantile sur TOUTE l'historique
    (forward-looking en realite). Le runtime utilise une rolling window
    (default 365j) strictement anterieure. Petit impact, attendu.

Status: paper_only, log-only retrospective.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


MES_SESSION_HOURS = (15, 16, 17, 18, 19, 20, 21)  # UTC
BTC_ASIA_HOURS = tuple(range(0, 8))  # 00:00-07:59 UTC
DEFAULT_NOTIONAL_USD = 10_000.0
DEFAULT_COST_RT_PCT = 0.0010  # 10 bps round trip (Binance spot)
DEFAULT_SIGNAL_QUANTILE = 0.70
DEFAULT_VOL_QUANTILE = 0.80
DEFAULT_ROLLING_WINDOW = 365
MAX_DATA_AGE_DAYS = 3


@dataclass(frozen=True)
class LeadlagSignal:
    """Signal computed for a given target Asia session (by UTC date)."""
    target_date: pd.Timestamp     # Asia session date (UTC calendar)
    side: Literal["BUY", "SELL", "NONE"]
    mes_sig: float                # signed return sum 15-21 UTC of D-1
    mes_vol: float                # vol of D-1
    signal_thr: float             # |mes_sig| threshold = rolling quantile
    vol_thr: float                # rolling vol quantile
    rolling_window_used: int      # N days used for quantile


@dataclass(frozen=True)
class PaperTrade:
    """Simulated trade result for journal."""
    target_date: pd.Timestamp
    side: Literal["BUY", "SELL", "NONE"]
    entry_price: float
    exit_price: float
    notional_usd: float
    gross_ret: float              # (exit/entry - 1) * side_sign
    cost_pct: float
    pnl_usd: float
    mes_sig: float
    mes_vol: float


def build_daily_dataset(
    mes_hourly: pd.DataFrame,
    btc_hourly: pd.DataFrame,
) -> pd.DataFrame:
    """Build daily features dataframe.

    Args:
        mes_hourly: DataFrame with DatetimeIndex (naive or UTC-aware) and at
                    least 'close' column. Naive index assumed to be UTC.
        btc_hourly: DataFrame with 'timestamp' column (UTC-aware) and
                    'open', 'close' columns.

    Returns:
        DataFrame indexed by UTC date (normalized, naive), columns:
            - mes_sig: shift(1) sum MES 15-21 UTC returns
            - mes_vol: shift(1) std MES all-day returns
            - btc_asia_ret: BTC last_close/first_open - 1 on 00-08 UTC of date
    """
    # MES: ensure UTC-aware then extract hourly returns
    mes = mes_hourly.copy().reset_index()
    mes = mes.rename(columns={mes.columns[0]: "timestamp"})
    mes["timestamp"] = pd.to_datetime(mes["timestamp"])
    if mes["timestamp"].dt.tz is None:
        mes["timestamp"] = mes["timestamp"].dt.tz_localize("UTC")
    else:
        mes["timestamp"] = mes["timestamp"].dt.tz_convert("UTC")
    mes = mes.sort_values("timestamp")
    mes["date"] = mes["timestamp"].dt.floor("D").dt.tz_localize(None)
    mes["ret_bar"] = mes["close"].pct_change()

    mes_sig = (
        mes[mes["timestamp"].dt.hour.isin(MES_SESSION_HOURS)]
        .groupby("date")["ret_bar"]
        .sum()
        .rename("mes_sig")
    )
    mes_vol = mes.groupby("date")["ret_bar"].std().rename("mes_vol")

    # BTC
    btc = btc_hourly.copy()
    btc["timestamp"] = pd.to_datetime(btc["timestamp"], utc=True)
    btc = btc.sort_values("timestamp")
    btc["date"] = btc["timestamp"].dt.floor("D").dt.tz_localize(None)
    asia = btc[btc["timestamp"].dt.hour.isin(BTC_ASIA_HOURS)].copy()
    first_open = asia.groupby("date")["open"].first()
    last_close = asia.groupby("date")["close"].last()
    asia_ret = (last_close / first_open - 1.0).rename("btc_asia_ret")

    # Shift mes features to align "D signal" with "D-1 mes"
    daily = pd.concat(
        [mes_sig.shift(1), mes_vol.shift(1), asia_ret, first_open.rename("btc_entry_price"), last_close.rename("btc_exit_price")],
        axis=1,
    ).dropna()
    return daily


def compute_signal_for_date(
    daily: pd.DataFrame,
    target_date: pd.Timestamp,
    signal_quantile: float = DEFAULT_SIGNAL_QUANTILE,
    vol_quantile: float = DEFAULT_VOL_QUANTILE,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
    mode: Literal["both", "long_only", "short_only"] = "both",
) -> LeadlagSignal | None:
    """Compute the signal for a given target Asia session date.

    Uses rolling quantiles STRICTLY from data BEFORE target_date (no lookahead).
    Returns None if insufficient history or target_date not in daily.

    Args:
        daily: output of build_daily_dataset()
        target_date: Asia session date (UTC, normalized, naive)
        signal_quantile: quantile for |mes_sig| threshold
        vol_quantile: quantile for mes_vol threshold
        rolling_window: days of history used for rolling quantiles
        mode: "both" = long+short, "long_only", "short_only"
    """
    target_date = pd.Timestamp(target_date).normalize()
    if target_date not in daily.index:
        return None
    # History strictly before target_date
    hist = daily.loc[daily.index < target_date]
    if len(hist) < rolling_window:
        return None
    recent = hist.iloc[-rolling_window:]
    pos_thr = float(recent["mes_sig"].abs().quantile(signal_quantile))
    vol_thr = float(recent["mes_vol"].quantile(vol_quantile))
    row = daily.loc[target_date]
    mes_sig = float(row["mes_sig"])
    mes_vol = float(row["mes_vol"])
    side: Literal["BUY", "SELL", "NONE"] = "NONE"
    if mes_vol <= vol_thr:
        if mode in ("both", "long_only") and mes_sig >= pos_thr:
            side = "BUY"
        elif mode in ("both", "short_only") and mes_sig <= -pos_thr:
            side = "SELL"
    return LeadlagSignal(
        target_date=target_date,
        side=side,
        mes_sig=mes_sig,
        mes_vol=mes_vol,
        signal_thr=pos_thr,
        vol_thr=vol_thr,
        rolling_window_used=rolling_window,
    )


def simulate_paper_trade(
    daily: pd.DataFrame,
    signal: LeadlagSignal,
    notional_usd: float = DEFAULT_NOTIONAL_USD,
    cost_rt_pct: float = DEFAULT_COST_RT_PCT,
) -> PaperTrade:
    """Simulate the trade for a given signal. Returns PaperTrade with PnL.

    PnL model: entry at BTC open 00:00 UTC, exit at BTC close 07:59 UTC.
    If side="NONE", returns trade with pnl=0 and ret=0 (for journal completeness).
    """
    row = daily.loc[signal.target_date]
    entry = float(row["btc_entry_price"])
    exit_ = float(row["btc_exit_price"])
    if signal.side == "BUY":
        ret = exit_ / entry - 1.0
    elif signal.side == "SELL":
        ret = -(exit_ / entry - 1.0)
    else:
        ret = 0.0
    cost = cost_rt_pct if signal.side != "NONE" else 0.0
    pnl = notional_usd * (ret - cost)
    return PaperTrade(
        target_date=signal.target_date,
        side=signal.side,
        entry_price=entry,
        exit_price=exit_,
        notional_usd=notional_usd,
        gross_ret=ret,
        cost_pct=cost,
        pnl_usd=pnl,
        mes_sig=signal.mes_sig,
        mes_vol=signal.mes_vol,
    )


def data_is_fresh(
    mes_hourly: pd.DataFrame,
    btc_hourly: pd.DataFrame,
    now_utc: pd.Timestamp | None = None,
    max_age_days: int = MAX_DATA_AGE_DAYS,
) -> bool:
    """Return True iff both MES and BTC data are younger than `max_age_days`."""
    if now_utc is None:
        now_utc = pd.Timestamp.utcnow()
    if now_utc.tz is None:
        now_utc = now_utc.tz_localize("UTC")
    # MES
    mes = mes_hourly.copy().reset_index()
    mes = mes.rename(columns={mes.columns[0]: "timestamp"})
    mes["timestamp"] = pd.to_datetime(mes["timestamp"])
    if mes["timestamp"].dt.tz is None:
        mes["timestamp"] = mes["timestamp"].dt.tz_localize("UTC")
    mes_last = mes["timestamp"].max()
    # BTC
    btc_last = pd.to_datetime(btc_hourly["timestamp"], utc=True).max()
    age_mes = (now_utc - mes_last).days
    age_btc = (now_utc - btc_last).days
    return age_mes <= max_age_days and age_btc <= max_age_days
