"""Iteration v3: 2 variantes supplementaires pour elargir panier.

v10 mes_3up_short_complacency  — short MES apres 3 up + VIX < 15 (fade complacence)
v11 mgc_mes_ratio_rotation     — rotation gold/equity via Z-score ratio (macro MR)
"""
from __future__ import annotations
import sys
sys.path.insert(0, '.')
from scripts.research.new_paper_candidates_2026_04_23 import (
    load_fut_long, load_fut_daily, metrics, wf_splits,
    proxy_cam, proxy_gor, proxy_btc_asia,
)
import numpy as np
import pandas as pd
import json
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "research"


def v10_mes_complacency_short(mes, vix, consec=3, vix_max=15.0,
                               hold=3, comm=0.62, slip=1.25) -> pd.Series:
    """SHORT MES after 3 consec up days AND VIX < 15 (complacency fade)."""
    common = mes.index.intersection(vix.index)
    m = mes.loc[common]
    v = vix.loc[common, "close"]
    m["is_up"] = m["close"] > m["open"]
    up_streak = m["is_up"].rolling(consec).sum()
    sig = pd.Series(0.0, index=common)
    sig[(up_streak >= consec) & (v < vix_max)] = -1  # short
    pos = pd.Series(0.0, index=common)
    i, n, trades = 0, len(common), 0
    while i < n - 1:
        if sig.iloc[i] < 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = -1.0
            trades += 1
            i = end + 1
        else:
            i += 1
    ret = pos.shift(1) * m["close"].pct_change()
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip) / m["close"])
    net = ret - cost
    net.attrs["trades"] = trades
    return net


def v11_mgc_mes_ratio(mgc, mes, lookback=30, z_entry=1.5, z_exit=0.3,
                      z_stop=3.0, max_hold=20, comm=0.62) -> pd.Series:
    """MR sur ratio MGC/MES (gold/equity).
    Z > +entry : MGC over vs MES -> LONG MES (bet equity catch-up)
    Z < -entry : MES over vs MGC -> LONG MGC (bet gold catch-up)
    Long-only each leg, alternate instrument.
    """
    common = mgc.index.intersection(mes.index)
    mgc_c = mgc.loc[common, "close"]
    mes_c = mes.loc[common, "close"]
    ratio = np.log(mgc_c) - np.log(mes_c)
    sma = ratio.rolling(lookback).mean()
    sd = ratio.rolling(lookback).std()
    z = (ratio - sma) / sd

    pos = pd.Series(0.0, index=common)  # 1=long MGC, -1=long MES
    state = 0
    hold = 0
    trades = 0
    for i in range(len(common)):
        if state == 0:
            if z.iloc[i] <= -z_entry:
                state, hold = 1, 0  # gold catch-up -> long MGC
                trades += 1
            elif z.iloc[i] >= z_entry:
                state, hold = -1, 0  # equity catch-up -> long MES
                trades += 1
        else:
            hold += 1
            if (abs(z.iloc[i]) < z_exit or abs(z.iloc[i]) > z_stop
                    or hold > max_hold):
                state = 0
        pos.iloc[i] = state

    mgc_r = mgc_c.pct_change()
    mes_r = mes_c.pct_change()
    prev = pos.shift(1).fillna(0)
    strat = pd.Series(0.0, index=common)
    strat[prev == 1] = mgc_r[prev == 1]
    strat[prev == -1] = mes_r[prev == -1]
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + 1.0) / mgc_c)
    net = strat - cost
    net.attrs["trades"] = trades
    return net


def main():
    print("=" * 80)
    print("NEW PAPER v3 2026-04-23 — 2 variantes rapid")
    print("=" * 80)
    mes = load_fut_long("MES")
    mgc = load_fut_long("MGC")
    vix = load_fut_daily("VIX")

    res = {}
    rets = {}

    print("\n[v10] mes_complacency_short ...")
    v10 = v10_mes_complacency_short(mes, vix)
    res["v10_mes_complacency_short"] = {"full": metrics(v10, v10.attrs.get("trades")),
                                          "wf": wf_splits(v10)}
    rets["v10_mes_complacency_short"] = v10
    print(f"  {res['v10_mes_complacency_short']}")

    print("\n[v11] mgc_mes_ratio ...")
    v11 = v11_mgc_mes_ratio(mgc, mes)
    res["v11_mgc_mes_ratio"] = {"full": metrics(v11, v11.attrs.get("trades")),
                                 "wf": wf_splits(v11)}
    rets["v11_mgc_mes_ratio"] = v11
    print(f"  {res['v11_mgc_mes_ratio']}")

    rets["_proxy_CAM"] = proxy_cam()
    rets["_proxy_GOR"] = proxy_gor()
    rets["_proxy_btc_asia"] = proxy_btc_asia()

    df = pd.DataFrame(rets).dropna(how="all")
    corr = df.corr(min_periods=60)
    print("\n[correlation]")
    print(corr.round(3))

    out = REPORT_DIR / "new_paper_v3_2026-04-23_metrics.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"metrics": res, "correlation": corr.to_dict()}, f, indent=2, default=str)
    df.to_parquet(REPORT_DIR / "new_paper_v3_2026-04-23_returns.parquet")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
