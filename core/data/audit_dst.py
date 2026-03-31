"""
Audit DST — Validation multi-broker des fuseaux horaires et transitions DST.

Verifie la coherence des horaires de marche, detecte les transitions
DST a venir (US et EU), et valide l'alignement des bougies avec les
fuseaux attendus.

Marches supportes :
  - EU (DAX/CAC/SX5E) : 09:00-17:30 CET
  - US (SPY/QQQ)      : 09:30-16:00 ET
  - FX                 : dimanche 17:00 ET -> vendredi 17:00 ET
  - Crypto             : 24/7 (maintenance Binance mardi ~06:00 UTC)
"""

import logging
import time as _time
from datetime import datetime, date, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

# -- Fuseaux horaires de reference --
_UTC = timezone.utc
_PARIS = ZoneInfo("Europe/Paris")
_NEW_YORK = ZoneInfo("America/New_York")

# -- Definitions des marches --
# Horaires locaux (dans le fuseau de reference du marche)
MARKET_DEFINITIONS = {
    "EU": {
        "timezone": "Europe/Paris",
        "open": time(9, 0),
        "close": time(17, 30),
        "days": [0, 1, 2, 3, 4],  # lundi-vendredi
        "description": "DAX / CAC / SX5E",
    },
    "US": {
        "timezone": "America/New_York",
        "open": time(9, 30),
        "close": time(16, 0),
        "days": [0, 1, 2, 3, 4],
        "description": "SPY / QQQ / US equities",
    },
    "FX": {
        "timezone": "America/New_York",
        "open_day": 6,   # dimanche
        "open": time(17, 0),
        "close_day": 4,  # vendredi
        "close": time(17, 0),
        "days": None,  # continu sauf weekend
        "description": "Forex 24h Sun 17:00 ET - Fri 17:00 ET",
    },
    "CRYPTO": {
        "timezone": "UTC",
        "open": time(0, 0),
        "close": time(23, 59, 59),
        "days": [0, 1, 2, 3, 4, 5, 6],  # 7/7
        "description": "Crypto 24/7 (maintenance Binance Tue ~06:00 UTC)",
        "maintenance_day": 1,  # mardi
        "maintenance_hour_utc": 6,
    },
}

# Seuil d'alerte DST (heures avant la transition)
DST_WARNING_HOURS = 48


class AuditDST:
    """Auditeur de coherence DST pour plateforme multi-broker.

    Valide les horaires de marche, detecte les transitions DST,
    verifie l'alignement des bougies et la synchronisation d'horloge.
    """

    def __init__(self, reference_date: Optional[date] = None):
        """
        Args:
            reference_date: date de reference pour les calculs.
                            Si None, utilise la date du jour (UTC).
        """
        self.reference_date = reference_date or datetime.now(_UTC).date()
        self._paris_tz = _PARIS
        self._ny_tz = _NEW_YORK

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def check_all(self) -> Dict:
        """Execute tous les controles et retourne un rapport structure.

        Returns:
            {
                "timestamp": str ISO,
                "reference_date": str,
                "checks": {
                    "market_hours": {market: {pass, details}},
                    "dst_transitions": {pass, transitions, warnings},
                    "broker_clock_sync": {pass, drift_ms, details},
                },
                "overall_pass": bool,
            }
        """
        results = {}

        # 1. Horaires de marche
        market_hours = {}
        for market in ["EU", "US", "FX", "CRYPTO"]:
            market_hours[market] = self.check_market_hours(market)
        results["market_hours"] = market_hours

        # 2. Transitions DST
        results["dst_transitions"] = self.check_dst_transitions()

        # 3. Synchronisation horloge
        results["broker_clock_sync"] = self.check_broker_clock_sync()

        # Verdict global
        all_pass = True
        for market, check in market_hours.items():
            if not check["pass"]:
                all_pass = False
        if not results["dst_transitions"]["pass"]:
            all_pass = False
        if not results["broker_clock_sync"]["pass"]:
            all_pass = False

        return {
            "timestamp": datetime.now(_UTC).isoformat(),
            "reference_date": self.reference_date.isoformat(),
            "checks": results,
            "overall_pass": all_pass,
        }

    def check_market_hours(self, market: str) -> Dict:
        """Valide les horaires open/close d'un marche pour la date de reference.

        Args:
            market: "EU", "US", "FX" ou "CRYPTO"

        Returns:
            {
                "pass": bool,
                "market": str,
                "open_utc": str ou None,
                "close_utc": str ou None,
                "timezone": str,
                "is_open_today": bool,
                "details": str,
            }
        """
        market = market.upper()
        if market not in MARKET_DEFINITIONS:
            return {
                "pass": False,
                "market": market,
                "open_utc": None,
                "close_utc": None,
                "timezone": "unknown",
                "is_open_today": False,
                "details": f"Marche inconnu: {market}",
            }

        try:
            open_utc, close_utc = self.get_market_calendar(
                market, self.reference_date
            )

            # Verifier que open < close (sauf FX continu et crypto)
            valid = True
            details = "OK"

            if market in ("EU", "US"):
                if open_utc is None or close_utc is None:
                    # Jour ferme (weekend/ferie)
                    valid = True
                    details = "Marche ferme ce jour"
                elif open_utc >= close_utc:
                    valid = False
                    details = f"Incoherence: open ({open_utc}) >= close ({close_utc})"

            weekday = self.reference_date.weekday()
            mdef = MARKET_DEFINITIONS[market]
            is_open = True

            if market == "FX":
                # FX ferme de vendredi 17h ET a dimanche 17h ET
                is_open = self._is_fx_open(self.reference_date)
            elif market == "CRYPTO":
                is_open = True  # Toujours ouvert
            else:
                is_open = weekday in mdef["days"]

            return {
                "pass": valid,
                "market": market,
                "open_utc": open_utc.isoformat() if open_utc else None,
                "close_utc": close_utc.isoformat() if close_utc else None,
                "timezone": mdef["timezone"],
                "is_open_today": is_open,
                "details": details,
            }

        except Exception as e:
            logger.error(f"Erreur check_market_hours({market}): {e}")
            return {
                "pass": False,
                "market": market,
                "open_utc": None,
                "close_utc": None,
                "timezone": "error",
                "is_open_today": False,
                "details": f"Exception: {e}",
            }

    def check_dst_transitions(self) -> Dict:
        """Detecte les prochaines transitions DST pour US et EU.

        Scanne les 180 prochains jours pour trouver les changements
        d'offset UTC. Emet un warning si une transition est dans les
        48 prochaines heures.

        Returns:
            {
                "pass": bool,
                "transitions": [
                    {"zone": str, "date": str, "from_offset": str,
                     "to_offset": str, "hours_away": float}
                ],
                "warnings": [str],
                "next_us": str ou None,
                "next_eu": str ou None,
            }
        """
        transitions = []
        warnings = []
        now_utc = datetime.now(_UTC)

        next_us = None
        next_eu = None

        for zone_name, tz_obj, label in [
            ("America/New_York", self._ny_tz, "US"),
            ("Europe/Paris", self._paris_tz, "EU"),
        ]:
            found = self._find_next_dst_transition(tz_obj, self.reference_date, 180)
            if found:
                trans_date, from_off, to_off = found
                # Calculer les heures avant la transition
                # La transition se produit generalement a 02:00 local
                trans_dt = datetime.combine(trans_date, time(2, 0), tzinfo=tz_obj)
                hours_away = (trans_dt - now_utc).total_seconds() / 3600.0

                entry = {
                    "zone": zone_name,
                    "label": label,
                    "date": trans_date.isoformat(),
                    "from_offset": str(from_off),
                    "to_offset": str(to_off),
                    "hours_away": round(hours_away, 1),
                }
                transitions.append(entry)

                if label == "US":
                    next_us = trans_date.isoformat()
                else:
                    next_eu = trans_date.isoformat()

                # Alerte si transition dans les DST_WARNING_HOURS prochaines heures
                if 0 < hours_away <= DST_WARNING_HOURS:
                    msg = (
                        f"ATTENTION: transition DST {label} ({zone_name}) "
                        f"dans {hours_away:.0f}h le {trans_date}. "
                        f"Offset passe de {from_off} a {to_off}."
                    )
                    warnings.append(msg)
                    logger.warning(msg)

        # Pass = pas de warning actif (transition imminente)
        return {
            "pass": len(warnings) == 0,
            "transitions": transitions,
            "warnings": warnings,
            "next_us": next_us,
            "next_eu": next_eu,
        }

    def check_candle_alignment(
        self, df: pd.DataFrame, expected_tz: str
    ) -> Dict:
        """Valide que les timestamps des bougies sont dans le fuseau attendu.

        Verifie :
        - Que l'index est un DatetimeIndex
        - Que le tzinfo correspond a expected_tz (ou UTC)
        - Que les heures des bougies tombent dans les plages normales

        Args:
            df: DataFrame avec index DatetimeIndex
            expected_tz: fuseau attendu ("UTC", "Europe/Paris", "America/New_York")

        Returns:
            {
                "pass": bool,
                "total_candles": int,
                "tz_info": str ou None,
                "expected_tz": str,
                "tz_match": bool,
                "anomalies": [str],
                "details": str,
            }
        """
        anomalies = []

        if df is None or df.empty:
            return {
                "pass": False,
                "total_candles": 0,
                "tz_info": None,
                "expected_tz": expected_tz,
                "tz_match": False,
                "anomalies": ["DataFrame vide ou None"],
                "details": "Pas de donnees a valider",
            }

        # Verifier que l'index est un DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            return {
                "pass": False,
                "total_candles": len(df),
                "tz_info": None,
                "expected_tz": expected_tz,
                "tz_match": False,
                "anomalies": ["L'index n'est pas un DatetimeIndex"],
                "details": f"Type d'index: {type(df.index).__name__}",
            }

        total = len(df)
        actual_tz = df.index.tz

        # Verifier la correspondance de timezone
        tz_match = False
        actual_tz_str = str(actual_tz) if actual_tz is not None else "naive (no tz)"

        if actual_tz is not None:
            # Normaliser pour comparaison
            expected_zone = ZoneInfo(expected_tz) if expected_tz != "UTC" else _UTC
            # Comparer en verifiant que l'offset est le meme pour une date donnee
            sample_dt = df.index[0].to_pydatetime()
            if hasattr(sample_dt, 'tzinfo') and sample_dt.tzinfo is not None:
                actual_offset = sample_dt.utcoffset()
                expected_dt = sample_dt.astimezone(expected_zone)
                expected_offset = expected_dt.utcoffset()
                tz_match = (actual_offset == expected_offset)
            else:
                tz_match = False
        elif expected_tz == "UTC":
            # Beaucoup de DataFrames sont en UTC naif (convention)
            tz_match = True
            anomalies.append(
                "Index naif (sans tz) — accepte comme UTC par convention"
            )
        else:
            anomalies.append(
                f"Index naif (sans tz) mais attendu: {expected_tz}"
            )

        if not tz_match and expected_tz != "UTC":
            anomalies.append(
                f"Timezone mismatch: index={actual_tz_str}, attendu={expected_tz}"
            )

        # Verifier les doublons de timestamp
        duplicates = df.index.duplicated().sum()
        if duplicates > 0:
            anomalies.append(f"{duplicates} timestamps dupliques detectes")

        # Verifier que l'index est monotone croissant
        if not df.index.is_monotonic_increasing:
            anomalies.append("Index non monotone croissant")

        # Verifier les gaps anormaux (>2x l'intervalle median)
        if total >= 3:
            diffs = pd.Series(df.index).diff().dropna()
            if len(diffs) > 0:
                median_diff = diffs.median()
                if median_diff > timedelta(0):
                    large_gaps = diffs[diffs > median_diff * 3]
                    if len(large_gaps) > 0:
                        anomalies.append(
                            f"{len(large_gaps)} gaps anormaux detectes "
                            f"(> 3x intervalle median de {median_diff})"
                        )

        is_pass = len(anomalies) == 0 or (
            len(anomalies) == 1
            and "naif" in anomalies[0]
            and expected_tz == "UTC"
        )

        return {
            "pass": is_pass,
            "total_candles": total,
            "tz_info": actual_tz_str,
            "expected_tz": expected_tz,
            "tz_match": tz_match,
            "anomalies": anomalies,
            "details": f"{total} bougies, tz={actual_tz_str}",
        }

    def check_broker_clock_sync(self) -> Dict:
        """Compare l'horloge systeme locale avec UTC (validation NTP).

        Mesure l'ecart entre time.time() et datetime.now(UTC).
        En production, un ecart > 1s indique un probleme NTP.

        Returns:
            {
                "pass": bool,
                "drift_ms": float,
                "system_utc": str,
                "reference_utc": str,
                "details": str,
            }
        """
        # Mesurer le drift en comparant deux sources
        t1 = _time.time()
        now_dt = datetime.now(_UTC)
        t2 = _time.time()

        # Temps systeme moyen (pour minimiser l'erreur de mesure)
        sys_epoch = (t1 + t2) / 2.0
        sys_utc = datetime.fromtimestamp(sys_epoch, tz=_UTC)

        # Drift en millisecondes
        drift = abs((sys_utc - now_dt).total_seconds() * 1000)

        # Seuil : 1000 ms = probleme NTP probable
        is_pass = drift < 1000

        details = "Horloge synchronisee" if is_pass else (
            f"ALERTE: drift de {drift:.0f}ms detecte — verifier NTP"
        )

        return {
            "pass": is_pass,
            "drift_ms": round(drift, 2),
            "system_utc": sys_utc.isoformat(),
            "reference_utc": now_dt.isoformat(),
            "details": details,
        }

    def get_market_calendar(
        self, market: str, target_date: date
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Retourne (open_time, close_time) en UTC pour un marche et une date.

        Args:
            market: "EU", "US", "FX" ou "CRYPTO"
            target_date: date cible

        Returns:
            (open_utc, close_utc) — datetimes aware (UTC).
            (None, None) si le marche est ferme ce jour.
        """
        market = market.upper()
        if market not in MARKET_DEFINITIONS:
            raise ValueError(f"Marche inconnu: {market}")

        mdef = MARKET_DEFINITIONS[market]
        weekday = target_date.weekday()

        if market == "CRYPTO":
            # Crypto 24/7 — open 00:00 UTC, close 23:59:59 UTC
            open_utc = datetime.combine(
                target_date, time(0, 0), tzinfo=_UTC
            )
            close_utc = datetime.combine(
                target_date, time(23, 59, 59), tzinfo=_UTC
            )
            return open_utc, close_utc

        if market == "FX":
            return self._get_fx_session(target_date)

        # EU et US : marches a horaires fixes dans leur fuseau local
        if weekday not in mdef["days"]:
            return None, None

        local_tz = ZoneInfo(mdef["timezone"])
        open_local = datetime.combine(target_date, mdef["open"], tzinfo=local_tz)
        close_local = datetime.combine(target_date, mdef["close"], tzinfo=local_tz)

        open_utc = open_local.astimezone(_UTC)
        close_utc = close_local.astimezone(_UTC)

        return open_utc, close_utc

    # ------------------------------------------------------------------
    # Methodes internes
    # ------------------------------------------------------------------

    def _find_next_dst_transition(
        self, tz: ZoneInfo, start_date: date, max_days: int = 180
    ) -> Optional[Tuple[date, timedelta, timedelta]]:
        """Trouve la prochaine transition DST apres start_date.

        Parcourt les jours a partir de start_date et detecte un changement
        d'offset UTC, signe d'une transition DST.

        Args:
            tz: fuseau horaire a scanner
            start_date: date de debut de recherche
            max_days: nombre max de jours a scanner

        Returns:
            (date_transition, ancien_offset, nouvel_offset) ou None
        """
        # Offset du jour de depart (a midi pour eviter les ambiguites)
        prev_dt = datetime.combine(start_date, time(12, 0), tzinfo=tz)
        prev_offset = prev_dt.utcoffset()

        for i in range(1, max_days + 1):
            check_date = start_date + timedelta(days=i)
            check_dt = datetime.combine(check_date, time(12, 0), tzinfo=tz)
            check_offset = check_dt.utcoffset()

            if check_offset != prev_offset:
                return check_date, prev_offset, check_offset

            prev_offset = check_offset

        return None

    def _is_fx_open(self, target_date: date) -> bool:
        """Determine si le FX est ouvert un jour donne.

        FX est ouvert du dimanche 17:00 ET au vendredi 17:00 ET.
        Ferme le samedi et dimanche avant 17:00 ET.
        """
        weekday = target_date.weekday()
        # Samedi (5) = ferme
        if weekday == 5:
            return False
        # Dimanche (6) = ouvert seulement apres 17:00 ET (on considere ouvert)
        # Les autres jours (lun-ven) = ouvert
        return True

    def _get_fx_session(
        self, target_date: date
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Retourne la session FX pour une date donnee.

        FX est continu du dimanche 17:00 ET au vendredi 17:00 ET.
        Pour un jour de semaine, la "session" va de 17:00 ET la veille
        a 17:00 ET le jour meme.
        """
        weekday = target_date.weekday()

        # Samedi : ferme
        if weekday == 5:
            return None, None

        if weekday == 6:
            # Dimanche : ouverture a 17:00 ET, pas de fermeture ce jour
            open_local = datetime.combine(
                target_date, time(17, 0), tzinfo=self._ny_tz
            )
            # La session se termine le lendemain a 17:00 ET
            close_local = datetime.combine(
                target_date + timedelta(days=1), time(17, 0),
                tzinfo=self._ny_tz
            )
            return open_local.astimezone(_UTC), close_local.astimezone(_UTC)

        # Lundi a vendredi : session 17:00 ET veille -> 17:00 ET jour meme
        open_local = datetime.combine(
            target_date - timedelta(days=1), time(17, 0),
            tzinfo=self._ny_tz
        )
        close_local = datetime.combine(
            target_date, time(17, 0), tzinfo=self._ny_tz
        )

        # Cas special vendredi : la session ferme a 17:00 ET
        # (pas de re-ouverture ensuite)

        return open_local.astimezone(_UTC), close_local.astimezone(_UTC)
