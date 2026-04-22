"""TTL 72h pour incidents (Phase 3.2 desk productif 2026-04-22).

Un incident est considere ACTIVE si:
  1. son timestamp est dans les TTL_HOURS dernieres heures (recent), OU
  2. il appartient a une chaine (meme severity+book+category) dont le plus
     recent incident est lui-meme recent.

Semantique: "tant que le meme probleme se re-declenche dans la fenetre de
72h, le compteur reste arme; sinon on considere l'incident comme resolu
par epuisement naturel apres 72h".

Utilise par:
  - core.governance.promotion_gate._count_recent_incidents_24h (can_go_live_micro)
  - scripts.alpaca_go_25k_gate._count_incidents_open_p0p1

TTL 72h a ete choisi parce que:
  - plus court que la plupart des gates hebdo/mensuels (pas de faux positif sur 7j)
  - assez long pour qu'un probleme reel declenche >=1 re-trigger (la plupart des
    incidents reconciliation ou preflight re-firent dans l'heure si encore ouverts)
  - dans l'esprit "allegement des controles" Marc 2026-04-22: ne plus laisser
    un incident ancien bloquer une decision capital actuelle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

UTC = timezone.utc

TTL_HOURS_DEFAULT = 72


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _incident_key(entry: dict) -> tuple:
    """Cle de groupage: (severity, book, category)."""
    sev = (entry.get("severity") or "").upper()
    book = (entry.get("context") or {}).get("book") or entry.get("book") or ""
    cat = entry.get("category") or ""
    return (sev, book, cat)


def filter_active_incidents(
    incidents: Iterable[dict],
    ttl_hours: int = TTL_HOURS_DEFAULT,
    now: datetime | None = None,
) -> list[dict]:
    """Retourne la sous-liste des incidents consideres ACTIVES.

    Regroupe par (severity, book, category). Un groupe est actif si son
    incident le plus recent a un age < ttl_hours. Si oui, TOUS les incidents
    du groupe sont retournes; sinon AUCUN.
    """
    now = now or datetime.now(UTC)
    ttl = timedelta(hours=ttl_hours)

    groups: dict[tuple, list[dict]] = {}
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        ts = _parse_ts(inc.get("timestamp"))
        if ts is None:
            continue
        # Enrichir entry avec timestamp parse pour usage aval
        inc_copy = dict(inc)
        inc_copy["_ts_parsed"] = ts
        key = _incident_key(inc)
        groups.setdefault(key, []).append(inc_copy)

    active: list[dict] = []
    for key, group in groups.items():
        latest_ts = max(inc["_ts_parsed"] for inc in group)
        age = now - latest_ts
        if age < ttl:
            active.extend(group)
    return active


def is_incident_active(
    incident: dict,
    all_incidents_in_context: Iterable[dict],
    ttl_hours: int = TTL_HOURS_DEFAULT,
    now: datetime | None = None,
) -> bool:
    """True si cet incident specifique doit etre considere actif."""
    active = filter_active_incidents(
        all_incidents_in_context, ttl_hours=ttl_hours, now=now,
    )
    target_ts = _parse_ts(incident.get("timestamp"))
    if target_ts is None:
        return False
    target_key = _incident_key(incident)
    for inc in active:
        if inc.get("_ts_parsed") == target_ts and _incident_key(inc) == target_key:
            return True
    return False
