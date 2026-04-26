"""Verify mes_mr_vix_spike voit MES bar fresh post-fix.

Reproduit le pattern du runner sans appeler IBKR (pas besoin de connexion live).
Charge MES + VIX via _load_futures_daily_frame, simule DataFeed.set_timestamp(now),
appelle on_bar et logue le journal entry.
"""
from pathlib import Path
import json
import pandas as pd

from core.worker.cycles.futures_runner import _load_futures_daily_frame
from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState
from strategies_v2.futures.mes_mr_vix_spike import MESMeanReversionVIXSpike

ROOT = Path("/opt/trading-platform")
data_dir = ROOT / "data" / "futures"

data_sources = {}
for sym in ["MES", "MNQ", "M2K", "MIB", "ESTX50", "VIX", "MGC", "MCL", "DAX", "CAC40"]:
    fpath = data_dir / f"{sym}_1D.parquet"
    if fpath.exists():
        data_sources[sym] = _load_futures_daily_frame(fpath)

feed = DataFeed(data_sources)
feed.set_timestamp(pd.Timestamp.now(tz="UTC"))

bar_mes = feed.get_latest_bar("MES")
bar_vix = feed.get_latest_bar("VIX")
print(f"feed.timestamp = {feed.timestamp}")
print(f"feed.get_latest_bar('MES'): ts={bar_mes.timestamp} close={bar_mes.close}")
print(f"feed.get_latest_bar('VIX'): ts={bar_vix.timestamp} close={bar_vix.close}")

# Compute bar age
bar_ts = pd.Timestamp(bar_mes.timestamp).normalize()
now = pd.Timestamp.now().normalize()
age_days = (now - bar_ts).days
print(f"Bar age days: {age_days} (MAX_BAR_AGE_DAYS={MESMeanReversionVIXSpike.MAX_BAR_AGE_DAYS})")

# Strategy on_bar
strat = MESMeanReversionVIXSpike()
strat.set_data_feed(feed)
ps = PortfolioState(equity=10000, cash=10000, positions={})
sig = strat.on_bar(bar_mes, ps)
print(f"\nSignal: {sig}")
if sig:
    print(f"  side={sig.side} SL={sig.stop_loss} TP={sig.take_profit} strength={sig.strength}")
else:
    print("  (no signal — soit conditions non remplies, soit guard freshness)")
