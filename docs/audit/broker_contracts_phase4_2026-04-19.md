# Audit broker contracts + fail-closed — Phase 4 XXL plan (2026-04-19)

## Findings pre-fix

- Contracts existaient dans `core/broker/contracts/{binance,ibkr,alpaca}_contracts.py`
  mais **0 tests** -> impossible de garantir qu'ils detectent les API breaking changes.
- ContractRunner defini dans `contract_runner.py` mais **non wire dans le worker**
  -> dead code, jamais execute.
- BrokerHealthRegistry existe mais pas de pont avec ContractRunner pour le
  fail-closed sur violations contractuelles.

## Implementation Phase 4

### 1. Tests de regression (18 tests)
- `tests/test_broker_contracts.py` :
  - 7 tests Binance (account, order, klines, margin, edge cases)
  - 4 tests IBKR (account_info, positions_list)
  - 2 tests Alpaca (account, order)
  - 5 tests ContractRunner (tolerance 1->WARN, 3->CRITICAL, reset, healthy, metrics)
- Tous PASS.

### 2. Validation cycle module
- Nouveau `core/broker/contracts/validation_cycle.py` :
  - `run_contract_validation_cycle(runner, binance, ibkr, alpaca, health)` orchestrator
  - Calls READ-ONLY API (account, positions) sur chaque broker dispo
  - Bridge vers `BrokerHealthRegistry.mark_degraded()` sur 3 violations consecutives
  - Smoke test OK : avec brokers None -> {}, avec mocks -> endpoints_tested

### 3. Documentation wiring (worker.py main())
Pattern a integrer en Phase 6 (reconciliation broker, plus large):

```python
from core.broker.contracts.contract_runner import ContractRunner
from core.broker.contracts.validation_cycle import run_contract_validation_cycle

_contract_runner = ContractRunner(
    alert_callback=_send_alert,
    metrics_callback=_metrics.gauge,
)

def _contract_validation_wrapper():
    run_contract_validation_cycle(
        runner=_contract_runner,
        binance_broker=_binance_broker_instance,
        ibkr_client=_ibkr_client,
        alpaca_client=_alpaca_client,
        health_registry=_broker_health,
    )

scheduler.add_job(
    _contract_validation_wrapper,
    trigger="interval", hours=1, id="contract_validation",
)
```

## Score post-Phase 4

- Contract validators tests: **9/10** (avant 3/10 — pas de tests)
- ContractRunner tolerance/escalation: **9/10** (avant 5/10 — pas de tests)
- Wiring runtime broker -> ContractRunner: **5/10** (framework pret + doc, integration Phase 6)
- Fail-closed integration BrokerHealthRegistry: **6/10** (bridge pret, integration Phase 6)

## Next phases related

- **Phase 5 pre-order guard**: utiliser `is_contract_healthy(broker)` comme 1 des 6 checks
- **Phase 6 reconciliation**: wire validation_cycle dans worker.py main() + scheduler
- **Phase 12 metrics pipeline**: ContractRunner _metrics_cb deja prevu, brancher au pipeline
