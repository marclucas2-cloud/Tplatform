"""V3: Simulate ALL futures strats with FRESH data from IBKR Gateway.

A executer sur le VPS (acces IBKR Gateway 4002 live ou 4003 paper).
Fetch daily bars via reqHistoricalData puis simule tout le cycle.
"""
from __future__ import annotations
import sys
import os
import random
from pathlib import Path
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(str(ROOT / ".env"))

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState

from ib_insync import IB, Future, Index, Contract

# IBKR contract specs (futures continuous + EU indices)
CONTRACTS = {
    # Futures (use front month, ib_insync auto-rolls)
    "MES":  {"type": "future", "symbol": "MES", "exchange": "CME", "currency": "USD",
             "expiry": "20260618"},
    "MNQ":  {"type": "future", "symbol": "MNQ", "exchange": "CME", "currency": "USD",
             "expiry": "20260618"},
    "M2K":  {"type": "future", "symbol": "M2K", "exchange": "CME", "currency": "USD",
             "expiry": "20260618"},
    "MGC":  {"type": "future", "symbol": "MGC", "exchange": "COMEX", "currency": "USD",
             "expiry": "20260626"},
    "MCL":  {"type": "future", "symbol": "MCL", "exchange": "NYMEX", "currency": "USD",
             "expiry": "20260520"},
    # Indices
    "VIX":   {"type": "index", "symbol": "VIX", "exchange": "CBOE", "currency": "USD"},
    "DAX":   {"type": "index", "symbol": "DAX", "exchange": "EUREX", "currency": "EUR"},
    "CAC40": {"type": "index", "symbol": "CAC40", "exchange": "MONEP", "currency": "EUR"},
    "ESTX50": {"type": "index", "symbol": "ESTX50", "exchange": "EUREX", "currency": "EUR"},
}


def fetch_daily(ib: IB, sym: str, n_years: int = 5) -> pd.DataFrame:
    cfg = CONTRACTS[sym]
    if cfg["type"] == "future":
        c = Future(symbol=cfg["symbol"], lastTradeDateOrContractMonth=cfg["expiry"],
                   exchange=cfg["exchange"], currency=cfg["currency"])
    else:
        c = Index(symbol=cfg["symbol"], exchange=cfg["exchange"], currency=cfg["currency"])
    try:
        qual = ib.qualifyContracts(c)
        if not qual:
            print(f"  {sym}: qualify failed")
            return pd.DataFrame()
        c = qual[0]
    except Exception as e:
        print(f"  {sym}: qualify ERR {e}")
        return pd.DataFrame()

    try:
        bars = ib.reqHistoricalData(
            c, endDateTime="", durationStr=f"{n_years} Y",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=2,
        )
    except Exception as e:
        print(f"  {sym}: reqHistoricalData ERR {e}")
        return pd.DataFrame()

    if not bars:
        return pd.DataFrame()

    rows = [{"datetime": b.date, "open": float(b.open), "high": float(b.high),
             "low": float(b.low), "close": float(b.close),
             "volume": int(b.volume) if b.volume >= 0 else 0} for b in bars]
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def main():
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "4002"))
    print(f"Connecting to IBKR {host}:{port}...")
    ib = IB()
    ib.connect(host, port, clientId=random.randint(70, 79), timeout=15)

    try:
        data_sources = {}
        for sym in CONTRACTS:
            print(f"Fetching {sym}...")
            df = fetch_daily(ib, sym, n_years=2)  # 2Y suffisant pour la plupart
            if not df.empty:
                data_sources[sym] = df
                print(f"  {sym}: {len(df)} rows, latest={df.index[-1].date()} close={df['close'].iloc[-1]:.2f}")

        feed = DataFeed(data_sources)
        now_ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
        feed.set_timestamp(now_ts)

        mes_bar = feed.get_latest_bar("MES")
        print(f"\n=== Eval bar: {mes_bar.timestamp.date()} {mes_bar.timestamp.strftime('%A')} ===\n")

        results = []

        # --- LIVE-CAPABLE ---
        print("--- STRATS LIVE-CAPABLE ---")
        portfolio_state = PortfolioState(equity=10_152.0, cash=10_152.0, positions={})

        try:
            from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum
            s = CrossAssetMomentum(); s.set_data_feed(feed)
            bar = feed.get_latest_bar("MES")
            top = s.get_top_pick()
            sig = s.on_bar(bar, portfolio_state) if bar else None
            print(f"  CrossAssetMomentum: top_pick={top}, signal={'BUY '+sig.symbol if sig else 'none'}")
            if sig:
                sym_bar = feed.get_latest_bar(sig.symbol)
                results.append(("LIVE", "CAM", sig.symbol, sig.side, sym_bar.close, sig.stop_loss, sig.take_profit))
        except Exception as e:
            print(f"  CAM ERR: {e}")

        try:
            from strategies_v2.futures.gold_trend_mgc import GoldTrendMGC
            s = GoldTrendMGC(); s.set_data_feed(feed)
            bar = feed.get_latest_bar("MGC")
            sig = s.on_bar(bar, portfolio_state) if bar else None
            if sig:
                print(f"  GoldTrendMGC: BUY MGC @ {bar.close:.2f}, SL={sig.stop_loss:.2f}, TP={sig.take_profit:.2f}")
                results.append(("LIVE", "GoldTrendMGC", "MGC", sig.side, bar.close, sig.stop_loss, sig.take_profit))
            else:
                bars = feed.get_bars("MGC", 25)
                ema20 = bars["close"].ewm(span=20).mean().iloc[-1] if bars is not None else 0
                print(f"  GoldTrendMGC: no signal (close {bar.close:.2f} vs EMA20 ~{ema20:.2f})")
        except Exception as e:
            print(f"  GoldTrendMGC ERR: {e}")

        try:
            from strategies_v2.futures.gold_oil_rotation import GoldOilRotation
            s = GoldOilRotation(); s.set_data_feed(feed)
            bar = feed.get_latest_bar("MGC")
            sig = s.on_bar(bar, portfolio_state) if bar else None
            if sig:
                sym_bar = feed.get_latest_bar(sig.symbol)
                print(f"  GoldOilRotation: BUY {sig.symbol} @ {sym_bar.close:.2f}")
                results.append(("LIVE", "GoldOilRotation", sig.symbol, sig.side, sym_bar.close, sig.stop_loss, sig.take_profit))
            else:
                print("  GoldOilRotation: no signal (spread < 2%)")
        except Exception as e:
            print(f"  GoldOilRotation ERR: {e}")

        # --- PAPER ---
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
                s = cls(**kwargs); s.set_data_feed(feed)
                bar = feed.get_latest_bar(sym_arg)
                if not bar:
                    print(f"  {label}: no bar for {sym_arg}")
                    continue
                sig = s.on_bar(bar, portfolio_state)
                if sig:
                    sym_bar = feed.get_latest_bar(sig.symbol) if sig.symbol != sym_arg else bar
                    print(f"  {label}: {sig.side} {sig.symbol} @ {sym_bar.close:.2f}, SL={sig.stop_loss:.2f}, TP={sig.take_profit:.2f}")
                    results.append(("PAPER", label, sig.symbol, sig.side, sym_bar.close, sig.stop_loss, sig.take_profit))
                else:
                    print(f"  {label}: no signal")
            except Exception as e:
                print(f"  {label}: ERR {type(e).__name__}: {str(e)[:80]}")

        print("\n--- T1-A CALENDAR PAPER STRATS ---")
        try:
            from strategies_v2.futures.mes_calendar_paper import (
                MESMondayLong, MESWednesdayLong, MESPreHolidayLong,
            )
            for cls in [MESMondayLong, MESWednesdayLong, MESPreHolidayLong]:
                s = cls(); s.set_data_feed(feed)
                bar = feed.get_latest_bar("MES")
                sig = s.on_bar(bar, portfolio_state) if bar else None
                day_name = bar.timestamp.strftime("%A") if bar else "?"
                if sig:
                    print(f"  {cls.__name__}: BUY MES @ {bar.close:.2f} (bar {bar.timestamp.date()} {day_name})")
                    results.append(("PAPER", cls.__name__, "MES", "BUY", bar.close, sig.stop_loss, sig.take_profit))
                else:
                    print(f"  {cls.__name__}: no signal (bar {bar.timestamp.date()} {day_name})")
        except Exception as e:
            print(f"  Calendar paper ERR: {e}")

        print("\n\n========== RECAP ==========")
        print(f"Eval bar: {mes_bar.timestamp.date()} {mes_bar.timestamp.strftime('%A')}")
        print(f"LIVE: {sum(1 for r in results if r[0]=='LIVE')} signaux")
        print(f"PAPER: {sum(1 for r in results if r[0]=='PAPER')} signaux\n")
        for mode, label, sym, side, entry, sl, tp in results:
            print(f"  [{mode}] {label}: {side} {sym} @ {entry:.2f} (SL {sl:.2f}, TP {tp:.2f})")

    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
