"""Worker configuration — timezones, market hours, constants."""
import threading
import zoneinfo

# Timezones
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
ET = zoneinfo.ZoneInfo("America/New_York")

# Daily schedule (Paris time)
DAILY_HOUR = 15
DAILY_MINUTE = 35

# US intraday window (Paris time)
INTRADAY_START_HOUR = 15
INTRADAY_START_MINUTE = 35
INTRADAY_END_HOUR = 22
INTRADAY_END_MINUTE = 0
INTRADAY_INTERVAL_SECONDS = 300  # 5 min

# EU market hours (09:00-17:30 CET)
EU_START_HOUR = 9
EU_START_MINUTE = 0
EU_END_HOUR = 17
EU_END_MINUTE = 30

# Live risk cycle interval
LIVE_RISK_INTERVAL_SECONDS = 300  # 5 min

# Crypto cycle interval (24/7)
CRYPTO_INTERVAL_SECONDS = 900  # 15 min

# Sizing SOFT_LAUNCH crypto: aligned with crypto_allocation.yaml
CRYPTO_KELLY_FRACTION = 0.25

# Threading locks for concurrent cycle coordination
execution_lock = threading.Lock()   # US/EU intraday + daily
crypto_lock = threading.Lock()      # Crypto cycle
ibkr_lock = threading.Lock()        # FX/futures IBKR
risk_lock = threading.Lock()        # Risk cycle
