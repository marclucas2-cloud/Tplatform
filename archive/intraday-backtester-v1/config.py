"""
Configuration globale du backtester intraday.
"""
import os
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Alpaca ──
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# ── Capital & Risk ──
INITIAL_CAPITAL = 100_000
MAX_POSITION_PCT = 0.05        # 5% du capital max par position
MAX_SIMULTANEOUS = 5           # Max 5 positions ouvertes
COMMISSION_PER_SHARE = 0.005   # $0.005 par action
SLIPPAGE_PCT = 0.0002          # 0.02% slippage

# ── Timing ──
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"
FORCE_EXIT_TIME = "15:59"     # Sortie forcée
TIMEZONE = "US/Eastern"

# ── Tickers ──
MOMENTUM_TICKERS = ["NVDA", "AAPL", "AMZN", "META", "TSLA", "AMD", "MSFT", "GOOGL"]
PAIR_TICKERS = [
    ("NVDA", "AMD"),
    ("AAPL", "MSFT"),
    ("JPM", "BAC"),
    ("XOM", "CVX"),
    ("GOOGL", "META"),
]
CROSS_ASSET_TICKERS = ["COIN", "MARA", "MSTR", "TLT", "GLD"]
BENCHMARK = "SPY"
ALL_TICKERS = list(set(
    MOMENTUM_TICKERS
    + [t for pair in PAIR_TICKERS for t in pair]
    + CROSS_ASSET_TICKERS
    + [BENCHMARK, "QQQ"]
))

# ── Data ──
BACKTEST_DAYS = 365            # 1 an d'historique
BACKTEST_END = datetime.now()
BACKTEST_START = BACKTEST_END - timedelta(days=BACKTEST_DAYS)
TIMEFRAME_1MIN = "1Min"
TIMEFRAME_5MIN = "5Min"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data_cache")

# ── Output ──
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
