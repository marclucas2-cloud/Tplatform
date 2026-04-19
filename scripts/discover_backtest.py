"""Quick WF backtest of 5 strategy candidates."""
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

MES_MULT = 5
MNQ_MULT = 2
MGC_MULT = 10  # micro gold $10/point
COMM = 1.24  # round-trip


def load(sym):
    df = pd.read_parquet(ROOT / f"data/futures/{sym}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def wf_test(trades_fn, data, n_windows=5, is_pct=0.6, label=""):
    n = len(data)
    oos_size = int(n * (1 - is_pct) / n_windows)
    results = []
    for w in range(n_windows):
        is_end = int(n * is_pct) + w * oos_size
        oos_start = is_end
        oos_end = min(oos_start + oos_size, n)
        if oos_end <= oos_start:
            break
        full = data.iloc[:oos_end]
        oos_start_date = data.index[oos_start]
        oos_end_date = data.index[oos_end - 1]
        all_trades = trades_fn(full)
        oos_trades = [t for t in all_trades
                      if oos_start_date <= pd.Timestamp(t["date"], tz="UTC") <= oos_end_date]
        pnl = sum(t["pnl"] for t in oos_trades)
        n_t = len(oos_trades)
        wins = sum(1 for t in oos_trades if t["pnl"] > 0)
        results.append({"w": w + 1, "pnl": pnl, "n": n_t, "wr": wins / n_t if n_t else 0})
    return results


def print_wf(results, label):
    total_pnl = sum(r["pnl"] for r in results)
    total_n = sum(r["n"] for r in results)
    profit_w = sum(1 for r in results if r["pnl"] > 0)
    nw = len(results)
    wins = sum(int(r["wr"] * r["n"]) for r in results)
    wr = wins / total_n if total_n else 0
    wf = f"{profit_w}/{nw}"
    verdict = "PASS" if profit_w >= nw * 0.5 else "FAIL"
    print(f"  {label}")
    for r in results:
        tag = "+" if r["pnl"] > 0 else "-"
        print(f"    W{r['w']}: {r['n']:3d} trades | ${r['pnl']:+,.0f} | WR {r['wr']:.0%} [{tag}]")
    print(f"    TOTAL: {total_n} trades | ${total_pnl:+,.0f} | WR {wr:.0%} | WF {wf} | {verdict}")
    return {"label": label, "pnl": total_pnl, "n": total_n, "wr": wr, "wf": wf, "verdict": verdict}


def main():
    mes = load("MES")
    mnq = load("MNQ")
    mgc = load("MGC")
    vix = load("VIX")

    all_results = []

    # 1. TURNAROUND TUESDAY
    print("=" * 60)
    print("1. TURNAROUND TUESDAY")
    print("=" * 60)

    def turnaround_tuesday(data):
        trades = []
        for i in range(1, len(data)):
            dow = data.index[i].dayofweek
            if dow != 1:  # Tuesday = 1
                continue
            # Check Monday was red
            prev_i = i - 1
            if data.index[prev_i].dayofweek != 0:  # Monday = 0
                continue
            mon_open = data["open"].iloc[prev_i]
            mon_close = data["close"].iloc[prev_i]
            if mon_close >= mon_open:  # Monday green, skip
                continue
            # BUY Tuesday open, SELL Tuesday close
            entry = data["open"].iloc[i]
            exit_p = data["close"].iloc[i]
            sl = entry - 30  # 30pt SL
            # Check if SL hit
            if data["low"].iloc[i] <= sl:
                exit_p = sl
            pnl = (exit_p - entry) * MES_MULT - COMM
            trades.append({"date": str(data.index[i].date()), "pnl": pnl})
        return trades

    r = wf_test(turnaround_tuesday, mes, label="Turnaround Tuesday MES")
    all_results.append(print_wf(r, "Turnaround Tuesday MES"))

    # 2. GOLD-EQUITY DIVERGENCE
    print("\n" + "=" * 60)
    print("2. GOLD-EQUITY DIVERGENCE")
    print("=" * 60)

    def gold_equity_div(data):
        # Need both MES and MGC
        trades = []
        mes_c = data["close"]
        mgc_full = mgc.reindex(data.index, method="ffill")
        if "close" not in mgc_full.columns:
            return trades
        mgc_c = mgc_full["close"]
        for i in range(10, len(data)):
            mes_ret5 = (mes_c.iloc[i] / mes_c.iloc[i - 5] - 1)
            mgc_ret5 = (mgc_c.iloc[i] / mgc_c.iloc[i - 5] - 1) if mgc_c.iloc[i - 5] > 0 else 0
            # Divergence: MES up > 2%, MGC down < -1%
            if mes_ret5 > 0.02 and mgc_ret5 < -0.01:
                # SHORT MES, exit in 5 days
                entry = mes_c.iloc[i]
                exit_i = min(i + 5, len(data) - 1)
                exit_p = mes_c.iloc[exit_i]
                pnl = (entry - exit_p) * MES_MULT - COMM
                trades.append({"date": str(data.index[i].date()), "pnl": pnl})
            # Reverse: MES down > 2%, MGC up > 1%
            elif mes_ret5 < -0.02 and mgc_ret5 > 0.01:
                entry = mes_c.iloc[i]
                exit_i = min(i + 5, len(data) - 1)
                exit_p = mes_c.iloc[exit_i]
                pnl = (exit_p - entry) * MES_MULT - COMM
                trades.append({"date": str(data.index[i].date()), "pnl": pnl})
        return trades

    r = wf_test(gold_equity_div, mes, label="Gold-Equity Divergence")
    all_results.append(print_wf(r, "Gold-Equity Divergence"))

    # 3. VIX MEAN REVERSION
    print("\n" + "=" * 60)
    print("3. VIX MEAN REVERSION")
    print("=" * 60)

    def vix_mr(data):
        trades = []
        mes_c = data["close"]
        vix_full = vix.reindex(data.index, method="ffill")
        if "close" not in vix_full.columns:
            return trades
        vix_c = vix_full["close"]
        # RSI14
        delta = mes_c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = delta.clip(upper=0).abs().rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss)
        in_trade = False
        entry_price = 0
        for i in range(20, len(data)):
            v = vix_c.iloc[i]
            r = rsi.iloc[i]
            if not in_trade:
                if v > 25 and r < 30:
                    in_trade = True
                    entry_price = mes_c.iloc[i]
                    entry_date = data.index[i]
            else:
                # Exit: VIX < 20 or 10 days max
                days_held = (data.index[i] - entry_date).days
                if v < 20 or days_held >= 10:
                    exit_p = mes_c.iloc[i]
                    sl = entry_price - 50  # 50pt SL
                    if mes_c.iloc[i] <= sl:
                        exit_p = sl
                    pnl = (exit_p - entry_price) * MES_MULT - COMM
                    trades.append({"date": str(entry_date.date()), "pnl": pnl})
                    in_trade = False
        return trades

    r = wf_test(vix_mr, mes, label="VIX Mean Reversion")
    all_results.append(print_wf(r, "VIX Mean Reversion"))

    # 4. CRYPTO RANGE BREAKOUT
    print("\n" + "=" * 60)
    print("4. CRYPTO RANGE BREAKOUT (BTC proxy via MES)")
    print("=" * 60)
    # Skip - we don't have BTC futures data in the same format
    print("  SKIP — requires Binance data, not futures. Test separately.")

    # 5. FIRST HOUR MOMENTUM (5M data)
    print("\n" + "=" * 60)
    print("5. FIRST HOUR MOMENTUM")
    print("=" * 60)

    try:
        mes5m = pd.read_parquet(ROOT / "data/futures/MES_5M.parquet")
        mes5m.columns = [c.lower() for c in mes5m.columns]
        if mes5m.index.tz is None:
            mes5m.index = mes5m.index.tz_localize("UTC")

        def first_hour_mom(data_5m):
            trades = []
            # Group by date
            data_5m = data_5m.copy()
            data_5m["date"] = data_5m.index.date
            for dt, day_data in data_5m.groupby("date"):
                if len(day_data) < 12:  # need at least 1h of 5m bars
                    continue
                # First 6 bars = 30 minutes
                first_30 = day_data.iloc[:6]
                rest = day_data.iloc[6:]
                if len(rest) < 1:
                    continue
                open_price = first_30["open"].iloc[0]
                close_30 = first_30["close"].iloc[-1]
                ret_30 = (close_30 - open_price) / open_price
                if abs(ret_30) < 0.003:  # need > 0.3% move
                    continue
                direction = "BUY" if ret_30 > 0 else "SELL"
                entry = close_30
                exit_p = rest["close"].iloc[-1]
                # SL
                sl = entry * (1 - 0.005) if direction == "BUY" else entry * (1 + 0.005)
                if direction == "BUY":
                    if rest["low"].min() <= sl:
                        exit_p = sl
                    pnl = (exit_p - entry) * MES_MULT - COMM
                else:
                    if rest["high"].max() >= sl:
                        exit_p = sl
                    pnl = (entry - exit_p) * MES_MULT - COMM
                trades.append({"date": str(dt), "pnl": pnl})
            return trades

        # WF on 5M data
        r5 = wf_test(first_hour_mom, mes5m, label="First Hour Momentum MES")
        all_results.append(print_wf(r5, "First Hour Momentum MES"))
    except Exception as e:
        print(f"  ERROR: {e}")

    # 6. BONUS: MCL Momentum (oil is +77% in 63d!)
    print("\n" + "=" * 60)
    print("6. BONUS: MCL TSMOM (oil momentum)")
    print("=" * 60)

    def mcl_tsmom(data):
        trades = []
        mcl_full = mgc  # reuse MGC loader pattern
        try:
            mcl_data = pd.read_parquet(ROOT / "data/futures/MCL_1D.parquet")
            mcl_data.columns = [c.lower() for c in mcl_data.columns]
            if mcl_data.index.tz is None:
                mcl_data.index = mcl_data.index.tz_localize("UTC")
        except Exception:
            return trades
        c = mcl_data["close"]
        for i in range(63, len(mcl_data), 21):  # rebalance monthly
            ret63 = c.iloc[i] / c.iloc[i - 63] - 1
            side = "BUY" if ret63 > 0 else "SELL"
            entry = c.iloc[i]
            exit_i = min(i + 21, len(mcl_data) - 1)
            exit_p = c.iloc[exit_i]
            mcl_mult = 100  # MCL = $100/point
            if side == "BUY":
                pnl = (exit_p - entry) * mcl_mult - COMM
            else:
                pnl = (entry - exit_p) * mcl_mult - COMM
            trades.append({"date": str(mcl_data.index[i].date()), "pnl": pnl})
        return trades

    mcl_data = load("MCL")
    r_mcl = wf_test(mcl_tsmom, mcl_data, label="MCL TSMOM")
    all_results.append(print_wf(r_mcl, "MCL TSMOM (oil momentum)"))

    # SUMMARY
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"{'Strategy':<35} {'Trades':>6} {'PnL':>10} {'WR':>6} {'WF':>6} {'Verdict':>8}")
    print("-" * 75)
    for r in all_results:
        print(f"{r['label']:<35} {r['n']:>6} ${r['pnl']:>+9,.0f} {r['wr']:>5.0%} {r['wf']:>6} {r['verdict']:>8}")


if __name__ == "__main__":
    main()
