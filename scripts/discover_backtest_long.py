"""Long-period WF backtest — 3 PASS strategies + crypto range breakout.
Data: 2015-2026 futures, 2018-2026 crypto.
"""
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

MES_MULT = 5
MGC_MULT = 10
MCL_MULT = 100  # micro crude = $100/point (actually $10 for MCL)
BTC_SIZE = 0.01  # trade 0.01 BTC (~$700)
COMM_FUT = 1.24
COMM_CRYPTO = 0.001  # 0.1% Binance


def load(path):
    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def wf_test(trades_fn, data, n_windows=8, is_pct=0.5, label=""):
    """Walk-forward with 8 windows, 50% IS."""
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
        oos_start_date = data.index[oos_start]
        oos_end_date = data.index[oos_end - 1]
        all_trades = trades_fn(full)
        oos_trades = [t for t in all_trades
                      if oos_start_date <= pd.Timestamp(t["date"], tz="UTC") <= oos_end_date]
        pnl = sum(t["pnl"] for t in oos_trades)
        n_t = len(oos_trades)
        wins = sum(1 for t in oos_trades if t["pnl"] > 0)
        results.append({
            "w": w + 1, "pnl": round(pnl, 2), "n": n_t,
            "wr": round(wins / n_t, 2) if n_t else 0,
            "period": f"{oos_start_date.date()} to {oos_end_date.date()}",
        })
    return results


def print_wf(results, label):
    total_pnl = sum(r["pnl"] for r in results)
    total_n = sum(r["n"] for r in results)
    profit_w = sum(1 for r in results if r["pnl"] > 0)
    nw = len(results)
    wins = sum(int(r["wr"] * r["n"]) for r in results)
    wr = wins / total_n if total_n else 0

    # Sharpe from trade PnLs
    all_pnls = []
    for r in results:
        if r["n"] > 0:
            avg = r["pnl"] / r["n"]
            all_pnls.extend([avg] * r["n"])
    sharpe = 0
    if all_pnls and np.std(all_pnls) > 0:
        sharpe = np.mean(all_pnls) / np.std(all_pnls) * np.sqrt(52)  # weekly approx

    wf = f"{profit_w}/{nw}"
    verdict = "PASS" if profit_w >= nw * 0.5 else "FAIL"
    print(f"\n  {label} ({nw} windows, {results[0]['period'].split(' to ')[0]} to {results[-1]['period'].split(' to ')[1]})")
    for r in results:
        tag = "PROFIT" if r["pnl"] > 0 else "LOSS"
        print(f"    W{r['w']} [{r['period']}]: {r['n']:3d} trades | ${r['pnl']:+,.0f} | WR {r['wr']:.0%} | {tag}")
    print(f"    ---")
    print(f"    TOTAL: {total_n} trades | ${total_pnl:+,.0f} | WR {wr:.0%} | Sharpe ~{sharpe:.2f} | WF {wf} | {verdict}")
    return {"label": label, "pnl": total_pnl, "n": total_n, "wr": wr, "sharpe": sharpe, "wf": wf, "verdict": verdict}


def main():
    mes = load(ROOT / "data/futures/MES_LONG.parquet")
    mgc = load(ROOT / "data/futures/MGC_LONG.parquet")
    mcl = load(ROOT / "data/futures/MCL_LONG.parquet")
    vix = load(ROOT / "data/futures/VIX_LONG.parquet")
    btc = load(ROOT / "data/crypto/candles/BTCUSDT_1D_LONG.parquet")

    print(f"Data: MES {len(mes)} bars, MGC {len(mgc)}, MCL {len(mcl)}, VIX {len(vix)}, BTC {len(btc)}")

    all_results = []

    # ================================================================
    # 1. GOLD-EQUITY DIVERGENCE (MES + MGC)
    # ================================================================
    print("\n" + "=" * 70)
    print("  1. GOLD-EQUITY DIVERGENCE")
    print("=" * 70)

    # Align MES and MGC
    common = mes.index.intersection(mgc.index)
    mes_a = mes.loc[common]
    mgc_a = mgc.loc[common]

    def gold_equity_div(data):
        trades = []
        idx = data.index
        mes_c = mes_a.loc[mes_a.index.isin(idx), "close"]
        mgc_c = mgc_a.loc[mgc_a.index.isin(idx), "close"]
        common_idx = mes_c.index.intersection(mgc_c.index)
        if len(common_idx) < 10:
            return trades
        for i in range(10, len(common_idx)):
            dt = common_idx[i]
            ci = common_idx.get_loc(dt)
            if ci < 5:
                continue
            mes_ret5 = mes_c.iloc[ci] / mes_c.iloc[ci - 5] - 1
            mgc_ret5 = mgc_c.iloc[ci] / mgc_c.iloc[ci - 5] - 1
            if mes_ret5 > 0.02 and mgc_ret5 < -0.01:
                entry = mes_c.iloc[ci]
                exit_i = min(ci + 5, len(mes_c) - 1)
                exit_p = mes_c.iloc[exit_i]
                pnl = (entry - exit_p) * MES_MULT - COMM_FUT  # SHORT MES
                trades.append({"date": str(dt.date()), "pnl": pnl})
            elif mes_ret5 < -0.02 and mgc_ret5 > 0.01:
                entry = mes_c.iloc[ci]
                exit_i = min(ci + 5, len(mes_c) - 1)
                exit_p = mes_c.iloc[exit_i]
                pnl = (exit_p - entry) * MES_MULT - COMM_FUT  # LONG MES
                trades.append({"date": str(dt.date()), "pnl": pnl})
        return trades

    r = wf_test(gold_equity_div, mes_a, n_windows=8, label="Gold-Equity Divergence")
    all_results.append(print_wf(r, "Gold-Equity Divergence (11 ans)"))

    # ================================================================
    # 2. VIX MEAN REVERSION
    # ================================================================
    print("\n" + "=" * 70)
    print("  2. VIX MEAN REVERSION")
    print("=" * 70)

    common_v = mes.index.intersection(vix.index)
    mes_v = mes.loc[common_v]
    vix_v = vix.loc[common_v]

    def vix_mr(data):
        trades = []
        idx = data.index
        mes_c = mes_v.loc[mes_v.index.isin(idx), "close"]
        vix_c = vix_v.loc[vix_v.index.isin(idx), "close"]
        common_idx = mes_c.index.intersection(vix_c.index)
        if len(common_idx) < 20:
            return trades
        delta = mes_c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = delta.clip(upper=0).abs().rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss)
        in_trade = False
        entry_price = 0
        entry_date = None
        for i in range(20, len(common_idx)):
            dt = common_idx[i]
            ci = common_idx.get_loc(dt)
            v = vix_c.iloc[ci]
            r_val = rsi.iloc[ci]
            if pd.isna(r_val):
                continue
            if not in_trade:
                if v > 25 and r_val < 30:
                    in_trade = True
                    entry_price = mes_c.iloc[ci]
                    entry_date = dt
            else:
                days_held = (dt - entry_date).days
                if v < 20 or days_held >= 10:
                    exit_p = mes_c.iloc[ci]
                    pnl = (exit_p - entry_price) * MES_MULT - COMM_FUT
                    trades.append({"date": str(entry_date.date()), "pnl": pnl})
                    in_trade = False
                elif mes_c.iloc[ci] <= entry_price - 50:
                    exit_p = entry_price - 50
                    pnl = (exit_p - entry_price) * MES_MULT - COMM_FUT
                    trades.append({"date": str(entry_date.date()), "pnl": pnl})
                    in_trade = False
        return trades

    r = wf_test(vix_mr, mes_v, n_windows=8, label="VIX Mean Reversion")
    all_results.append(print_wf(r, "VIX Mean Reversion (11 ans)"))

    # ================================================================
    # 3. MCL TSMOM (oil momentum)
    # ================================================================
    print("\n" + "=" * 70)
    print("  3. MCL TSMOM (oil momentum)")
    print("=" * 70)

    def mcl_tsmom(data):
        trades = []
        c = data["close"]
        for i in range(63, len(data), 21):
            ret63 = c.iloc[i] / c.iloc[i - 63] - 1
            side = "BUY" if ret63 > 0 else "SELL"
            entry = c.iloc[i]
            exit_i = min(i + 21, len(data) - 1)
            exit_p = c.iloc[exit_i]
            # MCL micro crude: $10/point (not $100)
            mcl_m = 10
            if side == "BUY":
                pnl = (exit_p - entry) * mcl_m - COMM_FUT
            else:
                pnl = (entry - exit_p) * mcl_m - COMM_FUT
            trades.append({"date": str(data.index[i].date()), "pnl": pnl})
        return trades

    r = wf_test(mcl_tsmom, mcl, n_windows=8, label="MCL TSMOM")
    all_results.append(print_wf(r, "MCL TSMOM (11 ans)"))

    # ================================================================
    # 4. CRYPTO RANGE BREAKOUT (BTC)
    # ================================================================
    print("\n" + "=" * 70)
    print("  4. CRYPTO RANGE BREAKOUT (BTC)")
    print("=" * 70)

    def btc_range_breakout(data):
        trades = []
        c = data["close"]
        h = data["high"]
        l = data["low"]
        for i in range(15, len(data)):
            # Check 10-day range
            range_10 = (h.iloc[i-10:i].max() - l.iloc[i-10:i].min()) / c.iloc[i-10]
            if range_10 >= 0.05:  # need < 5% range
                continue
            range_high = h.iloc[i-10:i].max()
            range_low = l.iloc[i-10:i].min()
            price = c.iloc[i]
            # Breakout
            if price > range_high:
                direction = "BUY"
            elif price < range_low:
                direction = "SELL"
            else:
                continue
            entry = price
            # Hold 5 days
            exit_i = min(i + 5, len(data) - 1)
            exit_p = c.iloc[exit_i]
            # SL: 3% from entry
            if direction == "BUY":
                sl = entry * 0.97
                if l.iloc[i:exit_i+1].min() <= sl:
                    exit_p = sl
                pnl = (exit_p - entry) / entry * BTC_SIZE * entry - entry * BTC_SIZE * COMM_CRYPTO * 2
            else:
                sl = entry * 1.03
                if h.iloc[i:exit_i+1].max() >= sl:
                    exit_p = sl
                pnl = (entry - exit_p) / entry * BTC_SIZE * entry - entry * BTC_SIZE * COMM_CRYPTO * 2
            trades.append({"date": str(data.index[i].date()), "pnl": pnl})
        return trades

    r = wf_test(btc_range_breakout, btc, n_windows=8, label="BTC Range Breakout")
    all_results.append(print_wf(r, "BTC Range Breakout (8 ans)"))

    # ================================================================
    # 5. BONUS: BTC TSMOM (same as futures TSMOM but on crypto)
    # ================================================================
    print("\n" + "=" * 70)
    print("  5. BTC TSMOM (crypto momentum)")
    print("=" * 70)

    def btc_tsmom(data):
        trades = []
        c = data["close"]
        for i in range(63, len(data), 21):
            ret63 = c.iloc[i] / c.iloc[i - 63] - 1
            side = "BUY" if ret63 > 0 else "SELL"
            entry = c.iloc[i]
            exit_i = min(i + 21, len(data) - 1)
            exit_p = c.iloc[exit_i]
            trade_size = 500  # $500 per trade
            if side == "BUY":
                pnl = (exit_p / entry - 1) * trade_size - trade_size * COMM_CRYPTO * 2
            else:
                pnl = (1 - exit_p / entry) * trade_size - trade_size * COMM_CRYPTO * 2
            trades.append({"date": str(data.index[i].date()), "pnl": pnl})
        return trades

    r = wf_test(btc_tsmom, btc, n_windows=8, label="BTC TSMOM")
    all_results.append(print_wf(r, "BTC TSMOM (8 ans)"))

    # ================================================================
    # 6. ETH/BTC RATIO MR (crypto pairs)
    # ================================================================
    print("\n" + "=" * 70)
    print("  6. ETH/BTC RATIO MEAN REVERSION")
    print("=" * 70)

    eth = load(ROOT / "data/crypto/candles/ETHUSDT_1D_LONG.parquet")
    common_eb = btc.index.intersection(eth.index)
    btc_eb = btc.loc[common_eb]
    eth_eb = eth.loc[common_eb]

    def ethbtc_mr(data):
        trades = []
        idx = data.index
        b_c = btc_eb.loc[btc_eb.index.isin(idx), "close"]
        e_c = eth_eb.loc[eth_eb.index.isin(idx), "close"]
        ci = b_c.index.intersection(e_c.index)
        if len(ci) < 60:
            return trades
        ratio = np.log(e_c.loc[ci].values / b_c.loc[ci].values)
        for i in range(60, len(ci)):
            window = ratio[i-60:i]
            mu = window.mean()
            sigma = window.std()
            if sigma == 0:
                continue
            z = (ratio[i] - mu) / sigma
            if abs(z) < 2.0:
                continue
            # Trade: if ETH/BTC ratio too high, short ETH, long BTC
            entry_eth = e_c.iloc[i]
            entry_btc = b_c.iloc[i]
            exit_i = min(i + 10, len(ci) - 1)
            exit_eth = e_c.iloc[exit_i]
            exit_btc = b_c.iloc[exit_i]
            trade_size = 250  # $250 per leg
            if z > 2.0:
                # Short ETH, Long BTC
                pnl_eth = (1 - exit_eth / entry_eth) * trade_size
                pnl_btc = (exit_btc / entry_btc - 1) * trade_size
            else:
                # Long ETH, Short BTC
                pnl_eth = (exit_eth / entry_eth - 1) * trade_size
                pnl_btc = (1 - exit_btc / entry_btc) * trade_size
            pnl = pnl_eth + pnl_btc - trade_size * 2 * COMM_CRYPTO * 2
            trades.append({"date": str(ci[i].date()), "pnl": pnl})
        return trades

    r = wf_test(ethbtc_mr, btc_eb, n_windows=8, label="ETH/BTC Ratio MR")
    all_results.append(print_wf(r, "ETH/BTC Ratio Mean Reversion (8 ans)"))

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 70)
    print("  FINAL SUMMARY — LONG PERIOD WF")
    print("=" * 70)
    print(f"{'Strategy':<40} {'Trades':>6} {'PnL':>10} {'WR':>6} {'Sharpe':>7} {'WF':>6} {'Verdict':>8}")
    print("-" * 85)
    for r in all_results:
        print(f"{r['label']:<40} {r['n']:>6} ${r['pnl']:>+9,.0f} {r['wr']:>5.0%} {r['sharpe']:>7.2f} {r['wf']:>6} {r['verdict']:>8}")


if __name__ == "__main__":
    main()
