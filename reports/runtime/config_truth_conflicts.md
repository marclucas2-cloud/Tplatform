# Config truth conflicts — sources de vérité divergentes

**Date** : 2026-04-16
**Contexte** : Phase 0 du plan TODO XXL DESK PERSO 10/10
**Méthode** : grep + diff entre sources prétendant définir les mêmes faits

## Sources analysées

| Fichier | Rôle prétendu | Status |
|---|---|---|
| `config/live_whitelist.yaml` | **Source canonique** strats live (doctrine) | enforce partiellement |
| `config/strategies_eu.yaml` | Activation strats EU via `enabled: true` | bypass canonical |
| `config/limits_live.yaml` | Limites de risque live | utilisé par risk_manager_live |
| `config/regime.yaml` | Activation matrix par régime | utilisé par v12_regime_cycle |
| `data/calendar_bce.csv` | Dates ECB | utilisé par macro_ecb |
| `worker.py:_STRAT_DISPLAY_TO_ID` | Mapping display→canonical_id | hardcoded |
| `strategies/crypto/__init__.py:CRYPTO_STRATEGIES` | Registry import-time crypto | import-driven |
| `data/kill_switch_state.json` | Thresholds kill switch | overrides DEFAULT_THRESHOLDS code |

## Conflits identifiés

### C1 — Kill switch thresholds (code vs state vs limits.yaml)

| Source | daily_loss_pct | weekly | monthly |
|---|---:|---:|---:|
| `core/kill_switch_live.py:DEFAULT_THRESHOLDS` (code) | 0.015 | 0.03 (5d) | 0.05 |
| `data/kill_switch_state.json` (state) | **0.05** | 0.08 | 0.12 |
| `config/limits_live.yaml:circuit_breakers` | 0.015 | 0.03 (weekly) | n/a |
| `config/limits_live.yaml:kill_switch` | n/a | 0.03 | 0.05 |

**Divergence** : 3 sources disent des choses différentes. Le state actif (0.05/0.08/0.12) **override** les défauts code (0.015/0.03/0.05) qui matchent limits.yaml.

**Severity** : 🟠 HIGH — la doctrine "limits.yaml source de vérité" est cassée par state file.

**Fix** : supprimer thresholds dans state JSON, charger toujours depuis limits.yaml au boot.

### C2 — gold_trend_mgc status (whitelist vs hardcoded mapping)

| Source | Status |
|---|---|
| `config/live_whitelist.yaml` v4 | `paper_only` (downgrade 2026-04-16 V1) |
| `worker.py:_STRAT_DISPLAY_TO_ID` | Liste mapping qui inclut `gold_trend_mgc` |

**Divergence** : la whitelist dit paper_only mais le mapping worker.py considère encore la strat comme live-mappable. Le check `is_strategy_live_allowed` enforce mais le mapping est "stale".

**Severity** : 🟡 MEDIUM — pas un bypass mais confusion possible.

**Fix** : commentaire dans _STRAT_DISPLAY_TO_ID indiquant que le mapping ≠ autorisation.

### C3 — Strats crypto paper_only mais importées + executées

| Strat | live_whitelist.yaml | CRYPTO_STRATEGIES (import) | run_crypto_cycle |
|---|---|---|---|
| `borrow_rate_carry` | paper_only (P0 fix) | imported active | execute via cycle |
| `btc_dominance_rotation_v2` | disabled (P0 fix) | imported active | execute via cycle |

**Divergence pré-P0-fix** : ces strats étaient executées sans aucun check whitelist car le crypto cycle ne validait pas.

**Severity post-fix** : 🟢 LOW — `is_strategy_live_allowed` enforce maintenant (commit ed976ff). La strat reste importée mais ne peut plus placer d'ordre live.

**Action future** : import conditionnel ? Avantage = code plus propre. Inconvénient = perte de visibilité signals (qui peuvent être loggés en paper).

### C4 — strategies_eu.yaml `enabled: true` vs ibkr_eu paper_only

| Source | Statut |
|---|---|
| `config/live_whitelist.yaml:ibkr_eu` | 5 entries `paper_only` |
| `config/strategies_eu.yaml` | `enabled: true` sur strats EU |
| `scripts/live_portfolio_eu.py` | exécute `enabled: true` strats |
| `worker.py:run_intraday(EU)` | **bloqué post-P0** (commit ed976ff) |

**Divergence** : `strategies_eu.yaml` n'est pas whitelist-aware. Bypass était possible pre-P0.

**Severity post-fix** : 🟡 MEDIUM — worker bloque mais quelqu'un peut lancer `python scripts/live_portfolio_eu.py` en CLI direct.

**Fix proposé** : `live_portfolio_eu.py` doit appeler `is_strategy_live_allowed` au démarrage et fail-closed.

### C5 — Allocation crypto somme totale

| Source | Vérité |
|---|---|
| `strategies/crypto/__init__.py` line 154 | sum(allocation_pct) = 143% pre-normalize |
| `core/crypto/allocator_crypto.py` | utilise allocations renormalisées 100% |
| `live_whitelist.yaml` | aucune source d'allocation par strat (orphaned info) |

**Divergence** : la somme dépend de quelles strats sont importées avec succès. Pre-P0, normalisation silencieuse → sizing non-déterministe selon l'ordre d'import.

**Severity post-fix** : 🟡 MEDIUM — ERROR log + opt-in fail-closed via `CRYPTO_ALLOC_FAIL_CLOSED`. Mais pas par défaut.

**Fix proposé** : retirer allocation_pct des strats individuelles, mettre la canonical dans `config/crypto_allocation.yaml` ou dans whitelist.

### C6 — V11 HRP weights vs allocation_pct manuels

| Source | Type |
|---|---|
| `config/allocation.yaml` | weights HRP optimisés |
| `strategies/crypto/STRAT_*.config["allocation_pct"]` | weights manuels par strat |

**Divergence** : 2 systèmes d'allocation coexistent. Le V11 HRP cycle calcule des weights mais ne semble pas les appliquer aux strats crypto qui utilisent leurs allocations import-time.

**Severity** : 🟡 MEDIUM — incohérence de méthodologie.

**Fix proposé** : décider : soit HRP applique partout, soit allocation manuelle. Document choisi.

## Top 3 priorités fix

1. **C1 kill switch thresholds** : 3 sources divergentes → consolider sur limits.yaml uniquement, supprimer thresholds du state JSON
2. **C5 allocation crypto** : centraliser dans `config/crypto_allocation.yaml` (déjà existe mais pas autoritaire), retirer allocation_pct des strats
3. **C4 strategies_eu.yaml** : rendre `live_portfolio_eu.py` whitelist-aware en CLI direct (pas juste via worker)

## Métriques

- **6 conflits identifiés** entre sources
- **2 résolus post-P0** (C3, C4 partiellement)
- **3 HIGH/MEDIUM en attente** (C1, C5, C6)
- **0 conflit LOW** non documenté

## Conclusion

La doctrine "1 source de vérité" annoncée n'est pas tenue. La whitelist enforce sur **certains** chemins (worker.py + crypto cycle post-P0) mais reste contournable via :
- script CLI direct (live_portfolio_eu.py, paper_portfolio.py)
- state JSON qui override code defaults (kill switch)
- import-time registries (CRYPTO_STRATEGIES) qui ne consultent pas la whitelist

Phase 1 du plan XXL doit créer `core/governance/registry_loader.py` qui consolide tout ça en un seul accès canonical.
