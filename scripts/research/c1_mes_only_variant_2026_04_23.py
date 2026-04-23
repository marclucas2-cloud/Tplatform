"""c1 variant MES-only : simplifier pour runtime (pas de routing ESTX50).

Strategie: LONG MES quand log(MES/ESTX50) Z-score <= -z_entry (MES oversold
vs ESTX50). Pas d'ESTX50 long (plus simple, meme book ibkr_futures).
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


def c1_simplified_mes_only(mes, estx50, lookback=20, z_entry=2.0,
                            z_exit=0.5, max_hold=10, comm=0.62, slip=1.25):
    """LONG MES only when Z(log(MES/ESTX50)) <= -z_entry."""
    common = mes.index.intersection(estx50.index)
    mes_c = mes.loc[common, "close"]
    est_c = estx50.loc[common, "close"]
    spread = np.log(mes_c) - np.log(est_c)
    sma = spread.rolling(lookback).mean()
    sd = spread.rolling(lookback).std()
    z = (spread - sma) / sd

    pos = pd.Series(0.0, index=common)
    state, hold, trades = 0, 0, 0
    for i in range(len(common)):
        if state == 0:
            if z.iloc[i] <= -z_entry:
                state, hold = 1, 0
                trades += 1
        else:
            hold += 1
            if z.iloc[i] > -z_exit or hold > max_hold:
                state = 0
        pos.iloc[i] = state

    ret = pos.shift(1) * mes_c.pct_change()
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip) / mes_c)
    net = ret - cost
    net.attrs["trades"] = trades
    return net


def main():
    mes = load_fut_long("MES")
    estx50 = load_fut_daily("ESTX50")

    print("c1_simplified_mes_only (long MES only on Z<=-2):")
    r = c1_simplified_mes_only(mes, estx50)
    print(f"  {metrics(r, r.attrs.get('trades'))}")
    print(f"  WF: {wf_splits(r)}")

    # sensitivity
    print("\nSensitivity grid (lookback, z_entry, max_hold):")
    print(f"{'LB':>4}{'Z':>5}{'H':>4}  {'trades':>7}{'Sharpe':>8}{'CAGR':>7}{'DD':>7}{'WF':>6}")
    for lb in [15, 20, 25]:
        for ze in [1.5, 2.0, 2.5]:
            for mh in [5, 10, 15]:
                r = c1_simplified_mes_only(mes, estx50, lookback=lb, z_entry=ze, max_hold=mh)
                m = metrics(r, r.attrs.get("trades"))
                if m.get("error"):
                    continue
                wf = wf_splits(r)
                print(f"{lb:>4}{ze:>5.1f}{mh:>4}  {m.get('n_trades', '-'):>7}"
                      f"{m['sharpe']:>8.2f}{m['cagr_pct']:>7.1f}"
                      f"{m['max_dd_pct']:>7.1f}{wf['ratio']:>6.2f}")


if __name__ == "__main__":
    main()
