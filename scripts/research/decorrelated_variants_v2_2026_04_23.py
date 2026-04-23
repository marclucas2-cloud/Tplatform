"""Iteration v2: add smart filters + rehab candidates.

Variants tested:
  1. mes_mr_vix_spike   — MES 3day stretch LONG-ONLY + VIX > 18 filter
  2. mgc_long_risk_off  — MGC long-only when VIX RSI > 55 (no shorts)
  3. mcl_overnight_mon  — Monday-only MCL long (reexam paper sleeve)
  4. alt_relmom_with_dd_stop — alt rel strength + portfolio DD stop
  5. mes_post_down_week — MES long after -2% down week (weekly MR)
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


def load_futures_long(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_FUT / f"{sym}_LONG.parquet").copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_vix_1d() -> pd.DataFrame:
    df = pd.read_parquet(DATA_FUT / "VIX_1D.parquet").copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_crypto_1d(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_CRYPTO / f"{sym}_1d.parquet").copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(None)
    return df.set_index("timestamp").sort_index()


def compute_metrics(r: pd.Series, n_trades=None) -> dict:
    r = r.dropna()
    if len(r) < 50:
        return {"n_days": len(r), "error": "too_few"}
    mu, sd = r.mean(), r.std()
    sharpe = (mu / sd) * np.sqrt(252) if sd > 0 else 0
    total = (1 + r).prod() - 1
    n_years = len(r) / 252
    cagr = (1 + total) ** (1 / n_years) - 1 if n_years > 0 else 0
    cum = (1 + r).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    down = r[r < 0]
    sortino = (mu / down.std()) * np.sqrt(252) if len(down) > 1 and down.std() > 0 else 0
    return {
        "n_days": int(len(r)), "n_years": round(n_years, 2),
        "sharpe": round(sharpe, 3), "sortino": round(sortino, 3),
        "cagr_pct": round(cagr * 100, 2), "total_pct": round(total * 100, 2),
        "max_dd_pct": round(dd * 100, 2),
        "hit_rate": round((r > 0).sum() / ((r != 0).sum() or 1), 3),
        "n_trades": n_trades,
        "calmar": round(cagr / abs(dd), 2) if dd < 0 else 0,
    }


def wf_5splits(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 500:
        return {"validated": False, "reason": "too_short", "n_splits": 0}
    step = len(r) // 6
    results = []
    for i in range(1, 6):
        oos = r.iloc[i * step:min((i + 1) * step, len(r))]
        if len(oos) < 50:
            continue
        s = (oos.mean() / oos.std()) * np.sqrt(252) if oos.std() > 0 else 0
        pnl = oos.sum()
        results.append({"w": i, "sharpe": round(s, 2), "pnl_pct": round(pnl * 100, 2), "profit": pnl > 0})
    profit = sum(1 for w in results if w["profit"])
    return {
        "n_splits": len(results),
        "profitable": profit,
        "ratio": round(profit / len(results), 2) if results else 0,
        "validated": profit / len(results) >= 0.5 if results else False,
        "windows": results,
    }


def is_oos(r: pd.Series) -> tuple:
    n = len(r.dropna())
    split = int(n * 0.7)
    rd = r.dropna()
    return rd.iloc[:split], rd.iloc[split:]


# ============================================================================
# Variant 1: MES 3day stretch LONG-ONLY + VIX>18 filter
# ============================================================================

def v1_mes_mr_vix_spike(mes: pd.DataFrame, vix: pd.DataFrame,
                        consec: int = 3, hold: int = 3,
                        vix_min: float = 18.0, comm: float = 0.62,
                        slip_ticks: float = 1.0, tick_val: float = 1.25) -> pd.Series:
    df = mes.copy()
    common = df.index.intersection(vix.index)
    df = df.loc[common]
    vix_c = vix.loc[common, "close"]

    df["is_dn"] = df["close"] < df["open"]
    dn_streak = df["is_dn"].rolling(consec).sum()

    signal = pd.Series(0.0, index=df.index)
    # LONG only: 3 down days AND VIX > 18
    signal[(dn_streak >= consec) & (vix_c > vix_min)] = 1

    pos = pd.Series(0.0, index=df.index)
    i, n, trades = 0, len(df), 0
    while i < n - 1:
        if signal.iloc[i] > 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = 1.0
            trades += 1
            i = end + 1
        else:
            i += 1

    px_ret = df["close"].pct_change()
    strat = pos.shift(1) * px_ret
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip_ticks * tick_val) / df["close"])
    net = strat - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# Variant 2: MGC long-only + VIX RSI positive momentum (fear rising = gold bid)
# ============================================================================

def v2_mgc_long_risk_off(mgc: pd.DataFrame, vix: pd.DataFrame,
                         vix_rsi_p: int = 14, vix_rsi_th: float = 55,
                         hold: int = 10, comm: float = 0.62) -> pd.Series:
    mgc_c = mgc["close"]
    vix_c = vix["close"]
    delta = vix_c.diff()
    gain = delta.clip(lower=0).rolling(vix_rsi_p).mean()
    loss = (-delta.clip(upper=0)).rolling(vix_rsi_p).mean()
    rs = gain / loss
    vix_rsi = 100 - (100 / (1 + rs))

    # Additional MGC trend up filter: MGC > SMA50
    mgc_sma50 = mgc_c.rolling(50).mean()

    common = mgc.index.intersection(vix.index)
    mgc_c = mgc_c.loc[common]
    mgc_sma50 = mgc_sma50.loc[common]
    vix_rsi = vix_rsi.loc[common]

    signal = pd.Series(0.0, index=common)
    # LONG only: VIX RSI rising + Gold trending up
    signal[(vix_rsi > vix_rsi_th) & (mgc_c > mgc_sma50)] = 1

    pos = pd.Series(0.0, index=common)
    i, n, trades = 0, len(common), 0
    while i < n - 1:
        if signal.iloc[i] > 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = 1.0
            trades += 1
            i = end + 1
        else:
            i += 1
    px_ret = mgc_c.pct_change()
    strat = pos.shift(1) * px_ret
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + 1.0) / mgc_c)
    net = strat - cost
    net.attrs["trades"] = trades
    return net


# ============================================================================
# Variant 3: MCL Overnight Monday-only long (rehab existing paper sleeve)
# Rule: buy MCL at Monday close, sell Tuesday close (day-of-week effect)
# ============================================================================

def v3_mcl_mon_overnight(mcl: pd.DataFrame, comm: float = 0.62,
                          slip_ticks: float = 1.0, tick_val: float = 10.0,
                          trend_filter: bool = True, sma_p: int = 10) -> pd.Series:
    """MCL long close-to-close Mon->Tue.
    Enter Monday close, exit Tuesday close. Add SMA10 trend filter.
    """
    df = mcl.copy()
    df["dow"] = df.index.dayofweek  # 0=Mon
    df["close"] = df["close"]
    close = df["close"]
    sma = close.rolling(sma_p).mean() if trend_filter else None

    # Long signal on Monday (enter at Mon close, held into Tue)
    sig = pd.Series(0.0, index=df.index)
    mask = df["dow"] == 0
    if trend_filter:
        mask &= close > sma
    sig[mask] = 1.0

    # Position: Tuesday = signal from Monday, else 0
    pos = sig.shift(1).fillna(0.0)

    px_ret = close.pct_change()
    strat = pos * px_ret
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip_ticks * tick_val) / close)
    net = strat - cost
    net.attrs["trades"] = int((sig > 0).sum())
    return net


# ============================================================================
# Variant 4: alt_rel_strength with portfolio DD stop (cash when DD >-15%)
# ============================================================================

def v4_alt_relmom_dd_stop(universe: list[str], lookback: int = 14,
                           rebal: int = 7, top_n: int = 2,
                           dd_stop_pct: float = -0.20,
                           btc_trend: bool = True) -> pd.Series:
    prices = {}
    for c in universe + ["BTCUSDT"]:
        try:
            prices[c] = load_crypto_1d(c)["close"]
        except Exception:
            continue
    all_px = pd.DataFrame(prices).dropna()
    rets = all_px.pct_change()
    alts = [c for c in universe if c in all_px.columns]

    cum_lb = (1 + rets[alts]).rolling(lookback).apply(np.prod, raw=True) - 1
    btc_cum_lb = (1 + rets["BTCUSDT"]).rolling(lookback).apply(np.prod, raw=True) - 1
    rel_score = cum_lb.sub(btc_cum_lb, axis=0)

    btc_sma = all_px["BTCUSDT"].rolling(20).mean()
    btc_up = all_px["BTCUSDT"] > btc_sma

    pos = pd.DataFrame(0.0, index=all_px.index, columns=alts)
    port_equity = pd.Series(1.0, index=all_px.index)
    peak = 1.0
    last_rebal = None

    for i, dt in enumerate(all_px.index):
        if i < lookback + 20:
            continue
        curr_eq = port_equity.iloc[i - 1] if i > 0 else 1.0
        peak = max(peak, curr_eq)
        dd = curr_eq / peak - 1

        if dd < dd_stop_pct:
            pos.iloc[i] = 0.0
            continue
        do_rebal = last_rebal is None or (i - last_rebal) >= rebal
        if not do_rebal:
            if i > 0:
                pos.iloc[i] = pos.iloc[i - 1]
            continue
        last_rebal = i
        if btc_trend and not btc_up.iloc[i]:
            pos.iloc[i] = 0.0
            continue
        scores = rel_score.iloc[i].dropna()
        if scores.empty or (scores <= 0).all():
            pos.iloc[i] = 0.0
            continue
        top = scores.nlargest(top_n).index
        pos.iloc[i] = 0.0
        pos.loc[dt, top] = 1.0 / top_n

        # update equity for next iter
        if i < len(all_px) - 1:
            pr = (pos.iloc[i] * rets[alts].iloc[i + 1]).sum()
            flips_i = (pos.iloc[i] - pos.iloc[i - 1]).abs().sum() if i > 0 else 0
            cost_i = flips_i * 10e-4
            port_equity.iloc[i + 1] = curr_eq * (1 + pr - cost_i)

    port_ret = (pos.shift(1) * rets[alts]).sum(axis=1)
    flips = pos.diff().abs().sum(axis=1).fillna(0)
    net = port_ret - flips * 10e-4
    net.attrs["trades"] = int((flips > 0).sum())
    return net


# ============================================================================
# Variant 5: MES post-down-week long (weekly MR)
# ============================================================================

def v5_mes_down_week_mr(mes: pd.DataFrame, week_ret_th: float = -0.02,
                        hold: int = 3, comm: float = 0.62,
                        slip_ticks: float = 1.0, tick_val: float = 1.25) -> pd.Series:
    """On Monday, if prior week return <= -2%, long MES for 'hold' days.
    """
    df = mes.copy()
    df["dow"] = df.index.dayofweek
    close = df["close"]
    # Weekly ret: Friday close to Friday close (approx: 5 bar pct change)
    w_ret = close.pct_change(5)
    sig = pd.Series(0.0, index=df.index)
    # Monday = 0. Entry at Monday close based on previous week ret
    mask = (df["dow"] == 0) & (w_ret <= week_ret_th)
    sig[mask] = 1.0

    pos = pd.Series(0.0, index=df.index)
    i, n, trades = 0, len(df), 0
    while i < n - 1:
        if sig.iloc[i] > 0 and pos.iloc[i] == 0:
            end = min(i + 1 + hold, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = 1.0
            trades += 1
            i = end + 1
        else:
            i += 1
    px_ret = close.pct_change()
    strat = pos.shift(1) * px_ret
    pc = pos.diff().abs().fillna(0)
    cost = pc * ((comm * 2 + slip_ticks * tick_val) / close)
    net = strat - cost
    net.attrs["trades"] = trades
    return net


def proxy_cam() -> pd.Series:
    syms = ["MES", "MNQ", "M2K", "MGC", "MCL"]
    dfs = {s: load_futures_long(s) for s in syms}
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
            if sc.empty or sc.max() < 0.02:
                pos.iloc[i] = 0.0
            else:
                pos.iloc[i] = 0.0
                pos.loc[dt, sc.idxmax()] = 1.0
            last = i
        else:
            pos.iloc[i] = pos.iloc[i - 1]
    return (pos.shift(1) * rets).sum(axis=1)


def proxy_gor() -> pd.Series:
    mgc = load_futures_long("MGC")["close"]
    mcl = load_futures_long("MCL")["close"]
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


def main():
    print("=" * 80)
    print("DECORRELATED CANDIDATES v2 — smart filters + rehab")
    print("=" * 80)

    mes = load_futures_long("MES")
    mnq = load_futures_long("MNQ")
    mgc = load_futures_long("MGC")
    mcl = load_futures_long("MCL")
    vix = load_vix_1d()

    results = {}
    rets = {}

    print("\n[v1] mes_mr_vix_spike ...")
    v1 = v1_mes_mr_vix_spike(mes, vix)
    results["v1_mes_mr_vix_spike"] = {
        "full": compute_metrics(v1, v1.attrs.get("trades")),
        "is": compute_metrics(is_oos(v1)[0]),
        "oos": compute_metrics(is_oos(v1)[1]),
        "wf": wf_5splits(v1),
    }
    rets["v1_mes_mr_vix_spike"] = v1
    print(f"  {results['v1_mes_mr_vix_spike']['full']}")
    print(f"  WF: {results['v1_mes_mr_vix_spike']['wf']}")

    print("\n[v2] mgc_long_risk_off ...")
    v2 = v2_mgc_long_risk_off(mgc, vix)
    results["v2_mgc_long_risk_off"] = {
        "full": compute_metrics(v2, v2.attrs.get("trades")),
        "is": compute_metrics(is_oos(v2)[0]),
        "oos": compute_metrics(is_oos(v2)[1]),
        "wf": wf_5splits(v2),
    }
    rets["v2_mgc_long_risk_off"] = v2
    print(f"  {results['v2_mgc_long_risk_off']['full']}")
    print(f"  WF: {results['v2_mgc_long_risk_off']['wf']}")

    print("\n[v3] mcl_mon_overnight ...")
    v3 = v3_mcl_mon_overnight(mcl)
    results["v3_mcl_mon_overnight"] = {
        "full": compute_metrics(v3, v3.attrs.get("trades")),
        "is": compute_metrics(is_oos(v3)[0]),
        "oos": compute_metrics(is_oos(v3)[1]),
        "wf": wf_5splits(v3),
    }
    rets["v3_mcl_mon_overnight"] = v3
    print(f"  {results['v3_mcl_mon_overnight']['full']}")
    print(f"  WF: {results['v3_mcl_mon_overnight']['wf']}")

    print("\n[v4] alt_relmom_dd_stop ...")
    alt_u = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "ADAUSDT",
             "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "NEARUSDT", "XRPUSDT"]
    v4 = v4_alt_relmom_dd_stop(alt_u)
    results["v4_alt_relmom_dd_stop"] = {
        "full": compute_metrics(v4, v4.attrs.get("trades")),
        "is": compute_metrics(is_oos(v4)[0]),
        "oos": compute_metrics(is_oos(v4)[1]),
        "wf": wf_5splits(v4),
    }
    rets["v4_alt_relmom_dd_stop"] = v4
    print(f"  {results['v4_alt_relmom_dd_stop']['full']}")
    print(f"  WF: {results['v4_alt_relmom_dd_stop']['wf']}")

    print("\n[v5] mes_down_week_mr ...")
    v5 = v5_mes_down_week_mr(mes)
    results["v5_mes_down_week_mr"] = {
        "full": compute_metrics(v5, v5.attrs.get("trades")),
        "is": compute_metrics(is_oos(v5)[0]),
        "oos": compute_metrics(is_oos(v5)[1]),
        "wf": wf_5splits(v5),
    }
    rets["v5_mes_down_week_mr"] = v5
    print(f"  {results['v5_mes_down_week_mr']['full']}")
    print(f"  WF: {results['v5_mes_down_week_mr']['wf']}")

    print("\n[proxy] CAM + GOR ...")
    rets["_proxy_CAM"] = proxy_cam()
    rets["_proxy_GOR"] = proxy_gor()
    results["_proxy_CAM"] = {"full": compute_metrics(rets["_proxy_CAM"])}
    results["_proxy_GOR"] = {"full": compute_metrics(rets["_proxy_GOR"])}

    print("\n[correlation]")
    df = pd.DataFrame(rets).dropna(how="all")
    corr = df.corr(min_periods=100)
    print(corr.round(3))

    out_json = REPORT_DIR / "decorrelated_v2_2026-04-23_metrics.json"
    with out_json.open("w", encoding="utf-8") as f:
        json.dump({"metrics": results, "correlation": corr.to_dict()}, f, indent=2, default=str)

    out_parquet = REPORT_DIR / "decorrelated_v2_2026-04-23_returns.parquet"
    df.to_parquet(out_parquet)
    print(f"\nSaved: {out_json}")


if __name__ == "__main__":
    main()
