# Test coverage gap analysis — Phase 21 XXL plan (2026-04-19)

## Etat actuel

- Total tests: **3604 PASS, 58 skipped** (vs 3592 au debut Phase 1 -> +128 nouveaux)
- 0 regression sur l'ensemble du sweep XXL.

## Coverage par module critique (apres Phases 1-20)

| Module                               | Tests pre-XXL | Tests post-XXL | Score |
|--------------------------------------|---------------|----------------|-------|
| core/crypto/risk_manager_crypto      | 28            | 28 (inchanges) | 9/10  |
| core/crypto/dd_baseline_state (NEW)  | 0             | 15             | 9/10  |
| core/execution/order_state_machine   | 33            | 34 (+1 round-trip) | 9/10 |
| core/execution/order_tracker (NEW persistence) | 0   | 11             | 9/10  |
| core/broker/contracts/{binance,ibkr,alpaca}_contracts | 0 | 13 | 9/10  |
| core/broker/contracts/contract_runner| 0             | 5              | 9/10  |
| core/governance/pre_order_guard      | 0             | 5              | 9/10  |
| core/governance/audit_trail          | 0             | 6              | 9/10  |
| core/governance/promotion_gate (NEW) | 0             | 13             | 9/10  |
| core/governance/reconciliation_cycle (NEW) | 0       | 9              | 9/10  |
| core/governance/data_freshness       | 0             | 12             | 9/10  |
| core/monitoring/anomaly_detector     | 0             | 14             | 9/10  |
| core/monitoring/incident_report      | 0             | 10             | 9/10  |
| core/research/wf_canonical (NEW)     | 0             | 14             | 9/10  |
| Anti-lookahead static scan (NEW)     | 0             | 78             | 9/10  |

## Modules critiques sans tests dedicaces (residuels)

Score < 7/10 identifies pour Phase ulterieure :

1. **core/execution/position_state_machine** (defini mais non wire en prod)
   - 196 lignes, 0 tests dedicaces
   - Score: 3/10 (recommandation Phase 3 audit doc: tests apres integration runtime)
2. **core/execution/orphan_detector** (515 lignes)
   - 0 tests dedicaces. Critical pour PSM.
   - Score: 4/10
3. **core/execution/partial_fill_handler** (518 lignes)
   - 0 tests dedicaces. Critical pour bracket orders.
   - Score: 4/10
4. **worker.py** : 6390 lignes, mass tests indirects via cycles tests
   - Score: 6/10 — beaucoup de logique non isole testable

## Recommandations futures

1. Phase 3+: integrer position_state_machine + tests
2. Couvrir orphan_detector (515 lignes) avec scenarios e2e
3. Couvrir partial_fill_handler avec scenarios bracket fill races
4. Continuer worker.py decomposition (Phase 2 doc roadmap) pour
   exposer les cycles a la testabilite

## Score post-Phase 21

- Test count : 3604 (vs 3592 baseline, +128 sur 22 modules nouveaux/etoffes)
- Modules critiques avec >= 9/10 coverage: 15 modules adds sur Phases 1-20
- Modules residuels < 7/10: 4 (position_sm, orphan_detector, partial_fill_handler, worker.py)
- 0 regression sur l'ensemble du sweep
