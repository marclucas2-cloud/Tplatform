# Audit OSM/PSM recovery — Phase 3 XXL plan (2026-04-19)

## Findings

### OrderStateMachine + OrderTracker
- **Status pre-fix**: in-memory only, lost on worker crash → orphan orders on broker
  with no internal record. Recovery impossible.
- **Status post-fix (commit a venir)**:
  - `OrderStateMachine.from_dict()` ajoute pour deserialisation symetrique de `to_dict()`
  - `OrderTracker(state_path=...)` charge sur init, persiste atomique sur chaque transition
    (create / validate / submit / fill / partial / cancel / reject / error)
  - `OrderTracker.recovery_summary()` expose les orders actives recuperees pour
    reconciliation broker au boot
  - Atomic write: tempfile + os.replace + fsync (pas de partial state)
  - 4 modes de recovery: empty / OK / corrupt-file / wrong-schema (tous testes)
- **Tests ajoutes**: `tests/test_order_tracker_recovery.py` (11 tests)

### PositionStateMachine
- **Status**: classe definie dans `core/execution/position_state_machine.py` (196 lignes)
  mais **pas encore wiree dans le worker en prod**. Aucun usage trouve hors tests.
- **Recommandation**: avant de wirer, ajouter le meme pattern de persistance que
  OrderTracker. Pas urgent puisque non-utilisee, mais prerequis avant activation.
- **Phase prochaine**: integration position_state_machine + position_tracker dans le
  flux execution (apres Phase 6 reconciliation broker).

### Recovery flow boot
Worker.py main() bootstrap (post-fix):
```python
_order_tracker_path = ROOT / "data" / "state" / "order_tracker.json"
_order_tracker = OrderTracker(
    alert_callback=_send_critical_alert,
    state_path=_order_tracker_path,
)
recovery = _order_tracker.recovery_summary()
if recovery["active_order_ids"]:
    # Telegram alert: N orders still active, manual reconciliation may be needed
```

### Reconciliation TODO (Phase 6 XXL)
Le tracker recupere l'etat **interne**, mais ne sait pas si :
- l'ordre a ete fill par le broker pendant que worker etait down
- l'ordre a ete cancel par broker (timeout, marge insuffisante)
- la position broker est differente de la position interne

Phase 6 XXL ajoutera:
1. Cron 5 min `reconcile_brokers_vs_tracker()` qui compare positions broker
   vs `_order_tracker.get_active_orders()` + `binance_broker.get_positions()`
   + `ibkr.positions()` + `alpaca.list_positions()`
2. Auto-heal si discrepance < threshold (re-sync state)
3. Alerte Telegram critique si discrepance > threshold

### Score post-Phase 3
- OrderTracker recovery: **9.5/10** (avant 4/10 — pas de persistance)
- PositionStateMachine wired: **3/10** (defini mais pas integre, recommande Phase ulterieure)
- Reconciliation broker vs interne: **3/10** (sera 9/10 apres Phase 6)
