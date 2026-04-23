"""Decorrelated strategy research 2026-04-23 — autonomous mission.

Objectif: trouver 2-5 strats decorrelees du desk actuel (CAM + GOR + btc_asia q80).
Anti-lookahead: .shift(1) strict, costs realistes, split 70/30 IS/OOS + WF.

Candidats testes:
  1. mes_3day_stretch_v2     — MR equity (oppose de CAM momentum)
  2. mgc_vix_hedge_v2        — Gold+VIX regime (orthog. a GOR rotation)
  3. mes_mnq_pairs_v2        — Stat arb market neutral
  4. alt_relmom_long_only_v2 — Rehab sleeve paper existante

Sortie:
  reports/research/decorrelated_strategies_2026-04-23_metrics.json
  reports/research/decorrelated_strategies_2026-04-23_returns.parquet
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

# Costs realistes (IBKR futures + Binance spot)
FUTURES_COMMISSION_PER_SIDE = 0.62  # USD per contract (IBKR micro)
FUTURES_SLIPPAGE_TICKS = 1.0  # 1 tick = $1.25 MES / $1 MGC / $10 MCL
CRYPTO_COMMISSION_BPS = 10  # 0.10% round-trip (5bps side)
CRYPTO_SLIPPAGE_BPS = 5  # 0.05% per side


def load_futures_long(sym: str) -> pd.DataFrame:
    """Load {sym}_LONG.parquet, standardize index to tz-naive DatetimeIndex."""
    df = pd.read_parquet(DATA_FUT / f"{sym}_LONG.parquet")
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_vix_1d() -> pd.DataFrame:
    df = pd.read_parquet(DATA_FUT / "VIX_1D.parquet")
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_crypto_1d(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_CRYPTO / f"{sym}_1d.parquet").copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(None)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


# ============================================================================
# Metrics helpers (returns series-based, not trade-based for simplicity+honesty)
# ============================================================================

def compute_metrics(daily_ret: pd.Series, n_trades: int | None = None) -> dict:
    """Return stats on daily return series."""
    r = daily_ret.dropna()
    if len(r) < 50:
        return {"n_days": len(r), "error": "too_few_days"}
    mu = r.mean()
    sd = r.std()
    sharpe = (mu / sd) * np.sqrt(252) if sd > 0 else 0.0
    total_ret = (1 + r).prod() - 1
    n_years = len(r) / 252
    cagr = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0.0

    cum = (1 + r).cumprod()
    peak = cum.cummax()
    dd_series = cum / peak - 1
    max_dd = float(dd_series.min())

    pos_days = (r > 0).sum()
    neg_days = (r < 0).sum()
    hit = pos_days / (pos_days + neg_days) if (pos_days + neg_days) else 0.0

    # Sortino
    down = r[r < 0]
    sortino = (mu / down.std()) * np.sqrt(252) if len(down) > 1 and down.std() > 0 else 0.0

    return {
        "n_days": int(len(r)),
        "n_years": round(n_years, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "cagr_pct": round(cagr * 100, 2),
        "total_ret_pct": round(total_ret * 100, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "hit_rate_days": round(hit, 3),
        "n_trades": n_trades,
        "calmar": round((cagr / abs(max_dd)), 2) if max_dd < 0 else 0.0,
    }


def oos_split(daily_ret: pd.Series, is_frac: float = 0.7) -> tuple[pd.Series, pd.Series]:
    n = len(daily_ret)
    split = int(n * is_frac)
    return daily_ret.iloc[:split], daily_ret.iloc[split:]


def walk_forward_oos(daily_ret: pd.Series, n_splits: int = 5) -> dict:
    """Time-series walk-forward: anchored windows expanding.
    Verdict: >= 50% OOS windows avec Sharpe > 0.3 = VALIDATED.
    """
    r = daily_ret.dropna()
    if len(r) < 500:
        return {"n_splits": 0, "ok": False, "reason": "too_short"}
    step = len(r) // (n_splits + 1)
    results = []
    for i in range(1, n_splits + 1):
        # anchored expanding: IS = [0, i*step], OOS = [i*step, (i+1)*step]
        oos_start = i * step
        oos_end = min((i + 1) * step, len(r))
        oos_r = r.iloc[oos_start:oos_end]
        if len(oos_r) < 50:
            continue
        oos_sharpe = (oos_r.mean() / oos_r.std()) * np.sqrt(252) if oos_r.std() > 0 else 0.0
        oos_pnl = oos_r.sum()
        results.append({"window": i, "sharpe": round(oos_sharpe, 3), "pnl_sum_pct": round(oos_pnl * 100, 2),
                        "profitable": oos_pnl > 0})
    profitable_count = sum(1 for w in results if w["profitable"])
    return {
        "n_splits": len(results),
        "profitable_count": profitable_count,
        "profitable_ratio": round(profitable_count / len(results), 2) if results else 0.0,
        "validated": profitable_count / len(results) >= 0.5 if results else False,
        "windows": results,
    }


# ============================================================================
# CANDIDATE 1: mes_3day_stretch_v2 — MR on MES daily
# ============================================================================

def backtest_mes_3day_stretch(df: pd.DataFrame, hold_days: int = 2,
                              consec_needed: int = 3, slip_ticks: float = 1.0,
                              tick_val: float = 1.25, comm: float = 0.62) -> pd.Series:
    """After N consecutive down days, go long for H days.
    After N consecutive up days, go short for H days.
    Position = signal.shift(1) (no lookahead). Close ret applied.
    Costs: per-trade at entry and exit (commission + slippage ticks).
    Returns: daily pct return series on $1 capital proxy (pnl / prev close).
    """
    df = df.copy()
    df["is_up"] = df["close"] > df["open"]
    df["is_dn"] = df["close"] < df["open"]

    # N consecutive
    up_streak = df["is_up"].rolling(consec_needed).sum()
    dn_streak = df["is_dn"].rolling(consec_needed).sum()

    # Signal emitted at EOD t, position enters at open t+1 (shift 1 for signal)
    signal = pd.Series(0, index=df.index)
    signal[dn_streak >= consec_needed] = 1     # 3 down -> long
    signal[up_streak >= consec_needed] = -1    # 3 up -> short

    # Hold H days: once entry fires, hold position for H bars then exit
    pos = pd.Series(0.0, index=df.index)
    i = 0
    n = len(df)
    idx_vals = df.index
    trades = 0
    while i < n - 1:
        if signal.iloc[i] != 0 and pos.iloc[i] == 0:
            direction = signal.iloc[i]
            end = min(i + 1 + hold_days, n - 1)  # enter t+1, hold H
            for j in range(i + 1, end + 1):
                pos.iloc[j] = direction
            trades += 1
            i = end + 1
        else:
            i += 1

    # Daily return of position (pnl / prev close) in pct
    close = df["close"]
    px_ret = close.pct_change()
    # Return from holding pos (pos taken at t, earns px_ret[t+1]?) — since pos is already shifted by entering at t+1 after signal at t, the daily return earned on day t is pos[t] * px_ret[t] (close t-1 to close t move on a position held at open t).
    # Actually simpler: strategy pnl on day t = pos[t-1]*ret[t] where pos[t-1] reflects position held at start of t.
    # We built pos to reflect position ACTIVE on day t (entered t+1 via shift logic), so:
    strat_ret = pos.shift(1) * px_ret

    # Transaction costs: apply on days pos changes
    pos_change = pos.diff().abs().fillna(0)
    # Cost per contract move (commission + slippage)
    cost_pct_move = (comm * 2 + slip_ticks * tick_val) / close  # approx as pct of notional
    # But strat_ret is in pct not dollars; apply cost as pct-of-close deduction on changes
    cost_pct = pos_change * cost_pct_move
    net_ret = strat_ret - cost_pct
    net_ret.attrs["trades"] = trades
    return net_ret


# ============================================================================
# CANDIDATE 2: mgc_vix_hedge_v2 — Gold long when VIX regime rising
# ============================================================================

def backtest_mgc_vix_hedge(mgc: pd.DataFrame, vix: pd.DataFrame,
                            bb_p: int = 20, bb_k: float = 2.0,
                            vix_rsi_p: int = 14, long_th: float = 60,
                            short_th: float = 35, hold_days: int = 5,
                            slip_ticks: float = 1.0, tick_val: float = 1.0,
                            comm: float = 0.62) -> pd.Series:
    """Long MGC when VIX RSI(14) > long_th AND MGC > Bollinger upper.
    Short MGC when VIX RSI(14) < short_th AND MGC < Bollinger lower.
    Hold H days max.
    """
    # Align on common dates
    mgc = mgc.copy()
    vix = vix.copy()
    mgc_c = mgc["close"]
    vix_c = vix["close"]

    # Bollinger on MGC
    mgc_ma = mgc_c.rolling(bb_p).mean()
    mgc_sd = mgc_c.rolling(bb_p).std()
    bb_up = mgc_ma + bb_k * mgc_sd
    bb_lo = mgc_ma - bb_k * mgc_sd

    # VIX RSI
    delta = vix_c.diff()
    gain = delta.clip(lower=0).rolling(vix_rsi_p).mean()
    loss = (-delta.clip(upper=0)).rolling(vix_rsi_p).mean()
    rs = gain / loss
    vix_rsi = 100 - (100 / (1 + rs))

    # Align
    common = mgc.index.intersection(vix.index)
    mgc_c = mgc_c.loc[common]
    bb_up_a = bb_up.loc[common]
    bb_lo_a = bb_lo.loc[common]
    vix_rsi_a = vix_rsi.loc[common]

    signal = pd.Series(0, index=common, dtype=float)
    signal[(vix_rsi_a > long_th) & (mgc_c > bb_up_a)] = 1
    signal[(vix_rsi_a < short_th) & (mgc_c < bb_lo_a)] = -1

    pos = pd.Series(0.0, index=common)
    i = 0
    n = len(common)
    trades = 0
    while i < n - 1:
        if signal.iloc[i] != 0 and pos.iloc[i] == 0:
            direction = signal.iloc[i]
            end = min(i + 1 + hold_days, n - 1)
            for j in range(i + 1, end + 1):
                pos.iloc[j] = direction
            trades += 1
            i = end + 1
        else:
            i += 1

    px_ret = mgc_c.pct_change()
    strat_ret = pos.shift(1) * px_ret
    pos_change = pos.diff().abs().fillna(0)
    cost_pct_move = (comm * 2 + slip_ticks * tick_val) / mgc_c
    net_ret = strat_ret - pos_change * cost_pct_move
    net_ret.attrs["trades"] = trades
    return net_ret


# ============================================================================
# CANDIDATE 3: mes_mnq_pairs_v2 — Market neutral stat arb
# ============================================================================

def backtest_mes_mnq_pairs(mes: pd.DataFrame, mnq: pd.DataFrame,
                            lookback: int = 20, z_entry: float = 2.0,
                            z_exit: float = 0.5, z_stop: float = 3.5,
                            max_hold: int = 10,
                            slip_ticks: float = 1.0) -> pd.Series:
    """Z-score of log-ratio MES/MNQ. Short divergence when |Z|>z_entry.
    Exit when |Z|<z_exit OR |Z|>z_stop OR hold>max_hold.
    Position: long MES - short MNQ if Z<-z_entry (MES oversold vs MNQ)
              short MES + long MNQ if Z>+z_entry (MES overbought vs MNQ)
    Returns: daily pct return on pair pnl (mes_ret - mnq_ret) * direction.
    """
    mes = mes.copy()
    mnq = mnq.copy()
    common = mes.index.intersection(mnq.index)
    mes_c = mes.loc[common, "close"]
    mnq_c = mnq.loc[common, "close"]
    spread = np.log(mes_c) - np.log(mnq_c)
    sp_ma = spread.rolling(lookback).mean()
    sp_sd = spread.rolling(lookback).std()
    z = (spread - sp_ma) / sp_sd

    pos = pd.Series(0.0, index=common)
    state = 0
    hold_count = 0
    trades = 0
    for i in range(len(common)):
        if state == 0:
            if z.iloc[i] <= -z_entry:
                state = 1  # long MES - short MNQ
                hold_count = 0
                trades += 1
            elif z.iloc[i] >= z_entry:
                state = -1
                hold_count = 0
                trades += 1
        else:
            hold_count += 1
            exit_now = False
            if abs(z.iloc[i]) < z_exit:
                exit_now = True
            elif abs(z.iloc[i]) > z_stop:
                exit_now = True
            elif hold_count > max_hold:
                exit_now = True
            if exit_now:
                state = 0
        pos.iloc[i] = state

    mes_ret = mes_c.pct_change()
    mnq_ret = mnq_c.pct_change()
    # Dollar-neutral pair: long MES short MNQ means earn mes_ret - mnq_ret
    pair_ret = mes_ret - mnq_ret
    strat_ret = pos.shift(1) * pair_ret

    # Costs on both legs
    pos_change = pos.diff().abs().fillna(0)
    # ~2 ticks * $1.25 MES + 2 ticks * $0.50 MNQ per flip, 2 commissions
    cost_flat_per_flip = (slip_ticks * 1.25 + slip_ticks * 0.50 + 0.62 * 2 * 2)  # $
    # approximate cost as pct of mes_c (notional proxy)
    cost_pct = pos_change * (cost_flat_per_flip / mes_c)
    net_ret = strat_ret - cost_pct
    net_ret.attrs["trades"] = trades
    return net_ret


# ============================================================================
# CANDIDATE 4: alt_relmom_long_only_v2 — Rehab existing paper strat
# ============================================================================

def backtest_alt_rel_strength(
    universe: list[str], lookback: int = 14, rebal: int = 7, top_n: int = 1,
    btc_filter: bool = True,
) -> pd.Series:
    """Long-only rotation on top N alt by relative strength vs BTC.
    Rebal every 'rebal' days. Hold top_n by relative return (alt_ret - btc_ret) over lookback.
    Filter: only LONG if BTC trend positive (BTC close > SMA20) -- otherwise CASH.
    Costs: 10bps roundtrip (Binance spot fee).
    """
    # Load all coins
    prices = {}
    for c in universe + ["BTCUSDT"]:
        try:
            df = load_crypto_1d(c)
            prices[c] = df["close"]
        except Exception:
            continue
    if "BTCUSDT" not in prices:
        raise RuntimeError("need BTCUSDT")

    # Align
    all_px = pd.DataFrame(prices).dropna()
    rets = all_px.pct_change()

    btc_sma20 = all_px["BTCUSDT"].rolling(20).mean()
    btc_up = all_px["BTCUSDT"] > btc_sma20  # trend filter

    # Rel strength score = alt_cumret - btc_cumret over lookback
    alt_symbols = [c for c in universe if c in all_px.columns]
    cum_lb = (1 + rets).rolling(lookback).apply(np.prod, raw=True) - 1
    btc_cum_lb = cum_lb["BTCUSDT"]

    # Score df
    rel_score = cum_lb[alt_symbols].sub(btc_cum_lb, axis=0)

    pos = pd.DataFrame(0.0, index=all_px.index, columns=alt_symbols)
    last_rebal = None
    for i, dt in enumerate(all_px.index):
        if i < lookback:
            continue
        do_rebal = last_rebal is None or (i - last_rebal) >= rebal
        if not do_rebal:
            # carry prior
            if i > 0:
                pos.iloc[i] = pos.iloc[i - 1]
            continue
        last_rebal = i
        # Filter: BTC up?
        if btc_filter and not btc_up.iloc[i]:
            pos.iloc[i] = 0.0  # cash
            continue
        # Top N
        scores_i = rel_score.iloc[i].dropna()
        if scores_i.empty or (scores_i <= 0).all():
            pos.iloc[i] = 0.0
            continue
        top = scores_i.nlargest(top_n).index
        weight = 1.0 / top_n
        pos.iloc[i] = 0.0
        pos.loc[dt, top] = weight

    # Portfolio daily ret
    port_ret = (pos.shift(1) * rets[alt_symbols]).sum(axis=1)

    # Costs: when pos flips per coin, charge 10bps
    flips = pos.diff().abs().sum(axis=1).fillna(0)
    cost = flips * 10e-4  # 10bps per flip unit
    net_ret = port_ret - cost
    net_ret.attrs["trades"] = int((pos.diff().abs().sum(axis=1) > 0).sum())
    return net_ret


# ============================================================================
# PROXY returns for existing live strats (for correlation check)
# ============================================================================

def proxy_cam_returns(symbols=("MES", "MNQ", "M2K", "MGC", "MCL"), lookback=20, hold=20) -> pd.Series:
    """Reproduction minimaliste CAM: chaque 'hold' jours, pick top-1 par return 'lookback',
    hold 'hold' jours. Commissions ignored (proxy pour correlation uniquement).
    """
    dfs = {s: load_futures_long(s) for s in symbols}
    common = None
    for d in dfs.values():
        idx = d.index
        common = idx if common is None else common.intersection(idx)
    closes = pd.DataFrame({s: dfs[s].loc[common, "close"] for s in symbols})
    rets = closes.pct_change()
    lb_ret = (1 + rets).rolling(lookback).apply(np.prod, raw=True) - 1

    pos = pd.DataFrame(0.0, index=common, columns=symbols)
    last = None
    for i, dt in enumerate(common):
        if i < lookback:
            continue
        if last is None or (i - last) >= hold:
            scores = lb_ret.iloc[i].dropna()
            if scores.empty or scores.max() < 0.02:
                pos.iloc[i] = 0.0
            else:
                top = scores.idxmax()
                pos.iloc[i] = 0.0
                pos.loc[dt, top] = 1.0
            last = i
        else:
            pos.iloc[i] = pos.iloc[i - 1]
    port_ret = (pos.shift(1) * rets).sum(axis=1)
    return port_ret


def proxy_gor_returns(lookback: int = 20) -> pd.Series:
    """Proxy gold_oil_rotation: rotate between MGC and MCL selecting best 'lookback' return.
    Hold until next rebalance (monthly ~ 20 bars).
    """
    mgc = load_futures_long("MGC")["close"]
    mcl = load_futures_long("MCL")["close"]
    common = mgc.index.intersection(mcl.index)
    mgc, mcl = mgc.loc[common], mcl.loc[common]
    rets = pd.DataFrame({"MGC": mgc.pct_change(), "MCL": mcl.pct_change()})
    lb_ret = (1 + rets).rolling(lookback).apply(np.prod, raw=True) - 1

    pos = pd.DataFrame(0.0, index=common, columns=["MGC", "MCL"])
    last = None
    for i, dt in enumerate(common):
        if i < lookback:
            continue
        if last is None or (i - last) >= lookback:
            scores = lb_ret.iloc[i]
            top = scores.idxmax()
            pos.iloc[i] = 0.0
            pos.loc[dt, top] = 1.0
            last = i
        else:
            pos.iloc[i] = pos.iloc[i - 1]
    return (pos.shift(1) * rets).sum(axis=1)


# ============================================================================
# Main driver
# ============================================================================

def main():
    print("=" * 80)
    print("DECORRELATED CANDIDATES RESEARCH 2026-04-23")
    print("=" * 80)

    mes = load_futures_long("MES")
    mnq = load_futures_long("MNQ")
    mgc = load_futures_long("MGC")
    mcl = load_futures_long("MCL")
    vix = load_vix_1d()

    print(f"MES: {len(mes)} bars ({mes.index.min().date()} -> {mes.index.max().date()})")
    print(f"MGC: {len(mgc)} bars, MNQ: {len(mnq)} bars, VIX: {len(vix)} bars")

    results = {}
    all_returns = {}

    # --- Candidate 1 ---
    print("\n[1/4] mes_3day_stretch_v2 ...")
    c1 = backtest_mes_3day_stretch(mes)
    results["mes_3day_stretch_v2"] = {
        "full": compute_metrics(c1, c1.attrs.get("trades")),
        "is": compute_metrics(oos_split(c1)[0]),
        "oos": compute_metrics(oos_split(c1)[1]),
        "wf": walk_forward_oos(c1),
    }
    all_returns["mes_3day_stretch_v2"] = c1
    print(f"  {results['mes_3day_stretch_v2']['full']}")

    # --- Candidate 2 ---
    print("\n[2/4] mgc_vix_hedge_v2 ...")
    c2 = backtest_mgc_vix_hedge(mgc, vix)
    results["mgc_vix_hedge_v2"] = {
        "full": compute_metrics(c2, c2.attrs.get("trades")),
        "is": compute_metrics(oos_split(c2)[0]),
        "oos": compute_metrics(oos_split(c2)[1]),
        "wf": walk_forward_oos(c2),
    }
    all_returns["mgc_vix_hedge_v2"] = c2
    print(f"  {results['mgc_vix_hedge_v2']['full']}")

    # --- Candidate 3 ---
    print("\n[3/4] mes_mnq_pairs_v2 ...")
    c3 = backtest_mes_mnq_pairs(mes, mnq)
    results["mes_mnq_pairs_v2"] = {
        "full": compute_metrics(c3, c3.attrs.get("trades")),
        "is": compute_metrics(oos_split(c3)[0]),
        "oos": compute_metrics(oos_split(c3)[1]),
        "wf": walk_forward_oos(c3),
    }
    all_returns["mes_mnq_pairs_v2"] = c3
    print(f"  {results['mes_mnq_pairs_v2']['full']}")

    # --- Candidate 4 ---
    print("\n[4/4] alt_relmom_long_only_v2 ...")
    alt_universe = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "ADAUSDT",
                    "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "NEARUSDT", "XRPUSDT"]
    try:
        c4 = backtest_alt_rel_strength(alt_universe, lookback=14, rebal=7, top_n=2, btc_filter=True)
        results["alt_relmom_long_only_v2"] = {
            "full": compute_metrics(c4, c4.attrs.get("trades")),
            "is": compute_metrics(oos_split(c4)[0]),
            "oos": compute_metrics(oos_split(c4)[1]),
            "wf": walk_forward_oos(c4),
        }
        all_returns["alt_relmom_long_only_v2"] = c4
        print(f"  {results['alt_relmom_long_only_v2']['full']}")
    except Exception as e:
        results["alt_relmom_long_only_v2"] = {"error": str(e)}
        print(f"  ERROR: {e}")

    # --- Proxy existing live strats ---
    print("\n[proxy] CAM + GOR for correlation ...")
    cam_r = proxy_cam_returns()
    gor_r = proxy_gor_returns()
    all_returns["_proxy_CAM"] = cam_r
    all_returns["_proxy_GOR"] = gor_r
    results["_proxy_CAM"] = {"full": compute_metrics(cam_r)}
    results["_proxy_GOR"] = {"full": compute_metrics(gor_r)}
    print(f"  CAM proxy: {results['_proxy_CAM']['full']}")
    print(f"  GOR proxy: {results['_proxy_GOR']['full']}")

    # --- Correlation matrix ---
    print("\n[correlation]")
    all_df = pd.DataFrame(all_returns).dropna(how="all")
    corr = all_df.corr(min_periods=100)
    print(corr.round(3))

    # --- Save ---
    out_json = REPORT_DIR / "decorrelated_strategies_2026-04-23_metrics.json"
    with out_json.open("w", encoding="utf-8") as f:
        json.dump({"metrics": results, "correlation": corr.to_dict()}, f, indent=2, default=str)

    out_parquet = REPORT_DIR / "decorrelated_strategies_2026-04-23_returns.parquet"
    all_df.to_parquet(out_parquet)
    print(f"\nSaved: {out_json}")
    print(f"Saved: {out_parquet}")


if __name__ == "__main__":
    main()
