"""
Walk-Forward Backtest — 4 EU Strategies v2
============================================
Strat 1: Equity Index Momentum (Trend Following)
Strat 2: Spread Intra-Indices (Relative Value)
Strat 3: Fixed Income Momentum (Rates Trend)
Strat 4: Volatility Expansion Breakout

Usage:
    python scripts/wf_eu_indices_v2.py
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = Path("data/eu")
REPORT_DIR = Path("reports/research")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Contract specs (multiplier, margin EUR)
CONTRACT_SPECS = {
    "ESTX50": {"mult": 10, "margin": 3500},
    "DAX":    {"mult": 25, "margin": 15000},
    "CAC40":  {"mult": 10, "margin": 3000},
    "MIB":    {"mult": 5,  "margin": 3000},
    # Bonds (ETF proxy -> use notional-based sizing)
    "FGBL":   {"mult": 1000, "margin": 2000},  # Bund ~1000 EUR/pt
    "FGBM":   {"mult": 1000, "margin": 1500},  # Bobl
    "FGBS":   {"mult": 1000, "margin": 800},   # Schatz
    "FBTP":   {"mult": 1000, "margin": 2500},  # BTP
}

COST_RT = 8.0  # round-trip cost per contract


def load(name: str) -> pd.DataFrame:
    f = DATA_DIR / f"{name}_1D.parquet"
    if not f.exists():
        raise FileNotFoundError(f)
    df = pd.read_parquet(f)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def align(*dfs: tuple[str, pd.DataFrame]) -> pd.DataFrame:
    """Align multiple DataFrames on common dates, return closes."""
    closes = {name: df["close"] for name, df in dfs}
    return pd.DataFrame(closes).dropna()


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    direction: str
    symbol: str
    entry_price: float
    exit_price: float
    pnl_usd: float
    holding_days: int


@dataclass
class WFResult:
    strategy: str
    window: int
    period: str
    trades: list[Trade] = field(default_factory=list)
    pnl: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_dd: float = 0.0
    n_trades: int = 0


def compute_metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"pnl": 0, "sharpe": 0, "wr": 0, "max_dd": 0, "n": 0}
    pnl = sum(t.pnl_usd for t in trades)
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    wr = wins / n if n > 0 else 0
    # Daily PnL for Sharpe
    daily = {}
    for t in trades:
        daily[t.exit_date] = daily.get(t.exit_date, 0) + t.pnl_usd
    s = pd.Series(daily).sort_index()
    sharpe = (s.mean() / s.std() * np.sqrt(252)) if len(s) > 1 and s.std() > 0 else 0
    # Max DD
    cum = np.cumsum([t.pnl_usd for t in trades])
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = float(dd.min()) if len(dd) > 0 else 0
    return {"pnl": pnl, "sharpe": sharpe, "wr": wr, "max_dd": max_dd, "n": n}


def walk_forward(run_fn, data: pd.DataFrame, n_windows: int = 5,
                 is_pct: float = 0.6, label: str = "") -> list[WFResult]:
    n = len(data)
    oos_size = int(n * (1 - is_pct) / n_windows)
    results = []

    for w in range(n_windows):
        is_end = int(n * is_pct) + w * oos_size
        oos_start = is_end
        oos_end = min(oos_start + oos_size, n)
        if oos_end <= oos_start or is_end < 100:
            break

        full = data.iloc[:oos_end]
        oos_start_date = str(data.index[oos_start].date())
        oos_end_date = str(data.index[oos_end - 1].date())

        all_trades = run_fn(full)
        oos_trades = [t for t in all_trades if oos_start_date <= t.entry_date <= oos_end_date]
        m = compute_metrics(oos_trades)

        results.append(WFResult(
            strategy=label, window=w + 1,
            period=f"{oos_start_date} to {oos_end_date}",
            trades=oos_trades, pnl=m["pnl"], sharpe=m["sharpe"],
            win_rate=m["wr"], max_dd=m["max_dd"], n_trades=m["n"],
        ))
    return results


def print_results(results: list[WFResult], name: str) -> dict:
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"{'='*70}")

    total_pnl = 0
    total_trades = 0
    total_wins = 0
    sharpes = []
    profit_w = 0

    for r in results:
        tag = "PROFIT" if r.pnl > 0 else "LOSS"
        print(f"  W{r.window} [{r.period}] : {r.n_trades:3d} trades | "
              f"PnL ${r.pnl:+,.0f} | WR {r.win_rate:.0%} | "
              f"Sharpe {r.sharpe:.2f} | MaxDD ${r.max_dd:,.0f} | {tag}")
        total_pnl += r.pnl
        total_trades += r.n_trades
        total_wins += int(r.win_rate * r.n_trades)
        sharpes.append(r.sharpe)
        if r.pnl > 0:
            profit_w += 1

    nw = len(results)
    avg_sharpe = np.mean(sharpes) if sharpes else 0
    wr = total_wins / total_trades if total_trades > 0 else 0
    wf = f"{profit_w}/{nw}"
    verdict = "PASS" if profit_w >= nw * 0.5 else "FAIL"

    print(f"  {'-'*66}")
    print(f"  TOTAL: {total_trades} trades | PnL ${total_pnl:+,.0f} | "
          f"WR {wr:.0%} | Avg Sharpe {avg_sharpe:.2f} | WF {wf}")
    print(f"  VERDICT: {verdict}")

    return {
        "strategy": name, "total_pnl": total_pnl, "total_trades": total_trades,
        "win_rate": wr, "avg_sharpe": avg_sharpe, "wf_ratio": wf,
        "profitable_windows": profit_w, "n_windows": nw, "verdict": verdict,
        "windows": [{"w": r.window, "period": r.period, "n": r.n_trades,
                     "pnl": r.pnl, "wr": r.win_rate, "sharpe": r.sharpe,
                     "max_dd": r.max_dd} for r in results],
    }


# ============================================================
# STRAT 1: Equity Index Momentum (Trend Following)
# ============================================================
def strat1_index_momentum(data: pd.DataFrame,
                           symbols: list[str],
                           vol_target: float = 0.12,
                           atr_period: int = 20,
                           ema_fast: int = 50,
                           ema_slow: int = 200,
                           breakout: int = 100,
                           atr_sl_mult: float = 2.5) -> list[Trade]:
    """
    Trend following on EU indices.
    Long if EMA50 > EMA200 AND price > highest(100).
    Short if EMA50 < EMA200 AND price < lowest(100).
    ATR trailing stop, vol-targeted sizing.
    Max 2 same-direction positions (correlation guard).
    """
    trades = []
    if len(data) < ema_slow + 10:
        return trades

    # Precompute indicators per symbol
    indicators = {}
    for sym in symbols:
        if sym not in data.columns:
            continue
        s = data[sym]
        ema_f = s.ewm(span=ema_fast, adjust=False).mean()
        ema_s = s.ewm(span=ema_slow, adjust=False).mean()
        highest = s.rolling(breakout).max()
        lowest = s.rolling(breakout).min()
        # ATR from close-to-close (simplified, no H/L)
        tr = s.diff().abs()
        atr = tr.rolling(atr_period).mean()
        indicators[sym] = {"ema_f": ema_f, "ema_s": ema_s,
                           "highest": highest, "lowest": lowest, "atr": atr}

    # Position tracking
    positions = {}  # sym -> {dir, entry_price, entry_date, trailing_stop, atr_at_entry}

    for i in range(ema_slow + breakout, len(data)):
        date = data.index[i]

        # Check exits first
        for sym in list(positions.keys()):
            pos = positions[sym]
            price = data[sym].iloc[i]
            atr_now = indicators[sym]["atr"].iloc[i]

            # Update trailing stop
            if pos["dir"] == "LONG":
                new_stop = price - atr_sl_mult * atr_now
                pos["trailing_stop"] = max(pos["trailing_stop"], new_stop)
                if price <= pos["trailing_stop"]:
                    # Exit
                    pnl_pts = price - pos["entry_price"]
                    spec = CONTRACT_SPECS.get(sym, {"mult": 10})
                    pnl = pnl_pts * spec["mult"] - COST_RT
                    trades.append(Trade(
                        entry_date=pos["entry_date"], exit_date=str(date.date()),
                        direction="LONG", symbol=sym,
                        entry_price=pos["entry_price"], exit_price=price,
                        pnl_usd=pnl,
                        holding_days=(date - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
                    ))
                    del positions[sym]
            else:  # SHORT
                new_stop = price + atr_sl_mult * atr_now
                pos["trailing_stop"] = min(pos["trailing_stop"], new_stop)
                if price >= pos["trailing_stop"]:
                    pnl_pts = pos["entry_price"] - price
                    spec = CONTRACT_SPECS.get(sym, {"mult": 10})
                    pnl = pnl_pts * spec["mult"] - COST_RT
                    trades.append(Trade(
                        entry_date=pos["entry_date"], exit_date=str(date.date()),
                        direction="SHORT", symbol=sym,
                        entry_price=pos["entry_price"], exit_price=price,
                        pnl_usd=pnl,
                        holding_days=(date - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
                    ))
                    del positions[sym]

        # Count current exposure by direction (correlation guard)
        n_long = sum(1 for p in positions.values() if p["dir"] == "LONG")
        n_short = sum(1 for p in positions.values() if p["dir"] == "SHORT")

        # Check entries
        for sym in symbols:
            if sym in positions or sym not in indicators:
                continue

            price = data[sym].iloc[i]
            ind = indicators[sym]
            ema_f = ind["ema_f"].iloc[i]
            ema_s = ind["ema_s"].iloc[i]
            highest = ind["highest"].iloc[i]
            lowest = ind["lowest"].iloc[i]
            atr = ind["atr"].iloc[i]

            if pd.isna(atr) or atr == 0:
                continue

            direction = None
            if ema_f > ema_s and price >= highest:
                if n_long < 2:  # Max 2 longs (correlation guard)
                    direction = "LONG"
            elif ema_f < ema_s and price <= lowest:
                if n_short < 2:
                    direction = "SHORT"

            if direction:
                stop = price - atr_sl_mult * atr if direction == "LONG" else price + atr_sl_mult * atr
                positions[sym] = {
                    "dir": direction,
                    "entry_price": price,
                    "entry_date": str(date.date()),
                    "trailing_stop": stop,
                    "atr_at_entry": atr,
                }
                if direction == "LONG":
                    n_long += 1
                else:
                    n_short += 1

    # Close remaining
    for sym in list(positions.keys()):
        pos = positions[sym]
        price = data[sym].iloc[-1]
        pnl_pts = (price - pos["entry_price"]) if pos["dir"] == "LONG" else (pos["entry_price"] - price)
        spec = CONTRACT_SPECS.get(sym, {"mult": 10})
        pnl = pnl_pts * spec["mult"] - COST_RT
        trades.append(Trade(
            entry_date=pos["entry_date"], exit_date=str(data.index[-1].date()),
            direction=pos["dir"], symbol=sym,
            entry_price=pos["entry_price"], exit_price=price,
            pnl_usd=pnl,
            holding_days=(data.index[-1] - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
        ))

    return trades


# ============================================================
# STRAT 2: Spread Intra-Indices (Relative Value)
# ============================================================
def strat2_spread_rv(data: pd.DataFrame,
                      pair: tuple[str, str],
                      lookback: int = 60,
                      z_entry: float = 2.0,
                      z_exit: float = 0.0,
                      z_stop: float = 3.5,
                      max_hold: int = 60) -> list[Trade]:
    """
    Mean reversion on log ratio spread between two indices.
    Beta-neutralized via OLS hedge ratio.
    """
    sym_a, sym_b = pair
    if sym_a not in data.columns or sym_b not in data.columns:
        return []

    trades = []
    log_ratio = np.log(data[sym_a] / data[sym_b])

    position = 0  # +1 = long spread (long A, short B), -1 = short spread
    entry_date = None
    entry_a = entry_b = 0.0
    hold_count = 0

    for i in range(lookback, len(data)):
        date = data.index[i]
        window = log_ratio.iloc[i - lookback:i]
        mu = window.mean()
        sigma = window.std()
        if sigma == 0:
            continue
        z = (log_ratio.iloc[i] - mu) / sigma

        if position == 0:
            if z < -z_entry:
                # Spread too low -> long A, short B
                position = 1
                entry_date = date
                entry_a = data[sym_a].iloc[i]
                entry_b = data[sym_b].iloc[i]
                hold_count = 0
            elif z > z_entry:
                # Spread too high -> short A, long B
                position = -1
                entry_date = date
                entry_a = data[sym_a].iloc[i]
                entry_b = data[sym_b].iloc[i]
                hold_count = 0
        else:
            hold_count += 1
            exit_now = False

            # TP at z = 0
            if position == 1 and z >= z_exit:
                exit_now = True
            elif position == -1 and z <= -z_exit:
                exit_now = True
            # SL at z = 3.5
            if position == 1 and z < -z_stop:
                exit_now = True
            elif position == -1 and z > z_stop:
                exit_now = True
            # Max hold
            if hold_count >= max_hold:
                exit_now = True

            if exit_now:
                exit_a = data[sym_a].iloc[i]
                exit_b = data[sym_b].iloc[i]
                holding = (date - entry_date).days
                spec_a = CONTRACT_SPECS.get(sym_a, {"mult": 10})
                spec_b = CONTRACT_SPECS.get(sym_b, {"mult": 10})

                # Leg A
                dir_a = "LONG" if position == 1 else "SHORT"
                pnl_a = ((exit_a - entry_a) if dir_a == "LONG" else (entry_a - exit_a)) * spec_a["mult"] - COST_RT

                # Leg B
                dir_b = "SHORT" if position == 1 else "LONG"
                pnl_b = ((exit_b - entry_b) if dir_b == "LONG" else (entry_b - exit_b)) * spec_b["mult"] - COST_RT

                trades.append(Trade(entry_date=str(entry_date.date()),
                    exit_date=str(date.date()), direction=dir_a, symbol=sym_a,
                    entry_price=entry_a, exit_price=exit_a, pnl_usd=pnl_a, holding_days=holding))
                trades.append(Trade(entry_date=str(entry_date.date()),
                    exit_date=str(date.date()), direction=dir_b, symbol=sym_b,
                    entry_price=entry_b, exit_price=exit_b, pnl_usd=pnl_b, holding_days=holding))

                position = 0

    return trades


# ============================================================
# STRAT 3: Fixed Income Momentum (Rates Trend)
# ============================================================
def strat3_fi_momentum(data: pd.DataFrame,
                        symbols: list[str],
                        ema_fast: int = 20,
                        ema_slow: int = 100,
                        atr_period: int = 20,
                        atr_sl: float = 2.0) -> list[Trade]:
    """
    Trend following on EU bond ETF proxies.
    Long if EMA20 > EMA100 (rates falling = bond prices rising).
    Short if inverse.
    Risk parity between maturities.
    """
    trades = []
    if len(data) < ema_slow + 10:
        return trades

    indicators = {}
    for sym in symbols:
        if sym not in data.columns:
            continue
        s = data[sym]
        ef = s.ewm(span=ema_fast, adjust=False).mean()
        es = s.ewm(span=ema_slow, adjust=False).mean()
        tr = s.diff().abs()
        atr = tr.rolling(atr_period).mean()
        # MACD
        ema12 = s.ewm(span=12, adjust=False).mean()
        ema26 = s.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        indicators[sym] = {"ema_f": ef, "ema_s": es, "atr": atr,
                           "macd": macd, "macd_sig": macd_signal}

    positions = {}

    for i in range(ema_slow + 5, len(data)):
        date = data.index[i]

        # Exits
        for sym in list(positions.keys()):
            pos = positions[sym]
            price = data[sym].iloc[i]
            ind = indicators[sym]
            atr_now = ind["atr"].iloc[i]

            if pos["dir"] == "LONG":
                new_stop = price - atr_sl * atr_now
                pos["trailing_stop"] = max(pos["trailing_stop"], new_stop)
                # Also exit if trend reverses
                if ind["ema_f"].iloc[i] < ind["ema_s"].iloc[i]:
                    pos["trailing_stop"] = price  # force exit

                if price <= pos["trailing_stop"]:
                    pnl = (price - pos["entry_price"]) * CONTRACT_SPECS.get(sym, {"mult": 1})["mult"] - COST_RT
                    trades.append(Trade(
                        entry_date=pos["entry_date"], exit_date=str(date.date()),
                        direction="LONG", symbol=sym,
                        entry_price=pos["entry_price"], exit_price=price,
                        pnl_usd=pnl,
                        holding_days=(date - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
                    ))
                    del positions[sym]
            else:
                new_stop = price + atr_sl * atr_now
                pos["trailing_stop"] = min(pos["trailing_stop"], new_stop)
                if ind["ema_f"].iloc[i] > ind["ema_s"].iloc[i]:
                    pos["trailing_stop"] = price

                if price >= pos["trailing_stop"]:
                    pnl = (pos["entry_price"] - price) * CONTRACT_SPECS.get(sym, {"mult": 1})["mult"] - COST_RT
                    trades.append(Trade(
                        entry_date=pos["entry_date"], exit_date=str(date.date()),
                        direction="SHORT", symbol=sym,
                        entry_price=pos["entry_price"], exit_price=price,
                        pnl_usd=pnl,
                        holding_days=(date - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
                    ))
                    del positions[sym]

        # Entries
        for sym in symbols:
            if sym in positions or sym not in indicators:
                continue
            ind = indicators[sym]
            price = data[sym].iloc[i]
            atr = ind["atr"].iloc[i]
            if pd.isna(atr) or atr == 0:
                continue

            ema_f = ind["ema_f"].iloc[i]
            ema_s = ind["ema_s"].iloc[i]
            macd = ind["macd"].iloc[i]
            macd_sig = ind["macd_sig"].iloc[i]

            direction = None
            if ema_f > ema_s and macd > macd_sig:
                direction = "LONG"
            elif ema_f < ema_s and macd < macd_sig:
                direction = "SHORT"

            if direction:
                stop = (price - atr_sl * atr) if direction == "LONG" else (price + atr_sl * atr)
                positions[sym] = {
                    "dir": direction,
                    "entry_price": price,
                    "entry_date": str(date.date()),
                    "trailing_stop": stop,
                }

    # Close remaining
    for sym in list(positions.keys()):
        pos = positions[sym]
        price = data[sym].iloc[-1]
        pnl_pts = (price - pos["entry_price"]) if pos["dir"] == "LONG" else (pos["entry_price"] - price)
        pnl = pnl_pts * CONTRACT_SPECS.get(sym, {"mult": 1})["mult"] - COST_RT
        trades.append(Trade(
            entry_date=pos["entry_date"], exit_date=str(data.index[-1].date()),
            direction=pos["dir"], symbol=sym,
            entry_price=pos["entry_price"], exit_price=price,
            pnl_usd=pnl,
            holding_days=(data.index[-1] - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
        ))

    return trades


# ============================================================
# STRAT 4: Volatility Expansion Breakout
# ============================================================
def strat4_vol_breakout(data: pd.DataFrame,
                         symbols: list[str],
                         bb_period: int = 20,
                         bb_std: float = 2.0,
                         atr_period: int = 20,
                         atr_pct_threshold: float = 0.20,
                         donchian: int = 50,
                         atr_sl: float = 1.5,
                         atr_tp: float = 3.0,
                         lookback_pct: int = 100) -> list[Trade]:
    """
    Breakout after volatility compression.
    Entry: ATR percentile < 20% AND price breaks Donchian channel.
    SL: 1.5 ATR, TP: 3 ATR.
    Max 2 trades simultaneous.
    """
    trades = []
    if len(data) < max(donchian, lookback_pct) + 10:
        return trades

    indicators = {}
    for sym in symbols:
        if sym not in data.columns:
            continue
        s = data[sym]
        tr = s.diff().abs()
        atr = tr.rolling(atr_period).mean()
        # ATR percentile over lookback
        atr_pctile = atr.rolling(lookback_pct).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
        # Donchian
        dc_high = s.rolling(donchian).max()
        dc_low = s.rolling(donchian).min()
        # Bollinger
        bb_mid = s.rolling(bb_period).mean()
        bb_std_val = s.rolling(bb_period).std()
        bb_upper = bb_mid + bb_std * bb_std_val
        bb_lower = bb_mid - bb_std * bb_std_val
        # Bandwidth
        bb_width = (bb_upper - bb_lower) / bb_mid

        indicators[sym] = {
            "atr": atr, "atr_pctile": atr_pctile,
            "dc_high": dc_high, "dc_low": dc_low,
            "bb_upper": bb_upper, "bb_lower": bb_lower,
            "bb_width": bb_width,
        }

    positions = {}

    for i in range(max(donchian, lookback_pct) + 5, len(data)):
        date = data.index[i]

        # Exits (SL/TP)
        for sym in list(positions.keys()):
            pos = positions[sym]
            price = data[sym].iloc[i]

            if pos["dir"] == "LONG":
                if price <= pos["sl"] or price >= pos["tp"]:
                    pnl = (price - pos["entry_price"]) * CONTRACT_SPECS.get(sym, {"mult": 10})["mult"] - COST_RT
                    trades.append(Trade(
                        entry_date=pos["entry_date"], exit_date=str(date.date()),
                        direction="LONG", symbol=sym,
                        entry_price=pos["entry_price"], exit_price=price,
                        pnl_usd=pnl,
                        holding_days=(date - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
                    ))
                    del positions[sym]
            else:
                if price >= pos["sl"] or price <= pos["tp"]:
                    pnl = (pos["entry_price"] - price) * CONTRACT_SPECS.get(sym, {"mult": 10})["mult"] - COST_RT
                    trades.append(Trade(
                        entry_date=pos["entry_date"], exit_date=str(date.date()),
                        direction="SHORT", symbol=sym,
                        entry_price=pos["entry_price"], exit_price=price,
                        pnl_usd=pnl,
                        holding_days=(date - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
                    ))
                    del positions[sym]

        if len(positions) >= 2:
            continue

        # Entries
        for sym in symbols:
            if sym in positions or sym not in indicators:
                continue
            if len(positions) >= 2:
                break

            ind = indicators[sym]
            price = data[sym].iloc[i]
            prev_price = data[sym].iloc[i - 1]
            atr = ind["atr"].iloc[i]
            pctile = ind["atr_pctile"].iloc[i]

            if pd.isna(pctile) or pd.isna(atr) or atr == 0:
                continue

            # Volatility compression check
            if pctile > atr_pct_threshold:
                continue

            dc_high = ind["dc_high"].iloc[i - 1]  # yesterday's channel (no lookahead)
            dc_low = ind["dc_low"].iloc[i - 1]

            direction = None
            if price > dc_high and prev_price <= dc_high:
                direction = "LONG"
            elif price < dc_low and prev_price >= dc_low:
                direction = "SHORT"

            if direction:
                if direction == "LONG":
                    sl = price - atr_sl * atr
                    tp = price + atr_tp * atr
                else:
                    sl = price + atr_sl * atr
                    tp = price - atr_tp * atr

                positions[sym] = {
                    "dir": direction,
                    "entry_price": price,
                    "entry_date": str(date.date()),
                    "sl": sl,
                    "tp": tp,
                }

    # Close remaining
    for sym in list(positions.keys()):
        pos = positions[sym]
        price = data[sym].iloc[-1]
        pnl_pts = (price - pos["entry_price"]) if pos["dir"] == "LONG" else (pos["entry_price"] - price)
        pnl = pnl_pts * CONTRACT_SPECS.get(sym, {"mult": 10})["mult"] - COST_RT
        trades.append(Trade(
            entry_date=pos["entry_date"], exit_date=str(data.index[-1].date()),
            direction=pos["dir"], symbol=sym,
            entry_price=pos["entry_price"], exit_price=price,
            pnl_usd=pnl,
            holding_days=(data.index[-1] - pd.Timestamp(pos["entry_date"], tz="UTC")).days,
        ))

    return trades


# ============================================================
# MAIN
# ============================================================
def main():
    print("Loading data...")

    # Load equity indices
    eq_indices = {}
    for name in ["ESTX50", "DAX", "CAC40", "MIB"]:
        try:
            eq_indices[name] = load(name)
            print(f"  {name}: {len(eq_indices[name])} bars")
        except FileNotFoundError:
            print(f"  {name}: NOT FOUND")

    # Load bond proxies
    bond_indices = {}
    for name in ["FGBL", "FGBM", "FGBS", "FBTP"]:
        try:
            bond_indices[name] = load(name)
            print(f"  {name}: {len(bond_indices[name])} bars")
        except FileNotFoundError:
            print(f"  {name}: NOT FOUND")

    # Align equity
    eq_closes = align(*eq_indices.items())
    print(f"\nEquity aligned: {len(eq_closes)} days, {list(eq_closes.columns)}")

    # Align bonds
    bond_closes = align(*bond_indices.items())
    print(f"Bonds aligned: {len(bond_closes)} days, {list(bond_closes.columns)}")

    all_results = []

    # ---- STRAT 1: Equity Index Momentum ----
    eq_symbols = list(eq_closes.columns)

    def run_s1(c):
        return strat1_index_momentum(c, eq_symbols)

    r1 = walk_forward(run_s1, eq_closes, n_windows=5, label="EQ Momentum")
    s1 = print_results(r1, "STRAT 1: Equity Index Momentum (Trend Following)")
    all_results.append(s1)

    # Variant: tighter parameters
    def run_s1b(c):
        return strat1_index_momentum(c, eq_symbols, ema_fast=20, ema_slow=100,
                                      breakout=50, atr_sl_mult=2.0)

    r1b = walk_forward(run_s1b, eq_closes, n_windows=5, label="EQ Momentum (fast)")
    s1b = print_results(r1b, "STRAT 1b: Equity Momentum (fast EMA20/100, BO50)")
    all_results.append(s1b)

    # ---- STRAT 2: Spread Intra-Indices ----
    pairs = [
        ("DAX", "ESTX50"),
        ("ESTX50", "CAC40"),
        ("MIB", "ESTX50"),
    ]

    for sym_a, sym_b in pairs:
        pair_name = f"{sym_a}/{sym_b}"

        def run_s2(c, a=sym_a, b=sym_b):
            return strat2_spread_rv(c, pair=(a, b))

        r2 = walk_forward(run_s2, eq_closes, n_windows=5, label=f"Spread {pair_name}")
        s2 = print_results(r2, f"STRAT 2: Spread {pair_name}")
        all_results.append(s2)

    # ---- STRAT 3: Fixed Income Momentum ----
    bond_symbols = list(bond_closes.columns)

    def run_s3(c):
        return strat3_fi_momentum(c, bond_symbols)

    r3 = walk_forward(run_s3, bond_closes, n_windows=5, label="FI Momentum")
    s3 = print_results(r3, "STRAT 3: Fixed Income Momentum")
    all_results.append(s3)

    # Variant: fast
    def run_s3b(c):
        return strat3_fi_momentum(c, bond_symbols, ema_fast=10, ema_slow=50, atr_sl=1.5)

    r3b = walk_forward(run_s3b, bond_closes, n_windows=5, label="FI Momentum (fast)")
    s3b = print_results(r3b, "STRAT 3b: FI Momentum (fast EMA10/50)")
    all_results.append(s3b)

    # ---- STRAT 4: Volatility Expansion Breakout ----
    def run_s4(c):
        return strat4_vol_breakout(c, eq_symbols)

    r4 = walk_forward(run_s4, eq_closes, n_windows=5, label="Vol Breakout")
    s4 = print_results(r4, "STRAT 4: Volatility Expansion Breakout")
    all_results.append(s4)

    # Variant: relaxed ATR threshold
    def run_s4b(c):
        return strat4_vol_breakout(c, eq_symbols, atr_pct_threshold=0.30,
                                    donchian=30, atr_sl=2.0, atr_tp=4.0)

    r4b = walk_forward(run_s4b, eq_closes, n_windows=5, label="Vol Breakout (relaxed)")
    s4b = print_results(r4b, "STRAT 4b: Vol Breakout (relaxed, DC30)")
    all_results.append(s4b)

    # ---- SUMMARY ----
    print(f"\n{'='*80}")
    print(f"  SUMMARY - ALL STRATEGIES v2")
    print(f"{'='*80}")
    print(f"{'Strategy':<50} {'Trades':>6} {'PnL':>12} {'WR':>6} {'Sharpe':>7} {'WF':>6} {'Verdict':>8}")
    print(f"{'-'*95}")

    for s in all_results:
        print(f"{s['strategy']:<50} {s['total_trades']:>6} "
              f"${s['total_pnl']:>+10,.0f} {s['win_rate']:>5.0%} "
              f"{s['avg_sharpe']:>7.2f} {s['wf_ratio']:>6} {s['verdict']:>8}")

    # Margin analysis
    print(f"\n{'='*70}")
    print(f"  MARGIN REQUIREMENTS (EUR 10K capital)")
    print(f"{'='*70}")
    print(f"\nStrat 1 (EQ Momentum): max 2 contracts same direction")
    for sym in eq_symbols:
        m = CONTRACT_SPECS.get(sym, {}).get("margin", "?")
        print(f"  {sym}: EUR {m:,}")
    print(f"  Worst case (2 contracts): EUR {2 * max(CONTRACT_SPECS.get(s, {}).get('margin', 0) for s in eq_symbols):,}")

    print(f"\nStrat 2 (Spreads): 2 contracts per spread")
    for a, b in pairs:
        m = CONTRACT_SPECS.get(a, {}).get("margin", 0) + CONTRACT_SPECS.get(b, {}).get("margin", 0)
        print(f"  {a}/{b}: EUR {m:,}")

    print(f"\nStrat 3 (FI Momentum): 1 contract per instrument")
    for sym in bond_symbols:
        m = CONTRACT_SPECS.get(sym, {}).get("margin", "?")
        print(f"  {sym}: EUR {m:,}")

    print(f"\nStrat 4 (Vol Breakout): max 2 trades")
    for sym in eq_symbols:
        m = CONTRACT_SPECS.get(sym, {}).get("margin", "?")
        print(f"  {sym}: EUR {m:,}")

    # Save report
    report_path = REPORT_DIR / "wf_eu_indices_v2.json"
    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
