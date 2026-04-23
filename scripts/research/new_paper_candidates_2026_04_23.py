"""New paper candidates research 2026-04-23 (afternoon mission).

6 candidats seriuex (reduits de 12 idees initiales):
  1. mes_estx50_divergence    — US-EU intermarket reversal (stat arb)
  2. m2k_weekly_trend         — Russell small-cap weekly trend + VIX filter
  3. mcl_mgc_ratio_rotation   — MGC/MCL Z-score rotation (different from GOR momentum)
  4. mgc_rsi_pullback         — MGC MR dans trend haussier (pullback long)
  5. eth_btc_rotation         — Long ETH quand ETH/BTC ratio trend up
  6. alt_oversold_bounce      — Long alt oversold 7d + BTC trend up filter

Framework: anti-lookahead strict, costs realistes, WF 5 splits, corr desk.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA_FUT = ROOT / "data" / "futures"
DATA_CRYPTO = ROOT / "data" / "crypto" / "candles"
REPORT_DIR = ROOT / "reports" / "research"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_fut_long(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_FUT / f"{sym}_LONG.parquet").copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index = pd.to_datetime(df.index).normalize()
    return df[~df.index.duplicated(keep="last")].sort_index()


def load_fut_daily(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_FUT / f"{sym}_1D.parquet").copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index = pd.to_datetime(df.index).normalize()
    return df[~df.index.duplicated(keep="last")].sort_index()


def load_crypto(sym: str, tf: str = "1d") -> pd.DataFrame:
    df = pd.read_parquet(DATA_CRYPTO / f"{sym}_{tf}.parquet").copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(None)
    return df.set_index("timestamp").sort_index()


def metrics(r: pd.Series, n_trades=None) -> dict:
    r = r.dropna()
    if len(r) < 50:
        return {"n_days": len(r), "error": "too_few"}
    mu, sd = r.mean(), r.std()
    sharpe = (mu / sd) * np.sqrt(252) if sd > 0 else 0
    total = (1 + r).prod() - 1
    ny = len(r) / 252
    cagr = (1 + total) ** (1 / ny) - 1 if ny > 0 else 0
    cum = (1 + r).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    down = r[r < 0]
    sortino = (mu / down.std()) * np.sqrt(252) if len(down) > 1 and down.std() > 0 else 0
    return {
        "n_days": int(len(r)), "n_years": round(ny, 2),
        "sharpe": round(float(sharpe), 3), "sortino": round(float(sortino), 3),
        "cagr_pct": round(cagr * 100, 2), "total_pct": round(total * 100, 2),
        "max_dd_pct": round(dd * 100, 2),
        "hit_rate": round((r > 0).sum() / ((r != 0).sum() or 1), 3),
        "n_trades": n_trades,
        "calmar": round(cagr / abs(dd), 2) if dd < 0 else 0,
    }


def wf_splits(r: pd.Series, n=5) -> dict:
    r = r.dropna()
    if len(r) < 300:
        return {"validated": False, "reason": "too_short", "n_splits": 0}
    step = len(r) // (n + 1)
    results = []
    for i in range(1, n + 1):
        oos = r.iloc[i * step:min((i + 1) * step, len(r))]
        if len(oos) < 30:
            continue
        s = (oos.mean() / oos.std()) * np.sqrt(252) if oos.std() > 0 else 0
        pnl = oos.sum()
        results.append({"w": i, "sharpe": round(float(s), 2),
                        "pnl_pct": round(pnl * 100, 2), "profit": pnl > 0})
    profit = sum(1 for w in results if w["profit"])
    return {
        "n_splits": len(results), "profitable": profit,
        "ratio": round(profit / len(results), 2) if results else 0,
        "validated": profit / len(results) >= 0.5 if results else False,
        "windows": results,
    }


def is_oos(r: pd.Series) -> tuple:
    rd = r.dropna()
    sp = int(len(rd) * 0.7)
    return rd.iloc[:sp], rd.iloc[sp:]


# ============================================================================
# CANDIDATE 1: mes_estx50_divergence — US-EU intermarket reversal
# ============================================================================
def c1_mes_estx50_divergence(mes: pd.DataFrame, estx50: pd.DataFrame,
                               lookback: int = 20, z_entry: float = 2.0,
                               z_exit: float = 0.5, max_hold: int = 10,
                               comm: float = 0.62, slip_pts: float = 1.25) -> pd.Series:
    """When MES/ESTX50 z-score extreme, bet on convergence.
    Z > +z_entry : ESTX50 oversold -> LONG ESTX50 only (paper tradable MIB/ESTX50)
    Z < -z_entry : MES oversold    -> LONG MES
    Hold until |Z| < z_exit or max_hold.
    """
    common = mes.index.intersection(estx50.index)
    mes_c = mes.loc[common, "close"]
    est_c = estx50.loc[common, "close"]
    spread = np.log(mes_c) - np.log(est_c)
    sma = spread.rolling(lookback).mean()
    sd = spread.rolling(lookback).std()
    z = (spread - sma) / sd

    pos_mes = pd.Series(0.0, index=common)
    state = 0  # 0=flat, 1=long MES, -1=long ESTX50
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
            if abs(z.iloc[i]) < z_exit or hold > max_hold:
                state = 0
        pos_mes.iloc[i] = state

    mes_ret = mes_c.pct_change()
    est_ret = est_c.pct_change()
    # state=1 long MES, state=-1 long ESTX50 (no short leg)
    strat_ret = pd.Series(0.0, index=common)
    prev_pos = pos_mes.shift(1).fillna(0)
    strat_ret[prev_pos == 1] = mes_ret[prev_pos == 1]
    strat_ret[prev_pos == -1] = est_ret[prev_pos == -1]

    pc = pos_mes.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip_pts) / mes_c)
    net = strat_ret - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# CANDIDATE 2: m2k_weekly_trend — Russell 2000 micro weekly momentum
# ============================================================================
def c2_m2k_weekly_trend(m2k: pd.DataFrame, vix: pd.DataFrame,
                         lookback: int = 20, momentum_th: float = 0.02,
                         vix_max: float = 28.0, hold: int = 10,
                         comm: float = 0.62, slip_pts: float = 0.50) -> pd.Series:
    """Long M2K if M2K cum return 20d > +2% AND VIX < 28.
    Exit after 10 days. Single sleeve, long only.
    """
    common = m2k.index.intersection(vix.index)
    m2k_c = m2k.loc[common, "close"]
    vix_c = vix.loc[common, "close"]
    lb_ret = m2k_c.pct_change(lookback)

    sig = pd.Series(0.0, index=common)
    sig[(lb_ret > momentum_th) & (vix_c < vix_max)] = 1

    pos = pd.Series(0.0, index=common)
    i = 0
    n = len(common)
    trades = 0
    while i < n - 1:
        if sig.iloc[i] > 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = 1.0
            trades += 1
            i = end + 1
        else:
            i += 1
    ret = pos.shift(1) * m2k_c.pct_change()
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip_pts) / m2k_c)
    net = ret - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# CANDIDATE 3: mcl_mgc_ratio_rotation — Z-score gold/oil rotation (vs momentum GOR)
# ============================================================================
def c3_mcl_mgc_ratio_z(mgc: pd.DataFrame, mcl: pd.DataFrame,
                        lookback: int = 30, z_entry: float = 1.5,
                        z_exit: float = 0.3, max_hold: int = 15,
                        comm: float = 0.62) -> pd.Series:
    """Ratio MGC/MCL mean reversion via Z-score.
    Z > +entry : MGC overvalued vs MCL -> LONG MCL
    Z < -entry : MCL overvalued vs MGC -> LONG MGC
    Different from GOR which uses momentum (lookback return).
    """
    common = mgc.index.intersection(mcl.index)
    mgc_c = mgc.loc[common, "close"]
    mcl_c = mcl.loc[common, "close"]
    ratio = np.log(mgc_c) - np.log(mcl_c)
    sma = ratio.rolling(lookback).mean()
    sd = ratio.rolling(lookback).std()
    z = (ratio - sma) / sd

    pos_long = pd.Series(0.0, index=common)  # 1=long MGC, -1=long MCL
    state = 0
    hold = 0
    trades = 0
    for i in range(len(common)):
        if state == 0:
            if z.iloc[i] <= -z_entry:
                state, hold = 1, 0  # long MGC
                trades += 1
            elif z.iloc[i] >= z_entry:
                state, hold = -1, 0  # long MCL
                trades += 1
        else:
            hold += 1
            if abs(z.iloc[i]) < z_exit or hold > max_hold:
                state = 0
        pos_long.iloc[i] = state

    mgc_r = mgc_c.pct_change()
    mcl_r = mcl_c.pct_change()
    strat = pd.Series(0.0, index=common)
    prev = pos_long.shift(1).fillna(0)
    strat[prev == 1] = mgc_r[prev == 1]
    strat[prev == -1] = mcl_r[prev == -1]
    pc = pos_long.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + 1.0) / mgc_c)  # approx
    net = strat - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# CANDIDATE 4: mgc_rsi_pullback — MR in bull trend (MGC)
# ============================================================================
def c4_mgc_rsi_pullback(mgc: pd.DataFrame, rsi_p: int = 14,
                         rsi_low: float = 35, sma_p: int = 50,
                         hold: int = 5, comm: float = 0.62) -> pd.Series:
    """Long MGC if: MGC > SMA50 (trend up) AND RSI(14) < 35 (short-term oversold).
    Hold 5 days. Exit on time.
    """
    c = mgc["close"]
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(rsi_p).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_p).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    sma = c.rolling(sma_p).mean()

    sig = pd.Series(0.0, index=c.index)
    sig[(c > sma) & (rsi < rsi_low)] = 1

    pos = pd.Series(0.0, index=c.index)
    i, n, trades = 0, len(c), 0
    while i < n - 1:
        if sig.iloc[i] > 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = 1.0
            trades += 1
            i = end + 1
        else:
            i += 1
    ret = pos.shift(1) * c.pct_change()
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + 1.0) / c)
    net = ret - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# CANDIDATE 5: eth_btc_rotation — Long ETH when ETH/BTC trends up
# ============================================================================
def c5_eth_btc_rotation(lookback: int = 14, rebal: int = 7,
                         min_strength: float = 0.0) -> pd.Series:
    """Long ETH if ETH/BTC ratio cum_return > min_strength over lookback.
    Rebalance every 'rebal' days. Else cash.
    Simple proxy alt rotation favoring ETH regime.
    """
    btc = load_crypto("BTCUSDT")["close"]
    eth = load_crypto("ETHUSDT")["close"]
    common = btc.index.intersection(eth.index)
    btc, eth = btc.loc[common], eth.loc[common]
    ratio = eth / btc
    ratio_ret = ratio.pct_change(lookback)

    pos_eth = pd.Series(0.0, index=common)
    last_rebal = None
    trades = 0
    for i, dt in enumerate(common):
        if i < lookback:
            continue
        do_rebal = last_rebal is None or (i - last_rebal) >= rebal
        if not do_rebal:
            pos_eth.iloc[i] = pos_eth.iloc[i - 1]
            continue
        last_rebal = i
        new_pos = 1.0 if ratio_ret.iloc[i] > min_strength else 0.0
        if new_pos != pos_eth.iloc[i - 1]:
            trades += 1
        pos_eth.iloc[i] = new_pos

    eth_r = eth.pct_change()
    strat = pos_eth.shift(1) * eth_r
    flips = pos_eth.diff().abs().fillna(0)
    cost = flips * 10e-4  # 10bps roundtrip
    net = strat - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# CANDIDATE 6: alt_oversold_bounce — contra MR alt after drawdown 7d
# ============================================================================
def c6_alt_oversold_bounce(universe: list[str], lookback: int = 7,
                            dd_threshold: float = -0.15, hold: int = 3,
                            btc_trend_filter: bool = True) -> pd.Series:
    """When alt cum_ret 7d <= -15% AND BTC > SMA20 (uptrend), long alt 3 days.
    Entries can be simultaneous on multiple alts, weights equal.
    """
    prices = {}
    for c in universe + ["BTCUSDT"]:
        try:
            prices[c] = load_crypto(c)["close"]
        except Exception:
            continue
    all_px = pd.DataFrame(prices).dropna()
    rets = all_px.pct_change()
    alts = [c for c in universe if c in all_px.columns]
    lb = (1 + rets[alts]).rolling(lookback).apply(np.prod, raw=True) - 1
    btc_sma = all_px["BTCUSDT"].rolling(20).mean()
    btc_up = all_px["BTCUSDT"] > btc_sma

    pos = pd.DataFrame(0.0, index=all_px.index, columns=alts)
    trades = 0
    for i in range(lookback + 20, len(all_px)):
        # carry prior if not expired
        if i > 0:
            pos.iloc[i] = pos.iloc[i - 1]
        # expire held positions after `hold` days (simple: check col-level)
        # Simpler: we only fire new entries at each bar, exit after hold counted separately.
        # Use rolling: pos[i] = pos[i-1] unless new signal or exit.
        # Exit logic: each column tracks its "days held" via entry date tracking.
        # For simplicity, at bar i, count how many bars ago position started.
        pass

    # Simpler implementation: signal array per alt, then simulate holding
    pos2 = pd.DataFrame(0.0, index=all_px.index, columns=alts)
    hold_counters = {a: 0 for a in alts}
    for i in range(lookback + 20, len(all_px)):
        dt = all_px.index[i]
        for a in alts:
            if hold_counters[a] > 0:
                pos2.iloc[i, pos2.columns.get_loc(a)] = 1.0
                hold_counters[a] -= 1
                continue
            if (btc_up.iloc[i] if btc_trend_filter else True) and lb.iloc[i][a] <= dd_threshold:
                pos2.iloc[i, pos2.columns.get_loc(a)] = 1.0
                hold_counters[a] = hold - 1  # already in today
                trades += 1
    # Normalize position weights (equal-weight active alts each day)
    active = pos2.sum(axis=1).replace(0, 1)
    weights = pos2.div(active, axis=0)
    port_ret = (weights.shift(1) * rets[alts]).sum(axis=1)
    flips = weights.diff().abs().sum(axis=1).fillna(0)
    cost = flips * 10e-4
    net = port_ret - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# Proxy existing desk strats
# ============================================================================
def proxy_cam() -> pd.Series:
    syms = ["MES", "MNQ", "M2K", "MGC", "MCL"]
    dfs = {s: load_fut_long(s) for s in syms}
    common = None
    for d in dfs.values():
        common = d.index if common is None else common.intersection(d.index)
    closes = pd.DataFrame({s: dfs[s].loc[common, "close"] for s in syms})
    rets = closes.pct_change()
    lb = (1 + rets).rolling(20).apply(np.prod, raw=True) - 1
    pos = pd.DataFrame(0.0, index=common, columns=syms)
    last = None
    for i, dt in enumerate(common):
        if i < 20:
            continue
        if last is None or (i - last) >= 20:
            sc = lb.iloc[i].dropna()
            pos.iloc[i] = 0.0
            if not sc.empty and sc.max() >= 0.02:
                pos.loc[dt, sc.idxmax()] = 1.0
            last = i
        else:
            pos.iloc[i] = pos.iloc[i - 1]
    return (pos.shift(1) * rets).sum(axis=1)


def proxy_gor() -> pd.Series:
    mgc = load_fut_long("MGC")["close"]
    mcl = load_fut_long("MCL")["close"]
    common = mgc.index.intersection(mcl.index)
    mgc, mcl = mgc.loc[common], mcl.loc[common]
    rets = pd.DataFrame({"MGC": mgc.pct_change(), "MCL": mcl.pct_change()})
    lb = (1 + rets).rolling(20).apply(np.prod, raw=True) - 1
    pos = pd.DataFrame(0.0, index=common, columns=["MGC", "MCL"])
    last = None
    for i, dt in enumerate(common):
        if i < 20:
            continue
        if last is None or (i - last) >= 20:
            pos.iloc[i] = 0.0
            pos.loc[dt, lb.iloc[i].idxmax()] = 1.0
            last = i
        else:
            pos.iloc[i] = pos.iloc[i - 1]
    return (pos.shift(1) * rets).sum(axis=1)


def proxy_btc_asia() -> pd.Series:
    """Simple proxy: long BTCUSDT overnight (close->next close) when prior MES up week."""
    btc = load_crypto("BTCUSDT")["close"]
    mes = load_fut_long("MES")["close"]
    common = btc.index.intersection(mes.index)
    btc_r = btc.loc[common].pct_change()
    mes_ret_w = mes.loc[common].pct_change(5)
    sig = pd.Series(0.0, index=common)
    sig[mes_ret_w.shift(1) > 0.005] = 1  # long if MES weekly > +0.5%
    return sig.shift(1) * btc_r


# ============================================================================
# Main
# ============================================================================
def main():
    print("=" * 85)
    print("NEW PAPER CANDIDATES 2026-04-23 afternoon — 6 seriuex candidates")
    print("=" * 85)

    mes = load_fut_long("MES")
    mnq = load_fut_long("MNQ")
    m2k = load_fut_long("M2K")
    mgc = load_fut_long("MGC")
    mcl = load_fut_long("MCL")
    estx50 = load_fut_daily("ESTX50")
    vix = load_fut_daily("VIX")

    results = {}
    rets = {}

    print("\n[c1] mes_estx50_divergence ...")
    c1 = c1_mes_estx50_divergence(mes, estx50)
    results["c1_mes_estx50_divergence"] = {"full": metrics(c1, c1.attrs.get("trades")),
                                            "wf": wf_splits(c1)}
    rets["c1_mes_estx50_divergence"] = c1
    print(f"  {results['c1_mes_estx50_divergence']}")

    print("\n[c2] m2k_weekly_trend ...")
    c2 = c2_m2k_weekly_trend(m2k, vix)
    results["c2_m2k_weekly_trend"] = {"full": metrics(c2, c2.attrs.get("trades")),
                                       "wf": wf_splits(c2)}
    rets["c2_m2k_weekly_trend"] = c2
    print(f"  {results['c2_m2k_weekly_trend']}")

    print("\n[c3] mcl_mgc_ratio_z ...")
    c3 = c3_mcl_mgc_ratio_z(mgc, mcl)
    results["c3_mcl_mgc_ratio_z"] = {"full": metrics(c3, c3.attrs.get("trades")),
                                      "wf": wf_splits(c3)}
    rets["c3_mcl_mgc_ratio_z"] = c3
    print(f"  {results['c3_mcl_mgc_ratio_z']}")

    print("\n[c4] mgc_rsi_pullback ...")
    c4 = c4_mgc_rsi_pullback(mgc)
    results["c4_mgc_rsi_pullback"] = {"full": metrics(c4, c4.attrs.get("trades")),
                                       "wf": wf_splits(c4)}
    rets["c4_mgc_rsi_pullback"] = c4
    print(f"  {results['c4_mgc_rsi_pullback']}")

    print("\n[c5] eth_btc_rotation ...")
    c5 = c5_eth_btc_rotation()
    results["c5_eth_btc_rotation"] = {"full": metrics(c5, c5.attrs.get("trades")),
                                       "wf": wf_splits(c5)}
    rets["c5_eth_btc_rotation"] = c5
    print(f"  {results['c5_eth_btc_rotation']}")

    print("\n[c6] alt_oversold_bounce ...")
    universe = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "ADAUSDT",
                "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "NEARUSDT", "XRPUSDT"]
    c6 = c6_alt_oversold_bounce(universe)
    results["c6_alt_oversold_bounce"] = {"full": metrics(c6, c6.attrs.get("trades")),
                                          "wf": wf_splits(c6)}
    rets["c6_alt_oversold_bounce"] = c6
    print(f"  {results['c6_alt_oversold_bounce']}")

    print("\n[proxy] CAM + GOR + btc_asia ...")
    rets["_proxy_CAM"] = proxy_cam()
    rets["_proxy_GOR"] = proxy_gor()
    rets["_proxy_btc_asia"] = proxy_btc_asia()

    df = pd.DataFrame(rets).dropna(how="all")
    corr = df.corr(min_periods=60)
    print("\n[correlation]")
    print(corr.round(3))

    out = REPORT_DIR / "new_paper_candidates_2026-04-23_metrics.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"metrics": results, "correlation": corr.to_dict()}, f,
                   indent=2, default=str)

    parquet = REPORT_DIR / "new_paper_candidates_2026-04-23_returns.parquet"
    df.to_parquet(parquet)
    print(f"\nSaved: {out}")
    print(f"Saved: {parquet}")


if __name__ == "__main__":
    main()
