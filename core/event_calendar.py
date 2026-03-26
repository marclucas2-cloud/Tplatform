"""
DATA-1 + EU-4 : Calendrier d'events enrichi.

Charge config/events_calendar.json et expose des helpers pour savoir
si un jour est FOMC, CPI, OpEx, earnings, etc.
Utilise par le pipeline pour ajuster le sizing et le risk management.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class EventCalendar:
    """Calendrier d'events macro et corporate."""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = str(Path(__file__).parent.parent / "config" / "events_calendar.json")
        with open(path, encoding="utf-8") as f:
            self._data = json.load(f)

        # Pre-parse toutes les dates en sets pour lookup O(1)
        self._fomc = self._parse_dates("fomc_meetings")
        self._cpi = self._parse_dates("cpi_releases")
        self._nfp = self._parse_dates("nfp_releases")
        self._bce = self._parse_dates("bce_meetings")
        self._opex = self._parse_dates("opex_dates")

        # Earnings : {ticker: set of dates}
        self._earnings_us = {
            ticker: set(self._to_date(d) for d in dates)
            for ticker, dates in self._data.get("earnings_us", {}).items()
        }
        self._earnings_eu = {
            ticker: set(self._to_date(d) for d in dates)
            for ticker, dates in self._data.get("earnings_eu", {}).items()
        }

        # Toutes les dates d'events indexees par type (pour days_until_next_event)
        self._all_sorted = {
            "fomc": sorted(self._fomc),
            "cpi": sorted(self._cpi),
            "nfp": sorted(self._nfp),
            "bce": sorted(self._bce),
            "opex": sorted(self._opex),
        }

        logger.info(
            "EventCalendar loaded: %d FOMC, %d CPI, %d NFP, %d BCE, %d OpEx, "
            "%d US earnings tickers, %d EU earnings tickers",
            len(self._fomc),
            len(self._cpi),
            len(self._nfp),
            len(self._bce),
            len(self._opex),
            len(self._earnings_us),
            len(self._earnings_eu),
        )

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _to_date(self, s: str) -> date:
        """Parse une date string YYYY-MM-DD en date object."""
        return datetime.strptime(s, "%Y-%m-%d").date()

    def _parse_dates(self, key: str) -> set:
        """Parse une liste de dates du JSON en set de date objects."""
        return set(self._to_date(d) for d in self._data.get(key, []))

    def _normalize_date(self, d) -> date:
        """Accepte date, datetime, ou string YYYY-MM-DD."""
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            return self._to_date(d)
        return d

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def get_events_today(self, d) -> List[dict]:
        """Retourne tous les events pour une date donnee.

        Args:
            d: date, datetime, ou string YYYY-MM-DD

        Returns:
            liste de dicts {type, detail}
        """
        d = self._normalize_date(d)
        events = []

        if d in self._fomc:
            events.append({"type": "fomc", "detail": "FOMC meeting"})
        if d in self._cpi:
            events.append({"type": "cpi", "detail": "CPI release"})
        if d in self._nfp:
            events.append({"type": "nfp", "detail": "NFP release"})
        if d in self._bce:
            events.append({"type": "bce", "detail": "BCE meeting"})
        if d in self._opex:
            events.append({"type": "opex", "detail": "Options expiration"})

        # Earnings US
        for ticker, dates in self._earnings_us.items():
            if d in dates:
                events.append({"type": "earnings_us", "detail": f"{ticker} earnings"})

        # Earnings EU
        for ticker, dates in self._earnings_eu.items():
            if d in dates:
                events.append({"type": "earnings_eu", "detail": f"{ticker} earnings"})

        return events

    def is_fomc_day(self, d) -> bool:
        """True si c'est un jour FOMC."""
        return self._normalize_date(d) in self._fomc

    def is_cpi_day(self, d) -> bool:
        """True si c'est un jour de release CPI."""
        return self._normalize_date(d) in self._cpi

    def is_nfp_day(self, d) -> bool:
        """True si c'est un jour de release NFP."""
        return self._normalize_date(d) in self._nfp

    def is_bce_day(self, d) -> bool:
        """True si c'est un jour de meeting BCE."""
        return self._normalize_date(d) in self._bce

    def is_opex_friday(self, d) -> bool:
        """True si c'est un vendredi d'expiration d'options."""
        return self._normalize_date(d) in self._opex

    def is_earnings_day(self, ticker: str, d) -> bool:
        """True si le ticker a des earnings ce jour.

        Cherche dans les earnings US et EU.

        Args:
            ticker: symbole (ex: 'AAPL', 'LVMH')
            d:      date

        Returns:
            bool
        """
        d = self._normalize_date(d)
        ticker_upper = ticker.upper()

        us_dates = self._earnings_us.get(ticker_upper, set())
        if d in us_dates:
            return True

        eu_dates = self._earnings_eu.get(ticker_upper, set())
        if d in eu_dates:
            return True

        return False

    def days_until_next_event(self, event_type: str, from_date) -> int:
        """Nombre de jours jusqu'au prochain event du type donne.

        Args:
            event_type: 'fomc', 'cpi', 'nfp', 'bce', ou 'opex'
            from_date:  date de reference

        Returns:
            nombre de jours (0 si c'est aujourd'hui, -1 si aucun event futur)
        """
        from_date = self._normalize_date(from_date)
        sorted_dates = self._all_sorted.get(event_type.lower(), [])

        for event_date in sorted_dates:
            if event_date >= from_date:
                return (event_date - from_date).days

        return -1

    def is_high_impact_day(self, d) -> bool:
        """True si le jour a au moins un event macro majeur (FOMC, CPI, NFP).

        Utile pour reduire le sizing automatiquement.
        """
        d = self._normalize_date(d)
        return d in self._fomc or d in self._cpi or d in self._nfp

    def get_earnings_tickers_today(self, d) -> List[str]:
        """Retourne la liste des tickers qui ont earnings ce jour.

        Args:
            d: date

        Returns:
            liste de tickers (US + EU)
        """
        d = self._normalize_date(d)
        tickers = []

        for ticker, dates in self._earnings_us.items():
            if d in dates:
                tickers.append(ticker)

        for ticker, dates in self._earnings_eu.items():
            if d in dates:
                tickers.append(ticker)

        return tickers
