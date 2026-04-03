"""
ROC-008 — Timezone Capital Allocator.

Alloue le capital dynamiquement selon le creneau horaire (CET).
Le capital non utilise par un creneau est disponible pour les suivants.

Creneaux (CET) :
  ASIA_CRYPTO  (0h-8h)  : crypto seulement, max 25%
  EU_MORNING   (8h-14h) : EU + FX + crypto, max 35%
  OVERLAP      (14h-18h): tous marches, max 50%
  US_EVENING   (18h-22h): US + FX + futures + crypto, max 40%
  NIGHT        (22h-0h) : FX + crypto seulement, max 20%
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)

# CRO FIX: utiliser zoneinfo pour gerer DST (CET/CEST automatiquement)
try:
    import zoneinfo
    _PARIS_TZ = zoneinfo.ZoneInfo("Europe/Paris")
except Exception:
    _PARIS_TZ = None

# --- Schedule par defaut ---
DEFAULT_SCHEDULE = {
    "ASIA_CRYPTO": {
        "start": 0,
        "end": 8,
        "max_pct": 25.0,
        "markets": ["crypto"],
    },
    "EU_MORNING": {
        "start": 8,
        "end": 14,
        "max_pct": 35.0,
        "markets": ["eu", "fx", "crypto"],
    },
    "OVERLAP": {
        "start": 14,
        "end": 18,
        "max_pct": 50.0,
        "markets": ["us", "eu", "fx", "futures", "crypto"],
    },
    "US_EVENING": {
        "start": 18,
        "end": 22,
        "max_pct": 40.0,
        "markets": ["us", "fx", "futures", "crypto"],
    },
    "NIGHT": {
        "start": 22,
        "end": 24,
        "max_pct": 20.0,
        "markets": ["fx", "crypto"],
    },
}


class TimezoneCapitalAllocator:
    """Allocateur de capital dynamique par creneau horaire.

    Gere la repartition du capital disponible selon l'heure CET
    et les marches actifs a chaque moment.
    """

    def __init__(
        self,
        total_capital: float,
        reserve_pct: float = 0.20,
        schedule: Dict | None = None,
    ):
        """
        Args:
            total_capital: capital total du portefeuille
            reserve_pct: pourcentage de reserve toujours maintenu (0.0 - 1.0)
            schedule: schedule custom (utilise DEFAULT_SCHEDULE si None)
        """
        if total_capital <= 0:
            raise ValueError("total_capital doit etre > 0")
        if not 0 <= reserve_pct < 1:
            raise ValueError("reserve_pct doit etre entre 0 et 1")

        self.total_capital = total_capital
        self.reserve_pct = reserve_pct
        self.schedule = schedule if schedule is not None else dict(DEFAULT_SCHEDULE)

        # Capital deployable (hors reserve)
        self.deployable_capital = total_capital * (1 - reserve_pct)

        # Historique d'utilisation par slot (pour reporting)
        self._utilization_history: List[dict] = []

        logger.info(
            f"TimezoneCapitalAllocator initialise — "
            f"capital=${total_capital:,.0f}, reserve={reserve_pct*100:.0f}%, "
            f"deployable=${self.deployable_capital:,.0f}, "
            f"{len(self.schedule)} slots"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_available_capital(
        self, hour_cet: int, blocked_margin: float = 0.0
    ) -> dict:
        """Calcule le capital disponible pour le creneau horaire actuel.

        Args:
            hour_cet: heure CET (0-23)
            blocked_margin: marge deja bloquee par les positions existantes

        Returns:
            {
                available: float,
                max_for_slot: float,
                markets_active: [str],
                slot_name: str,
                reserve: float,
                deployable: float,
            }
        """
        slot = self.get_current_slot(hour_cet)
        slot_name = slot["name"]
        max_pct = slot["max_pct"]
        markets = slot["markets"]

        # Capital max pour ce slot
        max_for_slot = self.deployable_capital * (max_pct / 100.0)

        # Capital disponible = max du slot - marge deja bloquee
        available = max(0.0, max_for_slot - blocked_margin)

        # Enregistrer l'utilisation
        self._utilization_history.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "slot": slot_name,
            "hour_cet": hour_cet,
            "max_for_slot": round(max_for_slot, 2),
            "blocked_margin": round(blocked_margin, 2),
            "available": round(available, 2),
        })

        return {
            "available": round(available, 2),
            "max_for_slot": round(max_for_slot, 2),
            "markets_active": markets,
            "slot_name": slot_name,
            "reserve": round(self.total_capital * self.reserve_pct, 2),
            "deployable": round(self.deployable_capital, 2),
        }

    def get_current_slot(self, hour_cet: int | None = None) -> dict:
        """Retourne les informations du creneau horaire actuel.

        Args:
            hour_cet: heure CET (0-23). Si None, utilise l'heure systeme.

        Returns:
            {name, start, end, max_pct, markets}
        """
        if hour_cet is None:
            if _PARIS_TZ:
                now_cet = datetime.now(_PARIS_TZ)
            else:
                now_cet = datetime.now(UTC) + timedelta(hours=1)
            hour_cet = now_cet.hour

        for slot_name, config in self.schedule.items():
            start = config["start"]
            end = config["end"]

            # Gestion du cas minuit (ex: 22h-0h = 22-24)
            if start <= hour_cet < end:
                return {
                    "name": slot_name,
                    "start": start,
                    "end": end,
                    "max_pct": config["max_pct"],
                    "markets": list(config["markets"]),
                }

        # Fallback si aucun slot ne correspond (ne devrait pas arriver)
        logger.warning(f"Aucun slot pour l'heure CET {hour_cet} — fallback NIGHT")
        night = self.schedule.get("NIGHT", DEFAULT_SCHEDULE["NIGHT"])
        return {
            "name": "NIGHT",
            "start": night["start"],
            "end": night["end"],
            "max_pct": night["max_pct"],
            "markets": list(night["markets"]),
        }

    def get_utilization_report(self) -> dict:
        """Rapport d'utilisation du capital par creneau.

        Returns:
            {
                total_entries: int,
                by_slot: {slot_name: {count, avg_available, avg_blocked}},
                history: [dict] (derniers 100 enregistrements),
            }
        """
        by_slot: Dict[str, dict] = {}

        for entry in self._utilization_history:
            slot = entry["slot"]
            if slot not in by_slot:
                by_slot[slot] = {
                    "count": 0,
                    "total_available": 0.0,
                    "total_blocked": 0.0,
                }
            by_slot[slot]["count"] += 1
            by_slot[slot]["total_available"] += entry["available"]
            by_slot[slot]["total_blocked"] += entry["blocked_margin"]

        # Calculer les moyennes
        report_by_slot = {}
        for slot, data in by_slot.items():
            count = data["count"]
            report_by_slot[slot] = {
                "count": count,
                "avg_available": round(data["total_available"] / count, 2),
                "avg_blocked": round(data["total_blocked"] / count, 2),
            }

        return {
            "total_entries": len(self._utilization_history),
            "by_slot": report_by_slot,
            "history": self._utilization_history[-100:],
        }

    def is_market_active(
        self, market: str, hour_cet: int | None = None
    ) -> bool:
        """Verifie si un marche est actif dans le creneau horaire.

        Args:
            market: type de marche ("us", "eu", "fx", "futures", "crypto")
            hour_cet: heure CET (0-23). Si None, utilise l'heure systeme.

        Returns:
            True si le marche est actif
        """
        slot = self.get_current_slot(hour_cet)
        return market.lower() in [m.lower() for m in slot["markets"]]
