"""
Configuration globale du backtester intraday.
"""
import os
from pathlib import Path
from datetime import datetime, timedelta

# ── Charger .env du projet parent ──
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv pas installé — clés doivent être en env

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
FORCE_EXIT_TIME = "15:55"     # Sortie forcée (5 min avant close)
EARLIEST_ENTRY = "09:35"      # Pas de trade avant 9:35
TIMEZONE = "US/Eastern"

# ── Universe ──
# Mode d'univers : "full", "eligible", "curated", "minimal"
# - full     : ~3000-5000 tickers (tout Alpaca)
# - eligible : ~500-1500 (filtré volume/prix/ATR)
# - curated  : ~200 (top volume + permanents)
# - minimal  : ~50 (permanents + sector leaders) — pour debug/test rapide
UNIVERSE_MODE = os.getenv("UNIVERSE_MODE", "eligible")

# Filtres pour le mode "eligible"
UNIVERSE_MIN_VOLUME = 500_000     # Volume moyen 20j minimum
UNIVERSE_MIN_PRICE = 5.0          # Prix minimum
UNIVERSE_MAX_PRICE = 2000.0       # Prix maximum
UNIVERSE_MIN_ATR_PCT = 1.0        # ATR daily minimum en %

# Scanner "Stocks in Play" quotidien
SCAN_MIN_GAP_PCT = 2.0            # Gap d'ouverture minimum
SCAN_MIN_VOL_RATIO = 2.0          # Volume vs moyenne
SCAN_MIN_ATR_RATIO = 1.5          # ATR vs moyenne
SCAN_MAX_STOCKS = 50              # Max stocks in play par jour

# ── Benchmarks (ne changent pas) ──
BENCHMARK = "SPY"

# ── Data ──
BACKTEST_DAYS = 365            # 1 an d'historique
BACKTEST_END = datetime.now()
BACKTEST_START = BACKTEST_END - timedelta(days=BACKTEST_DAYS)
TIMEFRAME_1MIN = "1Min"
TIMEFRAME_5MIN = "5Min"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data_cache")

# ── Data fetching ──
FETCH_MAX_WORKERS = 10             # Threads parallèles pour Alpaca
FETCH_BATCH_SIZE = 50              # Tickers par batch
FETCH_RATE_LIMIT_SLEEP = 3.0      # Pause entre batches (sec)

# ── Output ──
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
