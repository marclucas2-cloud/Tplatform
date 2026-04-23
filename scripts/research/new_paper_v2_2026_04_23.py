"""Iteration v2: 3 more candidates to enrich shortlist.

v7 mes_high_vix_stressed_bounce — MES MR en regime VIX > 25 + MES -3% sur 5d
v8 gold_q4_seasonality        — MGC long octobre-decembre (saisonnalite gold)
v9 mes_m2k_pair_z_robust      — MES/M2K stat arb Z-score avec exit strict
"""
from __future__ import annotations
import sys
sys.path.insert(0, '.')
from scripts.research.new_paper_candidates_2026_04_23 import (
    load_fut_long, load_fut_daily, load_crypto, metrics, wf_splits, is_oos,
    proxy_cam, proxy_gor, proxy_btc_asia,
)
import numpy as np
import pandas as pd
import json
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "research"


# v7 — MES MR en regime VIX stressed
def v7_mes_stressed_bounce(mes, vix, vix_min=25.0, mes_dd_5d=-0.03,
                            hold=4, comm=0.62, slip_pts=1.25) -> pd.Series:
    common = mes.index.intersection(vix.index)
    mes_c = mes.loc[common, "close"]
    vix_c = vix.loc[common, "close"]
    ret_5d = mes_c.pct_change(5)
    sig = pd.Series(0.0, index=common)
    sig[(vix_c > vix_min) & (ret_5d <= mes_dd_5d)] = 1
    pos = pd.Series(0.0, index=common)
    i, n, trades = 0, len(common), 0
    while i < n - 1:
        if sig.iloc[i] > 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = 1.0
            trades += 1
            i = end + 1
        else:
            i += 1
    ret = pos.shift(1) * mes_c.pct_change()
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip_pts) / mes_c)
    net = ret - cost
    net.attrs["trades"] = trades
    return net


# v8 — Gold Q4 seasonality
def v8_gold_q4_seasonality(mgc, comm=0.62, slip=1.0) -> pd.Series:
    c = mgc["close"]
    mo = c.index.month
    # Long October (10), November (11), December (12)
    sig = pd.Series(0.0, index=c.index)
    sig[mo.isin([10, 11, 12])] = 1.0
    # Enter at month start, exit at month end -> position = sig (no shift for flat entries, but return applied t+1)
    pos = sig.copy()
    ret = pos.shift(1) * c.pct_change()
    # Cost applied on transitions (month boundaries)
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip) / c)
    net = ret - cost
    # n_trades = number of Q4 entries (sig transitions 0->1)
    entries = ((pos == 1) & (pos.shift(1) == 0)).sum()
    net.attrs["trades"] = int(entries)
    return net


# v9 — MES/M2K pair Z-score (small-cap vs large-cap)
def v9_mes_m2k_pair(mes, m2k, lookback=20, z_entry=2.0, z_exit=0.5,
                     z_stop=4.0, max_hold=10, comm=0.62) -> pd.Series:
    """Long MES short M2K if Z<-entry (MES oversold vs M2K).
    Exit when |Z|<exit OR |Z|>stop OR max_hold.
    Z_stop tighter than c3 to avoid blow-up.
    """
    common = mes.index.intersection(m2k.index)
    mes_c = mes.loc[common, "close"]
    m2k_c = m2k.loc[common, "close"]
    spread = np.log(mes_c) - np.log(m2k_c)
    sma = spread.rolling(lookback).mean()
    sd = spread.rolling(lookback).std()
    z = (spread - sma) / sd

    pos = pd.Series(0.0, index=common)
    state = 0
    hold = 0
    trades = 0
    for i in range(len(common)):
        if state == 0:
            if z.iloc[i] <= -z_entry:
                state, hold = 1, 0
                trades += 1
            elif z.iloc[i] >= z_entry:
                state, hold = -1, 0
                trades += 1
        else:
            hold += 1
            exit_flag = False
            if abs(z.iloc[i]) < z_exit:
                exit_flag = True
            elif abs(z.iloc[i]) > z_stop:
                exit_flag = True
            elif hold > max_hold:
                exit_flag = True
            if exit_flag:
                state = 0
        pos.iloc[i] = state

    mes_r = mes_c.pct_change()
    m2k_r = m2k_c.pct_change()
    pair_ret = mes_r - m2k_r
    strat = pos.shift(1) * pair_ret
    pc = pos.diff().abs().fillna(0)
    # cost: 2 instruments * cost_each
    cost_pct = pc * ((comm * 4 + 1.25 + 0.5) / mes_c)
    net = strat - cost_pct
    net.attrs["trades"] = trades
    return net


def main():
    print("=" * 80)
    print("NEW PAPER v2 2026-04-23 — 3 variantes additionnelles")
    print("=" * 80)
    mes = load_fut_long("MES")
    m2k = load_fut_long("M2K")
    mgc = load_fut_long("MGC")
    vix = load_fut_daily("VIX")

    res = {}
    rets = {}

    print("\n[v7] mes_stressed_bounce ...")
    v7 = v7_mes_stressed_bounce(mes, vix)
    res["v7_mes_stressed_bounce"] = {"full": metrics(v7, v7.attrs.get("trades")),
                                      "wf": wf_splits(v7)}
    rets["v7_mes_stressed_bounce"] = v7
    print(f"  {res['v7_mes_stressed_bounce']}")

    print("\n[v8] gold_q4_seasonality ...")
    v8 = v8_gold_q4_seasonality(mgc)
    res["v8_gold_q4_seasonality"] = {"full": metrics(v8, v8.attrs.get("trades")),
                                      "wf": wf_splits(v8)}
    rets["v8_gold_q4_seasonality"] = v8
    print(f"  {res['v8_gold_q4_seasonality']}")

    print("\n[v9] mes_m2k_pair ...")
    v9 = v9_mes_m2k_pair(mes, m2k)
    res["v9_mes_m2k_pair"] = {"full": metrics(v9, v9.attrs.get("trades")),
                               "wf": wf_splits(v9)}
    rets["v9_mes_m2k_pair"] = v9
    print(f"  {res['v9_mes_m2k_pair']}")

    # proxies
    rets["_proxy_CAM"] = proxy_cam()
    rets["_proxy_GOR"] = proxy_gor()
    rets["_proxy_btc_asia"] = proxy_btc_asia()

    df = pd.DataFrame(rets).dropna(how="all")
    corr = df.corr(min_periods=60)
    print("\n[correlation]")
    print(corr.round(3))

    out = REPORT_DIR / "new_paper_v2_2026-04-23_metrics.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"metrics": res, "correlation": corr.to_dict()}, f, indent=2, default=str)
    df.to_parquet(REPORT_DIR / "new_paper_v2_2026-04-23_returns.parquet")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
