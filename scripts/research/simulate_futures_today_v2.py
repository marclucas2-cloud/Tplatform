"""V2: Simulate ALL futures strats with FRESH data via yfinance.

Le bug data_refresh laisse les parquets stale au 30 mars. On recharge
via yfinance pour avoir les data au 16 avril 2026.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState

# yfinance tickers for futures continuous
TICKERS = {
    "MES":  "ES=F",   # E-mini S&P 500 (proxy MES)
    "MNQ":  "NQ=F",   # E-mini Nasdaq (proxy MNQ)
    "M2K":  "RTY=F",  # E-mini Russell 2000 (proxy M2K)
    "MGC":  "GC=F",   # Gold (proxy MGC)
    "MCL":  "CL=F",   # WTI Crude (proxy MCL)
    "VIX":  "^VIX",   # VIX
    "DAX":  "^GDAXI",
    "CAC40": "^FCHI",
    "ESTX50": "^STOXX50E",
}

print("Downloading fresh data via yfinance...")
data_sources = {}
for sym, tk in TICKERS.items():
    df = yf.download(tk, start="2018-01-01", end=(date.today() + timedelta(days=1)).isoformat(),
                     interval="1d", progress=False, auto_adjust=False)
    if df.empty:
        print(f"  {sym}: empty")
        continue
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    data_sources[sym] = df
    print(f"  {sym} ({tk}): {len(df)} rows, latest={df.index[-1].date()} close={df['close'].iloc[-1]:.2f}")

feed = DataFeed(data_sources)
now_ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
feed.set_timestamp(now_ts)

# Get latest bar to confirm date
mes_bar = feed.get_latest_bar("MES")
print(f"\n=== Eval bar: {mes_bar.timestamp.date()} {mes_bar.timestamp.strftime('%A')} MES close={mes_bar.close:.2f} ===\n")

results = []

# === STRATS LIVE-CAPABLE (3) ===
print("--- STRATS LIVE-CAPABLE ---")
portfolio_state = PortfolioState(equity=10_152.0, cash=10_152.0, positions={})

# 1. Cross-Asset Momentum
try:
    from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum
    s = CrossAssetMomentum()
    s.set_data_feed(feed)
    bar = feed.get_latest_bar("MES")
    top = s.get_top_pick()
    sig = s.on_bar(bar, portfolio_state) if bar else None
    print(f"  CrossAssetMomentum: top_pick={top}, signal={'BUY '+sig.symbol+' @ '+f'{feed.get_latest_bar(sig.symbol).close:.2f}' if sig else 'none'}")
    if sig:
        results.append(("LIVE", "CrossAssetMomentum", sig.symbol, sig.side,
                       feed.get_latest_bar(sig.symbol).close,
                       sig.stop_loss, sig.take_profit))
except Exception as e:
    print(f"  CrossAssetMomentum ERR: {e}")

# 2. Gold Trend MGC
try:
    from strategies_v2.futures.gold_trend_mgc import GoldTrendMGC
    s = GoldTrendMGC()
    s.set_data_feed(feed)
    bar = feed.get_latest_bar("MGC")
    sig = s.on_bar(bar, portfolio_state) if bar else None
    if sig:
        print(f"  GoldTrendMGC: BUY MGC @ {bar.close:.2f}, SL={sig.stop_loss:.2f}, TP={sig.take_profit:.2f}")
        results.append(("LIVE", "GoldTrendMGC", "MGC", sig.side, bar.close, sig.stop_loss, sig.take_profit))
    else:
        # Show why
        bars = feed.get_bars("MGC", 25)
        ema20 = bars["close"].ewm(span=20).mean().iloc[-1] if bars is not None else 0
        print(f"  GoldTrendMGC: no signal (close {bar.close:.2f} vs EMA20 ~{ema20:.2f})")
except Exception as e:
    print(f"  GoldTrendMGC ERR: {e}")

# 3. Gold-Oil Rotation
try:
    from strategies_v2.futures.gold_oil_rotation import GoldOilRotation
    s = GoldOilRotation()
    s.set_data_feed(feed)
    bar = feed.get_latest_bar("MGC")
    sig = s.on_bar(bar, portfolio_state) if bar else None
    if sig:
        sym_bar = feed.get_latest_bar(sig.symbol)
        print(f"  GoldOilRotation: BUY {sig.symbol} @ {sym_bar.close:.2f}")
        results.append(("LIVE", "GoldOilRotation", sig.symbol, sig.side, sym_bar.close,
                       sig.stop_loss, sig.take_profit))
    else:
        print(f"  GoldOilRotation: no signal (spread < 2%)")
except Exception as e:
    print(f"  GoldOilRotation ERR: {e}")

# === PAPER-ONLY ===
print("\n--- STRATS PAPER-ONLY ---")
portfolio_state = PortfolioState(equity=1_003_782.0, cash=1_003_782.0, positions={})

paper_strats = [
    ("MES Trend", "strategies_v2.futures.mes_trend", "MESTrend", {}, "MES"),
    ("MES Trend+MR", "strategies_v2.futures.mes_trend_mr", "MESTrendMR", {}, "MES"),
    ("MES 3-Day Stretch", "strategies_v2.futures.mes_3day_stretch", "MES3DayStretch", {}, "MES"),
    ("Overnight MES V2", "strategies_v2.futures.overnight_buy_close", "OvernightBuyClose",
     {"symbol": "MES", "sl_points": 60, "tp_points": 120, "ema_period": 50}, "MES"),
    ("Overnight MNQ V2", "strategies_v2.futures.overnight_buy_close", "OvernightBuyClose",
     {"symbol": "MNQ", "sl_points": 140, "tp_points": 300, "ema_period": 40}, "MNQ"),
    ("TSMOM Multi", "strategies_v2.futures.tsmom_multi", "TSMOMMulti", {}, "MES"),
    ("M2K ORB", "strategies_v2.futures.m2k_orb", "M2KORB", {}, "M2K"),
    ("MCL Brent Lag", "strategies_v2.futures.mcl_brent_lag", "MCLBrentLag", {}, "MCL"),
    ("MGC VIX Hedge", "strategies_v2.futures.mgc_vix_hedge", "MGCVixHedge", {}, "MGC"),
    ("Thursday Rally", "strategies_v2.futures.thursday_rally", "ThursdayRally", {}, "MES"),
    ("Friday Monday MNQ", "strategies_v2.futures.friday_monday_mnq", "FridayMondayMNQ", {}, "MNQ"),
    ("Multi TF Mom MES", "strategies_v2.futures.multi_tf_mom_mes", "MultiTFMomMES", {}, "MES"),
    ("BB Squeeze MES", "strategies_v2.futures.bb_squeeze_mes", "BBSqueezeMES", {}, "MES"),
    ("RS MES MNQ", "strategies_v2.futures.rs_mes_mnq_rotate", "RSMesMnqRotate", {}, "MES"),
    ("VIX Mean Reversion", "strategies_v2.futures.vix_mean_reversion", "VIXMeanReversion", {}, "VIX"),
]

for label, mod_path, cls_name, kwargs, sym_arg in paper_strats:
    try:
        mod = __import__(mod_path, fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        s = cls(**kwargs)
        s.set_data_feed(feed)
        bar = feed.get_latest_bar(sym_arg)
        if not bar:
            print(f"  {label}: no bar for {sym_arg}")
            continue
        sig = s.on_bar(bar, portfolio_state)
        if sig:
            sym_bar = feed.get_latest_bar(sig.symbol) if sig.symbol != sym_arg else bar
            print(f"  {label}: {sig.side} {sig.symbol} @ {sym_bar.close:.2f}, SL={sig.stop_loss:.2f}, TP={sig.take_profit:.2f}")
            results.append(("PAPER", label, sig.symbol, sig.side, sym_bar.close,
                           sig.stop_loss, sig.take_profit))
        else:
            print(f"  {label}: no signal")
    except Exception as e:
        print(f"  {label}: ERR {type(e).__name__}: {str(e)[:80]}")

# Calendar paper strats (T1-A)
print("\n--- T1-A CALENDAR PAPER STRATS ---")
try:
    from strategies_v2.futures.mes_calendar_paper import (
        MESMondayLong, MESWednesdayLong, MESPreHolidayLong,
    )
    for cls in [MESMondayLong, MESWednesdayLong, MESPreHolidayLong]:
        s = cls()
        s.set_data_feed(feed)
        bar = feed.get_latest_bar("MES")
        sig = s.on_bar(bar, portfolio_state) if bar else None
        day_name = bar.timestamp.strftime("%A") if bar else "?"
        if sig:
            print(f"  {cls.__name__}: BUY @ {bar.close:.2f} (bar {bar.timestamp.date()} {day_name})")
            results.append(("PAPER", cls.__name__, "MES", "BUY", bar.close, sig.stop_loss, sig.take_profit))
        else:
            print(f"  {cls.__name__}: no signal (bar {bar.timestamp.date()} {day_name})")
except Exception as e:
    print(f"  Calendar paper ERR: {e}")

print("\n\n========== RECAP ==========")
print(f"\nFresh data eval bar: {mes_bar.timestamp.date()} {mes_bar.timestamp.strftime('%A')}")

n_live = sum(1 for r in results if r[0] == "LIVE")
n_paper = sum(1 for r in results if r[0] == "PAPER")
print(f"\nLIVE futures (€10K): {n_live} signaux")
print(f"PAPER futures ($1M): {n_paper} signaux\n")

print("| Mode | Strat | Symbol | Side | Entry | SL | TP |")
print("|---|---|---|---|---:|---:|---:|")
for mode, label, sym, side, entry, sl, tp in results:
    print(f"| {mode} | {label} | {sym} | {side} | {entry:.2f} | {sl:.2f} | {tp:.2f} |")

# First-refusal check (CAM bloque GoldOil + autres si meme symbol)
print("\n--- First-refusal CAM ---")
cam_signals = [r for r in results if r[1] == "CrossAssetMomentum"]
if cam_signals:
    cam_sym = cam_signals[0][2]
    blocked = [r for r in results if r[2] == cam_sym and r[1] != "CrossAssetMomentum"]
    if blocked:
        print(f"CAM reserved {cam_sym}, blocks:")
        for r in blocked:
            print(f"  - {r[0]}/{r[1]}: SKIP (CAM first-refusal)")
