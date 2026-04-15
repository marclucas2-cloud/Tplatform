"""Hierarchie d'exceptions pour les chemins critiques live.

Objectif: remplacer progressivement les `except Exception` dans les
chemins risque/execution par des exceptions typees qui permettent
une gestion differenciee.

Regles:
  - Les exceptions CRITIQUES doivent etre LOGUEES + RE-RAISED, pas absorbees
  - Les exceptions NON-CRITIQUES peuvent etre absorbees + log warning
  - Les exceptions INCONNUES (bug) doivent casser le process en mode fail-closed

Mapping recommande:
  - Sizing / risk calc: SizingError, RiskCheckError
  - Broker calls: BrokerConnectionError, BrokerOrderError, BrokerDataError
  - Reconcile: ReconcileError
  - Governance: WhitelistError, BookHealthError
  - State files: StatePersistenceError
"""
from __future__ import annotations


class TradingPlatformError(Exception):
    """Base exception pour tout le trading platform."""


# === SIZING / RISK ===

class SizingError(TradingPlatformError):
    """Erreur pendant le calcul de sizing — l'ordre ne doit PAS partir."""


class RiskCheckError(TradingPlatformError):
    """Erreur pendant un check de risque — fail-closed oblige."""


class InsufficientCapitalError(TradingPlatformError):
    """Capital insuffisant pour sizer la position demandee."""


# === BROKER ===

class BrokerError(TradingPlatformError):
    """Erreur generique broker."""


class BrokerConnectionError(BrokerError):
    """Pas de connectivite broker — retry ou abort."""


class BrokerOrderError(BrokerError):
    """Erreur pendant un place_order / cancel_order."""


class BrokerDataError(BrokerError):
    """Erreur de donnee broker (prix manquant, bar invalide)."""


class BrokerAuthError(BrokerError):
    """Auth broker echoue — fail-closed requis."""


# === RECONCILIATION ===

class ReconcileError(TradingPlatformError):
    """Reconcile positions local vs broker en echec."""


class PositionMismatchError(ReconcileError):
    """Positions locales != positions broker, divergence non resolue."""


# === GOVERNANCE ===

class GovernanceError(TradingPlatformError):
    """Erreur dans le plan de controle (whitelist, book health, audit)."""


class WhitelistError(GovernanceError):
    """Erreur de chargement ou verification de la whitelist."""


class BookHealthError(GovernanceError):
    """Erreur pendant un health check de book."""


# === STATE ===

class StatePersistenceError(TradingPlatformError):
    """Ecriture / lecture de state file en echec."""


class StateCorruptionError(StatePersistenceError):
    """State file corrompu, reconstruction requise."""


# === UTILITIES ===

def is_critical(exc: Exception) -> bool:
    """Retourne True si l'exception doit fail-close le live.

    Les exceptions critiques:
    - BrokerConnectionError (no broker = no trading)
    - BrokerAuthError (auth broken = no trading)
    - SizingError (bad size = refuse order)
    - RiskCheckError (risk unknown = refuse order)
    - StateCorruptionError (corrupted state = refuse)
    - WhitelistError (no whitelist = refuse)
    """
    critical_types = (
        BrokerConnectionError,
        BrokerAuthError,
        SizingError,
        RiskCheckError,
        StateCorruptionError,
        WhitelistError,
    )
    return isinstance(exc, critical_types)
