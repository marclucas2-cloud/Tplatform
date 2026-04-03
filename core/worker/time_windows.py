"""Market time window checks — used by cycles to determine activity."""
from datetime import datetime

from core.worker.config import (
    DAILY_HOUR,
    DAILY_MINUTE,
    EU_END_HOUR,
    EU_END_MINUTE,
    EU_START_HOUR,
    EU_START_MINUTE,
    INTRADAY_END_HOUR,
    INTRADAY_END_MINUTE,
    INTRADAY_START_HOUR,
    INTRADAY_START_MINUTE,
    PARIS,
)


def is_weekday():
    """Verifie si c'est un jour de semaine (lun-ven)."""
    return datetime.now(PARIS).weekday() < 5


def is_eu_intraday_window():
    """Verifie si on est dans la fenetre EU intraday (09:00-17:30 Paris)."""
    now = datetime.now(PARIS)
    start = now.replace(hour=EU_START_HOUR, minute=EU_START_MINUTE, second=0)
    end = now.replace(hour=EU_END_HOUR, minute=EU_END_MINUTE, second=0)
    return start <= now <= end


def is_live_risk_window():
    """Verifie si on est dans la fenetre live risk monitoring (09:00-22:00 Paris)."""
    now = datetime.now(PARIS)
    return 9 <= now.hour <= 22


def is_fx_window():
    """Verifie si le marche FX est ouvert.

    FX = dimanche 17:00 ET a vendredi 17:00 ET = quasi 24h lun-ven.
    En CET: dimanche 23:00 a vendredi 23:00.
    On trade lundi 00:00 CET a vendredi 22:59 CET (simplifie).
    """
    now = datetime.now(PARIS)
    weekday = now.weekday()  # 0=lundi ... 6=dimanche
    if 0 <= weekday <= 3:
        return True
    if weekday == 4:
        return now.hour < 23
    if weekday == 5:
        return False
    if weekday == 6:
        return now.hour >= 23
    return False


def is_intraday_window():
    """Verifie si on est dans la fenetre intraday (15:35-22:00 Paris)."""
    now = datetime.now(PARIS)
    start = now.replace(hour=INTRADAY_START_HOUR, minute=INTRADAY_START_MINUTE, second=0)
    end = now.replace(hour=INTRADAY_END_HOUR, minute=INTRADAY_END_MINUTE, second=0)
    return start <= now <= end


def is_daily_time():
    """Verifie si c'est l'heure du run daily (15:35 Paris, +/- 2 min)."""
    now = datetime.now(PARIS)
    return now.hour == DAILY_HOUR and DAILY_MINUTE <= now.minute <= DAILY_MINUTE + 2
