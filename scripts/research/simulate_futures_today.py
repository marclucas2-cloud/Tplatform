"""Simulate ALL futures strats (live + paper) on today's data.

Reproduit le cycle FUTURES du worker (1x/jour 14h UTC) qui a planté
aujourd'hui avec data sort error. Resultat: aurait-on tradé ?
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState

# Load all futures data with the SAME loader fix as worker
data_dir = ROOT / "data" / "futures"
data_sources = {}
for sym in ["MES", "MNQ", "M2K", "MIB", "ESTX50", "VIX", "MGC", "MCL", "DAX", "CAC40"]:
    fpath = data_dir / f"{sym}_1D.parquet"
    if fpath.exists():
        df = pd.read_parquet(fpath)
        df.columns = [c.lower() for c in df.columns]
        if "datetime" in df.columns:
            df.index = pd.to_datetime(df["datetime"])
        elif not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df = df[df.index.notna()]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        data_sources[sym] = df

print(f"Data loaded: {list(data_sources.keys())}")
for sym, df in data_sources.items():
    print(f"  {sym}: {len(df)} rows, latest={df.index[-1].date()}")

feed = DataFeed(data_sources)
now_ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
feed.set_timestamp(now_ts)

# Test both equity scenarios: LIVE (10K) and PAPER (1M)
results = {"LIVE": [], "PAPER": []}

for mode, equity in [("LIVE", 10_152.0), ("PAPER", 1_003_782.0)]:
    print(f"\n\n========== MODE {mode} (equity ${equity:,.0f}) ==========")
    portfolio_state = PortfolioState(equity=equity, cash=equity, positions={})

    # === STRATS LIVE-CAPABLE (live + paper both) ===
    print("\n--- STRATS LIVE-CAPABLE ---")

    # 1. Cross-Asset Momentum
    try:
        from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum
        s = CrossAssetMomentum()
        s.set_data_feed(feed)
        bar = feed.get_latest_bar("MES")
        top = s.get_top_pick()
        sig = s.on_bar(bar, portfolio_state) if bar else None
        msg = f"top_pick={top}, signal={'BUY '+sig.symbol if sig else 'none'}"
        print(f"  CrossAssetMomentum: {msg}")
        if sig:
            results[mode].append(("CrossAssetMomentum", sig.symbol, sig.side, bar.close))
    except Exception as e:
        print(f"  CrossAssetMomentum ERR: {e}")

    # 2. Gold Trend MGC
    try:
        from strategies_v2.futures.gold_trend_mgc import GoldTrendMGC
        s = GoldTrendMGC()
        s.set_data_feed(feed)
        bar = feed.get_latest_bar("MGC")
        sig = s.on_bar(bar, portfolio_state) if bar else None
        print(f"  GoldTrendMGC: signal={'BUY @'+f'{bar.close:.2f}' if sig else 'none (below EMA20)'}")
        if sig:
            results[mode].append(("GoldTrendMGC", "MGC", sig.side, bar.close))
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
            print(f"  GoldOilRotation: BUY {sig.symbol}")
            results[mode].append(("GoldOilRotation", sig.symbol, sig.side, bar.close))
        else:
            print(f"  GoldOilRotation: no signal (spread < 2%)")
    except Exception as e:
        print(f"  GoldOilRotation ERR: {e}")

    # === PAPER-ONLY ===
    if mode != "PAPER":
        continue
    print("\n--- STRATS PAPER-ONLY ---")

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
                print(f"  {label}: {sig.side} {sig.symbol} @ {bar.close:.2f}")
                results[mode].append((label, sig.symbol, sig.side, bar.close))
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
                print(f"  {cls.__name__}: {sig.side} @ {bar.close:.2f} (bar {bar.timestamp.date()} {day_name})")
                results[mode].append((cls.__name__, sig.symbol, sig.side, bar.close))
            else:
                print(f"  {cls.__name__}: no signal (bar {bar.timestamp.date()} {day_name})")
    except Exception as e:
        print(f"  Calendar paper ERR: {e}")


# === RECAP ===
print("\n\n========== RECAP TRADES POTENTIELS ==========")
for mode, lst in results.items():
    print(f"\n{mode}: {len(lst)} signaux")
    for label, sym, side, price in lst:
        print(f"  - {label}: {side} {sym} @ {price:.2f}")
