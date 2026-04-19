# Audit pre_order_guard + audit_trail — Phase 5 XXL plan (2026-04-19)

## Findings pre-fix
- `core/governance/pre_order_guard.py` implemente 7 checks (mieux que les "6"
  documentes dans CLAUDE.md):
  1. book non-vide
  2. strategy_id non-vide
  3. book existe dans books_registry.yaml
  4. book.mode_authorized != "disabled"
  5. mode_authorized == "live_allowed" en mode live
  6. strategy_id dans live_whitelist + status live_*
  7. safety_mode_flag inactif
  8. kill_switches_scoped (global > broker > book > strategy)
  9. book_health != BLOCKED (en live), DEGRADED conditionnel sur cause
- `core/governance/audit_trail.py` defini avec API record_order_decision()
  + read_recent() — 0 tests jusqu'a maintenant.
- Utilise dans worker.py SEULEMENT a la ligne 2729 (futures cycle) — 28+ autres
  entrypoints LIVE n'audient PAS.

## Implementation Phase 5

### 1. Tests regression (11 nouveaux)
- `tests/test_pre_order_guard_audit_trail.py`:
  - 5 tests pre_order_guard rejections (empty book, empty strat, unknown book,
    bypass guard hors pytest, bypass valide en pytest)
  - 6 tests audit_trail (round-trip, filtre book, extra field, fail-safe write,
    concurrent 20 threads x 5 records sans corruption, file rotation per day)

### 2. Coverage pre_order_guard runtime
- core/alpaca_client/client.py:445 — DEJA fail-closed (raise si pre_order_guard
  unavailable)
- core/binance_broker.py — a auditer Phase 6
- core/ibkr_bracket.py — a auditer Phase 6

## Score post-Phase 5

- pre_order_guard logic: **9/10** (avant 7/10 — pas de tests)
- audit_trail framework: **9/10** (avant 4/10 — pas de tests, seul 1 callsite)
- audit_trail coverage runtime: **3/10** (1 callsite sur ~28+) — a etendre Phase 6
- Fail-closed enforcement: **9/10** (Alpaca impl confirme blocking)

## Recommandations integration Phase 6

Pour passer audit_trail coverage 3/10 -> 9/10:
1. Wrapper `record_order_decision()` dans chaque chemin order placement majeur
2. Inventaire 28 entrypoints (cf reports/runtime/live_entrypoints_inventory.md)
3. 1 commit par broker (binance / ibkr / alpaca)
