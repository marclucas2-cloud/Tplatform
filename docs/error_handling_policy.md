# Error Handling Policy — P1.3 live hardening

## Constat

`worker.py` contient **233 `except Exception`** (1 broad except toutes les 26 lignes). Cette politique absorbe silencieusement des erreurs critiques et rend l'audit post-incident impossible.

## Politique cible

### Règle d'or

> Une erreur sur un chemin critique (risk, sizing, broker call, reconcile)
> doit être **typée**, **logguée structurellement**, et **fail-close le live**
> si elle est critique. Jamais absorbée silencieusement.

### Classification des catch

| Classe | Pattern | Action |
|---|---|---|
| **Critique** | Sizing, risk, broker auth, broker connect, whitelist, state corruption | Log + re-raise, fail-closed |
| **Recuperable** | Broker data missing, retry possible | Log warning + retry ou skip |
| **Cosmetique** | Dashboard display, logging, metrics | Log debug + continue |

### Hiérarchie d'exceptions

Définie dans `core/errors.py`. Tout nouveau chemin critique doit utiliser cette hiérarchie :

```python
from core.errors import (
    BrokerConnectionError, BrokerOrderError, BrokerAuthError,
    SizingError, RiskCheckError, InsufficientCapitalError,
    ReconcileError, PositionMismatchError,
    WhitelistError, BookHealthError,
    StatePersistenceError, StateCorruptionError,
)
```

### Pattern préféré

**AVANT** (anti-pattern) :
```python
try:
    order = broker.place_order(symbol, side, qty)
except Exception as e:
    logger.warning(f"Order failed: {e}")
    return  # silent absorption, no audit trail
```

**APRÈS** (politique P1.3) :
```python
try:
    order = broker.place_order(symbol, side, qty)
except BrokerConnectionError as e:
    logger.critical(f"[{strategy_id}] broker disconnected — fail-closed: {e}")
    record_order_decision(..., result="FAILED", broker_response={"error": str(e)})
    raise  # propagate to caller — book goes DEGRADED
except BrokerOrderError as e:
    logger.error(f"[{strategy_id}] order rejected by broker: {e}")
    record_order_decision(..., result="REJECTED", broker_response={"error": str(e)})
    return None  # skip this signal, continue with others
except Exception as e:
    # Unknown error = potential bug — fail-closed by design
    logger.critical(f"[{strategy_id}] unknown error in place_order: {e}", exc_info=True)
    record_order_decision(..., result="FAILED", broker_response={"error": "unknown"})
    raise  # re-raise to break the cycle and alert
```

## Plan de migration progressive

Les 233 broad except existants ne seront PAS tous remplacés d'un coup. Migration par vagues :

### Vague 1 — Sizing path (7 catches ciblés)
- Fonctions : `_get_global_nav`, sizing crypto, sizing futures
- Deadline : déjà fait en P0.1 (NAV fail-closed)

### Vague 2 — Broker execution path (15-20 catches)
- Fonctions : `_run_futures_cycle`, `run_crypto_cycle` (order placement sections)
- Deadline : prochaine itération live hardening (J+7)

### Vague 3 — Reconcile path (10 catches)
- Fonctions : `reconcile_positions_at_startup`, crypto reconcile, bracket watchdog
- Deadline : J+14

### Vague 4 — Risk manager calls (20 catches)
- Fonctions : `check_risk`, `live_risk_cycle`, kill switch
- Deadline : J+21

### Vague 5 — Le reste (180+ catches)
- Reporting, dashboard, logging, non-critique
- Deadline : background task, pas de deadline

## Règles pour les nouveaux développements

À partir du 2026-04-15 :

1. **Zero broad except sur nouveau code critique.** Utiliser la hiérarchie `core/errors.py`.
2. **Log structuré** : utiliser `logger.critical` / `logger.error` / `logger.warning` selon sévérité, jamais `logger.info` sur un catch.
3. **Audit trail** : tout échec d'ordre live doit appeler `record_order_decision(..., result="FAILED"|"REJECTED")`.
4. **Fail-closed** : en cas de doute, préférer refuser l'ordre (skip) plutôt que d'absorber silencieusement.
5. **exc_info=True** pour les critiques : `logger.critical(msg, exc_info=True)` pour avoir la stack trace dans les logs.

## Responsabilité

- **Claude agent** : respecte cette politique pour tout nouveau code critique, ne régresse pas sur les patches existants.
- **Marc (PO)** : review les changements qui introduisent du broad except sur un chemin critique, refuse si pas justifié.
