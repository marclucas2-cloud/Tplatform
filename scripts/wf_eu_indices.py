"""
Walk-Forward Backtest — 4 EU Index Futures Strategies
=====================================================
Strat 1: Country Momentum Cross-Sectionnel (Asness 1997)
Strat 2: Core vs Periphery Spread
Strat 3: Nikkei Lead-Lag (timezone arbitrage)
Strat 4: Intra-EU Relative Strength Pairs

Usage:
    python scripts/wf_eu_indices.py
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

# ----- Contract specs for PnL calculation -----
# Using cash index as proxy — 1 point = multiplier in currency
CONTRACT_SPECS = {
    "ESTX50": {"multiplier": 10, "currency": "EUR", "margin": 3500},
    "DAX":    {"multiplier": 25, "currency": "EUR", "margin": 15000},
    "SMI":    {"multiplier": 10, "currency": "CHF", "margin": 3500},
    "MIB":    {"multiplier": 5,  "currency": "EUR", "margin": 3000},
    "IBEX":   {"multiplier": 10, "currency": "EUR", "margin": 3500},
    "CAC40":  {"multiplier": 10, "currency": "EUR", "margin": 3000},
    "AEX":    {"multiplier": 200,"currency": "EUR", "margin": 5000},
    "FTSE100":{"multiplier": 10, "currency": "GBP", "margin": 3500},
    "NKD":    {"multiplier": 5,  "currency": "USD", "margin": 5000},
    "MES":    {"multiplier": 5,  "currency": "USD", "margin": 1400},
    "M2K_IDX":{"multiplier": 5,  "currency": "USD", "margin": 500},
}

# Slippage + commission per contract round-trip
COST_PER_CONTRACT = 8.0  # ~$4 each way (commission + slippage)


def load_index(name: str) -> pd.DataFrame:
    """Load daily OHLCV for an index."""
    f = DATA_DIR / f"{name}_1D.parquet"
    if not f.exists():
        raise FileNotFoundError(f"Missing data: {f}")
    df = pd.read_parquet(f)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def load_all_indices() -> dict[str, pd.DataFrame]:
    """Load all available indices."""
    indices = {}
    for name in CONTRACT_SPECS:
        try:
            indices[name] = load_index(name)
        except FileNotFoundError:
            pass
    return indices


def align_indices(indices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Create aligned close price DataFrame (inner join on dates)."""
    closes = {}
    for name, df in indices.items():
        closes[name] = df["close"]
    aligned = pd.DataFrame(closes).dropna()
    return aligned


@dataclass
class TradeResult:
    entry_date: str
    exit_date: str
    direction: str  # "LONG" or "SHORT"
    symbol: str
    entry_price: float
    exit_price: float
    pnl_points: float
    pnl_usd: float
    holding_days: int


@dataclass
class BacktestResult:
    strategy: str
    window: int
    period: str
    trades: list[TradeResult] = field(default_factory=list)
    pnl_usd: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_dd_pct: float = 0.0
    n_trades: int = 0


# ============================================================
# STRATEGY 1: Country Momentum Cross-Sectionnel
# ============================================================
def strat1_country_momentum(closes: pd.DataFrame,
                             symbols: list[str],
                             lookback: int = 252,
                             skip_last: int = 21,
                             top_n: int = 3,
                             rebal_freq: int = 21) -> list[TradeResult]:
    """
    Long top-N momentum, short bottom-N momentum.
    Momentum = return over lookback days, skipping last skip_last days.
    Rebalance every rebal_freq trading days.
    """
    trades = []
    dates = closes.index

    if len(dates) < lookback + skip_last + 10:
        return trades

    positions: dict[str, str] = {}  # symbol -> "LONG"/"SHORT"
    entry_prices: dict[str, float] = {}
    entry_dates: dict[str, pd.Timestamp] = {}

    for i in range(lookback + skip_last, len(dates)):
        if (i - (lookback + skip_last)) % rebal_freq != 0:
            continue

        date = dates[i]

        # Calculate momentum for each index
        mom = {}
        for sym in symbols:
            if sym not in closes.columns:
                continue
            price_now = closes[sym].iloc[i - skip_last]
            price_past = closes[sym].iloc[i - lookback - skip_last]
            if price_past > 0:
                mom[sym] = (price_now / price_past) - 1

        if len(mom) < 2 * top_n:
            continue

        ranked = sorted(mom.items(), key=lambda x: x[1], reverse=True)
        longs = [s for s, _ in ranked[:top_n]]
        shorts = [s for s, _ in ranked[-top_n:]]

        # Close positions not in new portfolio
        for sym in list(positions.keys()):
            new_side = "LONG" if sym in longs else ("SHORT" if sym in shorts else None)
            if new_side != positions[sym]:
                # Close
                exit_price = closes[sym].iloc[i]
                entry_p = entry_prices[sym]
                direction = positions[sym]
                pnl_pts = (exit_price - entry_p) if direction == "LONG" else (entry_p - exit_price)
                spec = CONTRACT_SPECS.get(sym, {"multiplier": 1})
                pnl_usd = pnl_pts * spec["multiplier"] - COST_PER_CONTRACT
                holding = (date - entry_dates[sym]).days

                trades.append(TradeResult(
                    entry_date=str(entry_dates[sym].date()),
                    exit_date=str(date.date()),
                    direction=direction,
                    symbol=sym,
                    entry_price=entry_p,
                    exit_price=exit_price,
                    pnl_points=pnl_pts,
                    pnl_usd=pnl_usd,
                    holding_days=holding,
                ))
                del positions[sym]
                del entry_prices[sym]
                del entry_dates[sym]

        # Open new positions
        for sym in longs:
            if sym not in positions:
                positions[sym] = "LONG"
                entry_prices[sym] = closes[sym].iloc[i]
                entry_dates[sym] = date

        for sym in shorts:
            if sym not in positions:
                positions[sym] = "SHORT"
                entry_prices[sym] = closes[sym].iloc[i]
                entry_dates[sym] = date

    # Close remaining at end
    if positions:
        last_date = dates[-1]
        for sym in list(positions.keys()):
            exit_price = closes[sym].iloc[-1]
            entry_p = entry_prices[sym]
            direction = positions[sym]
            pnl_pts = (exit_price - entry_p) if direction == "LONG" else (entry_p - exit_price)
            spec = CONTRACT_SPECS.get(sym, {"multiplier": 1})
            pnl_usd = pnl_pts * spec["multiplier"] - COST_PER_CONTRACT
            holding = (last_date - entry_dates[sym]).days
            trades.append(TradeResult(
                entry_date=str(entry_dates[sym].date()),
                exit_date=str(last_date.date()),
                direction=direction,
                symbol=sym,
                entry_price=entry_p,
                exit_price=exit_price,
                pnl_points=pnl_pts,
                pnl_usd=pnl_usd,
                holding_days=holding,
            ))

    return trades


# ============================================================
# STRATEGY 2: Core vs Periphery Spread
# ============================================================
def strat2_core_periphery(closes: pd.DataFrame,
                           core_syms: list[str] = None,
                           periph_syms: list[str] = None,
                           lookback: int = 60,
                           z_entry: float = 1.5,
                           z_exit: float = 0.5,
                           ret_window: int = 20) -> list[TradeResult]:
    """
    Mean-reversion on core vs periphery EU spread.
    """
    if core_syms is None:
        core_syms = ["ESTX50", "SMI", "AEX"]
    if periph_syms is None:
        periph_syms = ["MIB", "IBEX"]

    # Verify data availability
    available_core = [s for s in core_syms if s in closes.columns]
    available_periph = [s for s in periph_syms if s in closes.columns]

    if len(available_core) < 2 or len(available_periph) < 1:
        return []

    trades = []
    dates = closes.index

    # Compute returns
    core_ret = closes[available_core].pct_change(ret_window).mean(axis=1)
    periph_ret = closes[available_periph].pct_change(ret_window).mean(axis=1)
    spread = core_ret - periph_ret

    position = 0  # +1 = long periph/short core, -1 = long core/short periph
    entry_date = None
    entry_prices_core = {}
    entry_prices_periph = {}

    for i in range(lookback + ret_window, len(dates)):
        date = dates[i]

        # Z-score of spread
        window = spread.iloc[i - lookback:i]
        if window.std() == 0:
            continue
        z = (spread.iloc[i] - window.mean()) / window.std()

        # Entry
        if position == 0:
            if z > z_entry:
                # Core outperforming -> convergence trade: long periph, short core
                position = 1
                entry_date = date
                entry_prices_core = {s: closes[s].iloc[i] for s in available_core}
                entry_prices_periph = {s: closes[s].iloc[i] for s in available_periph}
            elif z < -z_entry:
                # Periphery outperforming -> divergence trade: long core, short periph
                position = -1
                entry_date = date
                entry_prices_core = {s: closes[s].iloc[i] for s in available_core}
                entry_prices_periph = {s: closes[s].iloc[i] for s in available_periph}

        # Exit
        elif position != 0:
            exit_signal = False
            if position == 1 and z < z_exit:
                exit_signal = True
            elif position == -1 and z > -z_exit:
                exit_signal = True

            # Stop-loss: if spread moves 3x entry z against us
            if abs(z) > 3 * z_entry:
                exit_signal = True

            if exit_signal:
                holding = (date - entry_date).days

                # PnL from core leg (SHORT if position=1)
                for sym in available_core:
                    exit_p = closes[sym].iloc[i]
                    entry_p = entry_prices_core[sym]
                    direction = "SHORT" if position == 1 else "LONG"
                    pnl_pts = (entry_p - exit_p) if direction == "SHORT" else (exit_p - entry_p)
                    spec = CONTRACT_SPECS.get(sym, {"multiplier": 1})
                    pnl_usd = pnl_pts * spec["multiplier"] - COST_PER_CONTRACT
                    trades.append(TradeResult(
                        entry_date=str(entry_date.date()),
                        exit_date=str(date.date()),
                        direction=direction,
                        symbol=sym,
                        entry_price=entry_p,
                        exit_price=exit_p,
                        pnl_points=pnl_pts,
                        pnl_usd=pnl_usd,
                        holding_days=holding,
                    ))

                # PnL from periphery leg (LONG if position=1)
                for sym in available_periph:
                    exit_p = closes[sym].iloc[i]
                    entry_p = entry_prices_periph[sym]
                    direction = "LONG" if position == 1 else "SHORT"
                    pnl_pts = (exit_p - entry_p) if direction == "LONG" else (entry_p - exit_p)
                    spec = CONTRACT_SPECS.get(sym, {"multiplier": 1})
                    pnl_usd = pnl_pts * spec["multiplier"] - COST_PER_CONTRACT
                    trades.append(TradeResult(
                        entry_date=str(entry_date.date()),
                        exit_date=str(date.date()),
                        direction=direction,
                        symbol=sym,
                        entry_price=entry_p,
                        exit_price=exit_p,
                        pnl_points=pnl_pts,
                        pnl_usd=pnl_usd,
                        holding_days=holding,
                    ))

                position = 0

    return trades


# ============================================================
# STRATEGY 3: Nikkei Lead-Lag
# ============================================================
def strat3_nikkei_leadlag(closes: pd.DataFrame,
                           nkd_sym: str = "NKD",
                           eu_sym: str = "ESTX50",
                           threshold: float = 0.01,
                           sl_pct: float = 0.015,
                           tp_pct: float = 0.01) -> list[TradeResult]:
    """
    If Nikkei moved > threshold% today, trade EU in same direction.
    Intraday (using daily data: entry at open, exit at close - simulated via open/close).
    Since we only have daily data, we simulate:
    - Signal: NKD return yesterday (close-to-close)
    - Entry: EU open today
    - Exit: EU close today
    """
    if nkd_sym not in closes.columns or eu_sym not in closes.columns:
        return []

    trades = []

    # We need open prices for EU
    eu_data = load_index(eu_sym)
    nkd_data = load_index(nkd_sym)

    # Align dates
    common = closes.index

    for i in range(2, len(common)):
        date = common[i]
        prev_date = common[i - 1]

        # Nikkei return yesterday
        if prev_date not in nkd_data.index or common[i - 2] not in nkd_data.index:
            continue

        nkd_close = nkd_data.loc[prev_date, "close"] if prev_date in nkd_data.index else None
        nkd_prev = nkd_data.loc[common[i - 2], "close"] if common[i - 2] in nkd_data.index else None

        if nkd_close is None or nkd_prev is None or nkd_prev == 0:
            continue

        nkd_ret = (nkd_close - nkd_prev) / nkd_prev

        if abs(nkd_ret) < threshold:
            continue

        # Trade EU today
        if date not in eu_data.index:
            continue

        entry_price = eu_data.loc[date, "open"]
        exit_price = eu_data.loc[date, "close"]

        if pd.isna(entry_price) or pd.isna(exit_price) or entry_price == 0:
            continue

        direction = "LONG" if nkd_ret > 0 else "SHORT"

        # Apply SL/TP
        eu_high = eu_data.loc[date, "high"]
        eu_low = eu_data.loc[date, "low"]

        if direction == "LONG":
            sl_price = entry_price * (1 - sl_pct)
            tp_price = entry_price * (1 + tp_pct)
            if eu_low <= sl_price:
                exit_price = sl_price
            elif eu_high >= tp_price:
                exit_price = tp_price
            pnl_pts = exit_price - entry_price
        else:
            sl_price = entry_price * (1 + sl_pct)
            tp_price = entry_price * (1 - tp_pct)
            if eu_high >= sl_price:
                exit_price = sl_price
            elif eu_low <= tp_price:
                exit_price = tp_price
            pnl_pts = entry_price - exit_price

        spec = CONTRACT_SPECS.get(eu_sym, {"multiplier": 1})
        pnl_usd = pnl_pts * spec["multiplier"] - COST_PER_CONTRACT

        trades.append(TradeResult(
            entry_date=str(date.date()),
            exit_date=str(date.date()),
            direction=direction,
            symbol=eu_sym,
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            pnl_points=float(pnl_pts),
            pnl_usd=float(pnl_usd),
            holding_days=0,
        ))

    return trades


# ============================================================
# STRATEGY 4: Intra-EU Relative Strength Pairs
# ============================================================
def strat4_pairs_relative(closes: pd.DataFrame,
                           sym_a: str = "CAC40",
                           sym_b: str = "MIB",
                           lookback: int = 60,
                           z_entry: float = 2.0,
                           z_exit: float = 0.5,
                           max_hold: int = 40) -> list[TradeResult]:
    """
    Mean reversion on ratio sym_a / sym_b.
    """
    if sym_a not in closes.columns or sym_b not in closes.columns:
        return []

    trades = []
    ratio = closes[sym_a] / closes[sym_b]
    log_ratio = np.log(ratio)

    position = 0  # +1 = long A short B, -1 = short A long B
    entry_date = None
    entry_a = entry_b = 0.0
    hold_count = 0

    for i in range(lookback, len(closes)):
        date = closes.index[i]

        window = log_ratio.iloc[i - lookback:i]
        if window.std() == 0:
            continue
        z = (log_ratio.iloc[i] - window.mean()) / window.std()

        if position == 0:
            if z > z_entry:
                # Ratio too high -> short A, long B (expect convergence)
                position = -1
                entry_date = date
                entry_a = closes[sym_a].iloc[i]
                entry_b = closes[sym_b].iloc[i]
                hold_count = 0
            elif z < -z_entry:
                # Ratio too low -> long A, short B
                position = 1
                entry_date = date
                entry_a = closes[sym_a].iloc[i]
                entry_b = closes[sym_b].iloc[i]
                hold_count = 0
        else:
            hold_count += 1
            exit_signal = False

            if position == 1 and z > -z_exit:
                exit_signal = True
            elif position == -1 and z < z_exit:
                exit_signal = True
            if hold_count >= max_hold:
                exit_signal = True
            # Stop-loss: z goes 3x further against
            if position == 1 and z < -(z_entry + 2):
                exit_signal = True
            if position == -1 and z > (z_entry + 2):
                exit_signal = True

            if exit_signal:
                exit_a = closes[sym_a].iloc[i]
                exit_b = closes[sym_b].iloc[i]
                holding = (date - entry_date).days

                spec_a = CONTRACT_SPECS.get(sym_a, {"multiplier": 1})
                spec_b = CONTRACT_SPECS.get(sym_b, {"multiplier": 1})

                # Leg A
                dir_a = "LONG" if position == 1 else "SHORT"
                pnl_a_pts = (exit_a - entry_a) if dir_a == "LONG" else (entry_a - exit_a)
                pnl_a = pnl_a_pts * spec_a["multiplier"] - COST_PER_CONTRACT

                trades.append(TradeResult(
                    entry_date=str(entry_date.date()),
                    exit_date=str(date.date()),
                    direction=dir_a,
                    symbol=sym_a,
                    entry_price=entry_a,
                    exit_price=exit_a,
                    pnl_points=pnl_a_pts,
                    pnl_usd=pnl_a,
                    holding_days=holding,
                ))

                # Leg B
                dir_b = "SHORT" if position == 1 else "LONG"
                pnl_b_pts = (exit_b - entry_b) if dir_b == "LONG" else (entry_b - exit_b)
                pnl_b = pnl_b_pts * spec_b["multiplier"] - COST_PER_CONTRACT

                trades.append(TradeResult(
                    entry_date=str(entry_date.date()),
                    exit_date=str(date.date()),
                    direction=dir_b,
                    symbol=sym_b,
                    entry_price=entry_b,
                    exit_price=exit_b,
                    pnl_points=pnl_b_pts,
                    pnl_usd=pnl_b,
                    holding_days=holding,
                ))

                position = 0

    return trades


# ============================================================
# Walk-Forward Engine
# ============================================================
def walk_forward(run_fn, closes: pd.DataFrame, n_windows: int = 5,
                 is_pct: float = 0.6, label: str = "strategy") -> list[BacktestResult]:
    """
    Anchored walk-forward: IS grows, OOS is fixed-size rolling window.
    """
    n = len(closes)
    oos_size = int(n * (1 - is_pct) / n_windows)

    results = []
    for w in range(n_windows):
        is_end = int(n * is_pct) + w * oos_size
        oos_start = is_end
        oos_end = min(oos_start + oos_size, n)

        if oos_end <= oos_start or is_end < 100:
            break

        oos_closes = closes.iloc[oos_start:oos_end]
        period = f"{oos_closes.index[0].date()} to {oos_closes.index[-1].date()}"

        # Run strategy on full data up to oos_end but only count trades in OOS period
        full_closes = closes.iloc[:oos_end]
        all_trades = run_fn(full_closes)

        # Filter trades that entered during OOS period
        oos_start_date = str(oos_closes.index[0].date())
        oos_end_date = str(oos_closes.index[-1].date())
        oos_trades = [t for t in all_trades if oos_start_date <= t.entry_date <= oos_end_date]

        # Metrics
        pnl = sum(t.pnl_usd for t in oos_trades)
        n_trades = len(oos_trades)
        wins = sum(1 for t in oos_trades if t.pnl_usd > 0)
        wr = wins / n_trades if n_trades > 0 else 0

        # Sharpe from daily PnL
        if oos_trades:
            daily_pnl = {}
            for t in oos_trades:
                d = t.exit_date
                daily_pnl[d] = daily_pnl.get(d, 0) + t.pnl_usd
            pnl_series = pd.Series(daily_pnl).sort_index()
            sharpe = (pnl_series.mean() / pnl_series.std() * np.sqrt(252)) if pnl_series.std() > 0 else 0
        else:
            sharpe = 0

        # Max DD
        if oos_trades:
            cum = np.cumsum([t.pnl_usd for t in oos_trades])
            peak = np.maximum.accumulate(cum)
            dd = cum - peak
            max_dd = float(dd.min()) if len(dd) > 0 else 0
        else:
            max_dd = 0

        results.append(BacktestResult(
            strategy=label,
            window=w + 1,
            period=period,
            trades=oos_trades,
            pnl_usd=pnl,
            sharpe=sharpe,
            win_rate=wr,
            max_dd_pct=max_dd,
            n_trades=n_trades,
        ))

    return results


def print_wf_results(results: list[BacktestResult], name: str):
    """Pretty-print WF results."""
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"{'='*70}")

    total_pnl = 0
    total_trades = 0
    total_wins = 0
    all_sharpes = []
    profitable_windows = 0

    for r in results:
        status = "PROFIT" if r.pnl_usd > 0 else "LOSS"
        print(f"  W{r.window} [{r.period}] : {r.n_trades:3d} trades | "
              f"PnL ${r.pnl_usd:+,.0f} | WR {r.win_rate:.0%} | "
              f"Sharpe {r.sharpe:.2f} | MaxDD ${r.max_dd_pct:,.0f} | {status}")
        total_pnl += r.pnl_usd
        total_trades += r.n_trades
        total_wins += int(r.win_rate * r.n_trades)
        all_sharpes.append(r.sharpe)
        if r.pnl_usd > 0:
            profitable_windows += 1

    n_windows = len(results)
    avg_sharpe = np.mean(all_sharpes) if all_sharpes else 0
    overall_wr = total_wins / total_trades if total_trades > 0 else 0
    wf_ratio = f"{profitable_windows}/{n_windows}"

    print(f"  {'-'*66}")
    print(f"  TOTAL: {total_trades} trades | PnL ${total_pnl:+,.0f} | "
          f"WR {overall_wr:.0%} | Avg Sharpe {avg_sharpe:.2f} | WF {wf_ratio}")

    verdict = "PASS" if profitable_windows >= n_windows * 0.5 else "FAIL"
    print(f"  VERDICT: {verdict} (WF {wf_ratio}, need >= 50%)")

    return {
        "strategy": name,
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "win_rate": overall_wr,
        "avg_sharpe": avg_sharpe,
        "wf_ratio": wf_ratio,
        "profitable_windows": profitable_windows,
        "n_windows": n_windows,
        "verdict": verdict,
        "windows": [{
            "window": r.window,
            "period": r.period,
            "n_trades": r.n_trades,
            "pnl_usd": r.pnl_usd,
            "win_rate": r.win_rate,
            "sharpe": r.sharpe,
            "max_dd": r.max_dd_pct,
        } for r in results],
    }


# ============================================================
# MAIN
# ============================================================
def main():
    print("Loading data...")
    indices = load_all_indices()
    print(f"Loaded {len(indices)} indices: {list(indices.keys())}")

    closes = align_indices(indices)
    print(f"Aligned: {len(closes)} trading days, {closes.index[0].date()} to {closes.index[-1].date()}")
    print(f"Columns: {list(closes.columns)}")

    all_results = []

    # ---- Strat 1: Country Momentum ----
    mom_symbols = ["ESTX50", "DAX", "SMI", "MIB", "IBEX", "CAC40", "AEX", "FTSE100", "NKD"]
    available_mom = [s for s in mom_symbols if s in closes.columns]
    print(f"\nStrat 1 universe: {available_mom}")

    def run_mom(c):
        return strat1_country_momentum(c, available_mom, lookback=252, skip_last=21, top_n=3)

    r1 = walk_forward(run_mom, closes, n_windows=5, label="Country Momentum")
    s1 = print_wf_results(r1, "STRAT 1: Country Momentum Cross-Sectionnel")
    all_results.append(s1)

    # ---- Strat 2: Core vs Periphery ----
    def run_cp(c):
        return strat2_core_periphery(c,
            core_syms=["ESTX50", "SMI", "AEX"],
            periph_syms=["MIB", "IBEX"],
            lookback=60, z_entry=1.5, z_exit=0.5)

    r2 = walk_forward(run_cp, closes, n_windows=5, label="Core vs Periphery")
    s2 = print_wf_results(r2, "STRAT 2: Core vs Periphery Spread")
    all_results.append(s2)

    # ---- Strat 3: Nikkei Lead-Lag ----
    def run_nkd(c):
        return strat3_nikkei_leadlag(c, threshold=0.01, sl_pct=0.015, tp_pct=0.01)

    r3 = walk_forward(run_nkd, closes, n_windows=5, label="Nikkei Lead-Lag")
    s3 = print_wf_results(r3, "STRAT 3: Nikkei Lead-Lag")
    all_results.append(s3)

    # Also test with lower threshold
    def run_nkd_low(c):
        return strat3_nikkei_leadlag(c, threshold=0.005, sl_pct=0.012, tp_pct=0.008)

    r3b = walk_forward(run_nkd_low, closes, n_windows=5, label="Nikkei Lead-Lag (low)")
    s3b = print_wf_results(r3b, "STRAT 3b: Nikkei Lead-Lag (threshold 0.5%)")
    all_results.append(s3b)

    # ---- Strat 4: Intra-EU Pairs ----
    # Test multiple pairs
    pairs = [
        ("CAC40", "MIB", "CAC40/MIB"),
        ("ESTX50", "FTSE100", "ESTX50/FTSE"),
        ("DAX", "MIB", "DAX/MIB"),
        ("AEX", "IBEX", "AEX/IBEX"),
    ]

    for sym_a, sym_b, pair_name in pairs:
        def run_pair(c, a=sym_a, b=sym_b):
            return strat4_pairs_relative(c, sym_a=a, sym_b=b, lookback=60, z_entry=2.0, z_exit=0.5)

        r4 = walk_forward(run_pair, closes, n_windows=5, label=f"Pairs {pair_name}")
        s4 = print_wf_results(r4, f"STRAT 4: Pairs {pair_name}")
        all_results.append(s4)

    # ---- Summary ----
    print(f"\n{'='*70}")
    print(f"  SUMMARY — ALL STRATEGIES")
    print(f"{'='*70}")
    print(f"{'Strategy':<45} {'Trades':>6} {'PnL':>10} {'WR':>6} {'Sharpe':>7} {'WF':>6} {'Verdict':>8}")
    print(f"{'-'*90}")

    for s in all_results:
        print(f"{s['strategy']:<45} {s['total_trades']:>6} "
              f"${s['total_pnl']:>+9,.0f} {s['win_rate']:>5.0%} "
              f"{s['avg_sharpe']:>7.2f} {s['wf_ratio']:>6} {s['verdict']:>8}")

    # Margin requirements
    print(f"\n{'='*70}")
    print(f"  MARGIN REQUIREMENTS")
    print(f"{'='*70}")

    print("\nStrat 1 (Country Momentum): 3 LONG + 3 SHORT = 6 contracts")
    margins_needed = sorted(
        [(s, CONTRACT_SPECS[s]["margin"]) for s in available_mom if s in CONTRACT_SPECS],
        key=lambda x: x[1]
    )
    cheapest_6 = margins_needed[:6]
    total_margin = sum(m for _, m in cheapest_6)
    print(f"  Cheapest 6: {[s for s,_ in cheapest_6]} = ${total_margin:,} margin")

    print("\nStrat 2 (Core/Periphery): 3 core + 2 periph = 5 contracts")
    cp_margin = sum(CONTRACT_SPECS.get(s, {}).get("margin", 5000)
                    for s in ["ESTX50", "SMI", "AEX", "MIB", "IBEX"])
    print(f"  Total margin: ${cp_margin:,}")

    print("\nStrat 3 (Nikkei Lead-Lag): 1 ESTX50 intraday")
    print(f"  Margin: ${CONTRACT_SPECS['ESTX50']['margin']:,} (intraday = ~50%)")

    print("\nStrat 4 (Pairs): 2 contracts")
    for sym_a, sym_b, name in pairs:
        m = CONTRACT_SPECS.get(sym_a, {}).get("margin", 5000) + CONTRACT_SPECS.get(sym_b, {}).get("margin", 5000)
        print(f"  {name}: ${m:,}")

    # Save report
    report_path = REPORT_DIR / "wf_eu_indices.json"
    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
