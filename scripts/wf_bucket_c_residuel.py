#!/usr/bin/env python3
"""RE-WF Bucket C residuel post-XXL — vix_mean_reversion, gold_equity_divergence,
sector_rotation_eu (Phase C-2 residuel).

Run via : python scripts/wf_bucket_c_residuel.py

Genere des manifests JSON dans data/research/wf_bucket_c/ via wf_canonical.
Pour chaque strat:
  1. Charge data parquet 5.3 ans (2021-01 -> 2026-03)
  2. Implementation event-driven minimaliste (signal -> entry -> SL/TP/exit)
  3. Window function PnL/Sharpe/MaxDD/n_trades par fenetre WF
  4. Verdict canonique (VALIDATED / REJECTED / INSUFFICIENT_TRADES)

Couts: 0 commission + slippage 0.05% par leg (futures CME tick).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.research.wf_canonical import run_walk_forward  # noqa: E402

DATA_DIR = ROOT / "data" / "futures"
OUTPUT_DIR = ROOT / "data" / "research" / "wf_bucket_c"
SLIPPAGE_PCT = 0.0005   # 0.05% per leg


def load_data(symbol: str) -> pd.DataFrame:
    """Load + clean parquet."""
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    if "datetime" in df.columns:
        df.index = pd.to_datetime(df["datetime"])
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def _sharpe(returns: np.ndarray) -> float:
    if len(returns) < 2:
        return 0.0
    mu = float(np.mean(returns))
    sd = float(np.std(returns, ddof=1))
    if sd <= 0:
        return 0.0
    return float(mu / sd * np.sqrt(252))


def _max_dd_pct(equity_curve: np.ndarray) -> float:
    if len(equity_curve) < 2:
        return 0.0
    peak = np.maximum.accumulate(equity_curve)
    dd = (equity_curve - peak) / np.maximum(peak, 1e-9)
    return float(dd.min() * 100)


# -----------------------------------------------------------------------------
# Strategy implementations (signal logic)
# -----------------------------------------------------------------------------

def _vix_mean_reversion_signal(mes_df: pd.DataFrame, vix_df: pd.DataFrame) -> pd.Series:
    """BUY MES when VIX > 25 AND RSI14(MES) < 30. Hold 10d max OR until VIX < 20."""
    delta = mes_df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    vix_aligned = vix_df["close"].reindex(mes_df.index).ffill()
    signal = (vix_aligned.shift(1) > 25) & (rsi.shift(1) < 30)
    return signal.fillna(False)


def _gold_equity_div_signal(mes_df: pd.DataFrame, mgc_df: pd.DataFrame) -> pd.Series:
    """LONG MES if 5d MES return < -2% AND 5d MGC return > +1%.
    SHORT MES if 5d MES return > +2% AND 5d MGC return < -1%.
    Returns +1 (long), -1 (short), 0 (none).
    """
    mes_ret_5d = mes_df["close"].pct_change(5).shift(1)
    mgc_aligned = mgc_df["close"].reindex(mes_df.index).ffill()
    mgc_ret_5d = mgc_aligned.pct_change(5).shift(1)

    long_sig = (mes_ret_5d < -0.02) & (mgc_ret_5d > 0.01)
    short_sig = (mes_ret_5d > 0.02) & (mgc_ret_5d < -0.01)
    return long_sig.astype(int) - short_sig.astype(int)


def _sector_rotation_signal(dax_df: pd.DataFrame, cac_df: pd.DataFrame) -> pd.Series:
    """Weekly Monday rebalance: long winner of DAX vs CAC40 20d momentum.
    Returns +1 (DAX long), -1 (CAC long), 0 (neutral)."""
    dax_mom = dax_df["close"].pct_change(20).shift(1)
    cac_aligned = cac_df["close"].reindex(dax_df.index).ffill()
    cac_mom = cac_aligned.pct_change(20).shift(1)
    diff = dax_mom - cac_mom

    # Rebalance only on Mondays
    is_monday = dax_df.index.weekday == 0
    sig = pd.Series(0, index=dax_df.index)
    sig[is_monday & (diff > 0.02)] = 1   # DAX
    sig[is_monday & (diff < -0.02)] = -1  # CAC40 (we go LONG CAC, code as -1 for differentiation)
    # Hold position 5 days: forward fill the non-zero values
    sig = sig.where(sig != 0).ffill(limit=5).fillna(0).astype(int)
    return sig


# -----------------------------------------------------------------------------
# Generic backtest window function builder
# -----------------------------------------------------------------------------

def _backtest_window(
    signal: pd.Series,
    price: pd.Series,
    train_s: int,
    train_e: int,
    test_e: int,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    max_hold_days: int = 10,
) -> dict:
    """Simple event-driven backtest on the test window.

    Position sizing: 1 contract notional. PnL = (exit - entry) * sign * contract_value.
    For simplicity we use percentage returns scaled to 1 contract MES (~$50K notional).
    """
    test_signal = signal.iloc[train_e:test_e]
    test_price = price.iloc[train_e:test_e]

    in_pos = 0  # +1 long, -1 short
    entry_price = 0.0
    days_held = 0
    pnl_pct_log: list[float] = []
    trade_count = 0

    for i in range(len(test_signal)):
        sig = int(test_signal.iloc[i]) if not pd.isna(test_signal.iloc[i]) else 0
        # Boolean signal coerce
        if isinstance(test_signal.iloc[i], (bool, np.bool_)):
            sig = 1 if test_signal.iloc[i] else 0
        cur_price = float(test_price.iloc[i])

        if in_pos == 0 and sig != 0:
            in_pos = sig
            entry_price = cur_price * (1 + SLIPPAGE_PCT * sig)
            days_held = 0
            trade_count += 1
        elif in_pos != 0:
            days_held += 1
            ret = (cur_price - entry_price) / entry_price * in_pos
            exit_now = False
            # SL / TP / time exit
            if sl_pct is not None and ret < -abs(sl_pct):
                exit_now = True
            elif tp_pct is not None and ret > abs(tp_pct):
                exit_now = True
            elif days_held >= max_hold_days:
                exit_now = True
            elif sig == 0:  # signal disappear
                exit_now = True

            if exit_now:
                exit_price = cur_price * (1 - SLIPPAGE_PCT * in_pos)
                final_ret = (exit_price - entry_price) / entry_price * in_pos
                pnl_pct_log.append(final_ret)
                in_pos = 0

    # Close any open position at last bar
    if in_pos != 0 and entry_price > 0:
        exit_price = float(test_price.iloc[-1]) * (1 - SLIPPAGE_PCT * in_pos)
        final_ret = (exit_price - entry_price) / entry_price * in_pos
        pnl_pct_log.append(final_ret)

    if not pnl_pct_log:
        return {"sharpe": 0.0, "max_dd_pct": 0.0, "total_pnl_usd": 0.0, "n_trades": 0}

    arr = np.array(pnl_pct_log)
    sharpe = _sharpe(arr)
    eq = (1 + arr).cumprod()
    max_dd = _max_dd_pct(eq)
    # Approx PnL in $: 1 contract MES ~= $50K notional, $5/point
    notional = 50_000.0
    total_pnl_usd = float(arr.sum() * notional)

    return {
        "sharpe": sharpe,
        "max_dd_pct": max_dd,
        "total_pnl_usd": total_pnl_usd,
        "n_trades": trade_count,
        "avg_trade_pnl_pct": float(arr.mean()),
    }


# -----------------------------------------------------------------------------
# Main runner
# -----------------------------------------------------------------------------

def run_vix_mean_reversion() -> dict:
    print("=== RE-WF vix_mean_reversion ===")
    mes = load_data("MES")
    vix = load_data("VIX")
    signal = _vix_mean_reversion_signal(mes, vix)

    def window_fn(train_s, train_e, test_e):
        return _backtest_window(
            signal, mes["close"], train_s, train_e, test_e,
            sl_pct=0.01, tp_pct=None, max_hold_days=10,  # SL ~50pts on MES ~5000 = ~1%
        )

    result = run_walk_forward(
        strategy_id="vix_mean_reversion",
        data_length=len(mes),
        backtest_window_fn=window_fn,
        n_windows=5,
        train_pct=0.7,
        test_pct=0.3,
        seed=42,
        extra_params={
            "vix_threshold": 25.0,
            "rsi_threshold": 30.0,
            "sl_points": 50,
            "max_hold_days": 10,
            "data_range": f"{mes.index.min().date()} -> {mes.index.max().date()}",
            "data_bars": len(mes),
        },
    )
    path = result.write_manifest(OUTPUT_DIR)
    print(f"  Verdict: {result.verdict}  (windows {result.windows_pass}/{result.windows_total} pass, "
          f"median Sharpe {result.median_sharpe:.2f})")
    print(f"  Manifest: {path.relative_to(ROOT)}")
    return {"strategy_id": "vix_mean_reversion", "verdict": result.verdict,
            "windows_pass": result.windows_pass, "windows_total": result.windows_total,
            "median_sharpe": result.median_sharpe, "median_dd": result.median_dd,
            "manifest": str(path.relative_to(ROOT))}


def run_gold_equity_divergence() -> dict:
    print("=== RE-WF gold_equity_divergence ===")
    mes = load_data("MES")
    mgc = load_data("MGC")
    signal = _gold_equity_div_signal(mes, mgc)

    def window_fn(train_s, train_e, test_e):
        return _backtest_window(
            signal, mes["close"], train_s, train_e, test_e,
            sl_pct=0.008, tp_pct=0.012, max_hold_days=5,  # SL 40pts/5000 = 0.8%
        )

    result = run_walk_forward(
        strategy_id="gold_equity_divergence",
        data_length=len(mes),
        backtest_window_fn=window_fn,
        n_windows=5, train_pct=0.7, test_pct=0.3, seed=42,
        extra_params={
            "mes_threshold": 0.02, "mgc_threshold": 0.01,
            "sl_points": 40, "tp_points": 60, "lookback": 5, "max_hold_days": 5,
            "data_range": f"{mes.index.min().date()} -> {mes.index.max().date()}",
            "data_bars": len(mes),
        },
    )
    path = result.write_manifest(OUTPUT_DIR)
    print(f"  Verdict: {result.verdict}  (windows {result.windows_pass}/{result.windows_total} pass, "
          f"median Sharpe {result.median_sharpe:.2f})")
    print(f"  Manifest: {path.relative_to(ROOT)}")
    return {"strategy_id": "gold_equity_divergence", "verdict": result.verdict,
            "windows_pass": result.windows_pass, "windows_total": result.windows_total,
            "median_sharpe": result.median_sharpe, "median_dd": result.median_dd,
            "manifest": str(path.relative_to(ROOT))}


def run_sector_rotation_eu() -> dict:
    print("=== RE-WF sector_rotation_eu ===")
    dax = load_data("DAX")
    cac = load_data("CAC40")
    signal = _sector_rotation_signal(dax, cac)
    # Use DAX price as proxy (longest data); position long DAX (+1) or long CAC (-1 = swap)
    # For simplicity: backtest only DAX leg with absolute(signal) entries
    abs_signal = signal.abs()  # 1 when in any position

    def window_fn(train_s, train_e, test_e):
        # We backtest "long DAX when sig>0, hold 5d", "long CAC when sig<0, hold 5d"
        # Simplification: track sign-aware return on DAX (long DAX) when sig>0,
        # CAC return when sig<0. For brevity we average them.
        sig_test = signal.iloc[train_e:test_e]
        dax_test = dax["close"].iloc[train_e:test_e]
        cac_test = cac["close"].reindex(dax.index).ffill().iloc[train_e:test_e]

        in_pos = 0; entry = 0.0; days = 0; pnls = []; n_trades = 0
        for i in range(len(sig_test)):
            sig = int(sig_test.iloc[i])
            price = float(dax_test.iloc[i] if sig >= 0 else cac_test.iloc[i])
            if in_pos == 0 and sig != 0:
                in_pos = sig
                entry = float(dax_test.iloc[i] if sig > 0 else cac_test.iloc[i]) * (1 + SLIPPAGE_PCT)
                days = 0; n_trades += 1
            elif in_pos != 0:
                days += 1
                cur = float(dax_test.iloc[i] if in_pos > 0 else cac_test.iloc[i])
                ret = (cur - entry) / entry
                exit_now = ret < -0.04 or ret > 0.08 or days >= 5 or sig != in_pos
                if exit_now:
                    exit_p = cur * (1 - SLIPPAGE_PCT)
                    pnls.append((exit_p - entry) / entry)
                    in_pos = 0

        if not pnls:
            return {"sharpe": 0.0, "max_dd_pct": 0.0, "total_pnl_usd": 0.0, "n_trades": 0}
        arr = np.array(pnls)
        sharpe = _sharpe(arr)
        eq = (1 + arr).cumprod()
        max_dd = _max_dd_pct(eq)
        total_pnl = float(arr.sum() * 50_000)
        return {"sharpe": sharpe, "max_dd_pct": max_dd, "total_pnl_usd": total_pnl,
                "n_trades": n_trades}

    result = run_walk_forward(
        strategy_id="sector_rotation_eu",
        data_length=len(dax),
        backtest_window_fn=window_fn,
        n_windows=5, train_pct=0.7, test_pct=0.3, seed=42,
        extra_params={
            "momentum_period": 20, "threshold": 0.02,
            "sl_pct": 0.04, "tp_pct": 0.08, "max_hold_days": 5,
            "data_range": f"{dax.index.min().date()} -> {dax.index.max().date()}",
            "data_bars": len(dax),
        },
    )
    path = result.write_manifest(OUTPUT_DIR)
    print(f"  Verdict: {result.verdict}  (windows {result.windows_pass}/{result.windows_total} pass, "
          f"median Sharpe {result.median_sharpe:.2f})")
    print(f"  Manifest: {path.relative_to(ROOT)}")
    return {"strategy_id": "sector_rotation_eu", "verdict": result.verdict,
            "windows_pass": result.windows_pass, "windows_total": result.windows_total,
            "median_sharpe": result.median_sharpe, "median_dd": result.median_dd,
            "manifest": str(path.relative_to(ROOT))}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for runner in (run_vix_mean_reversion, run_gold_equity_divergence, run_sector_rotation_eu):
        try:
            results.append(runner())
        except Exception as e:
            print(f"ERROR: {runner.__name__} failed: {e}")
            import traceback; traceback.print_exc()

    print("\n=== SYNTHESE BUCKET C-2 RE-WF ===")
    for r in results:
        print(f"  {r['strategy_id']:<28} {r['verdict']:<25} "
              f"({r['windows_pass']}/{r['windows_total']} OOS, "
              f"med Sharpe {r['median_sharpe']:+.2f}, med DD {r['median_dd']:+.2f}%)")
    return results


if __name__ == "__main__":
    main()
