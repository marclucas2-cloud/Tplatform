# Live Trading Checklist V2 — Multi-Market

> Date de creation : 2026-03-27
> Scope : Alpaca (US equities) + IBKR (EU + FX + Futures)
> A valider avant TOUT passage paper -> live sur chaque broker

---

## Pre-Launch Verification (must ALL pass)

### Broker & Connectivity

- [ ] **1. Alpaca paper verified** — 60+ days profitable, no execution errors
  - Verification: `python scripts/paper_portfolio.py --status`
  - Criteria: Sharpe > 1.5, max DD < 8%, 0 failed orders in 30 days

- [ ] **2. IBKR paper verified** — EU + FX + Futures tested
  - Verification: `python scripts/paper_portfolio_eu.py --status`
  - Criteria: All 5 EU strategies executed at least once, FX pairs tested

- [ ] **3. IBKR futures reconciliation tested**
  - Verification: Compare IBKR positions vs internal state
  - Criteria: 0 discrepancy over 30 days, roll executed correctly

### Strategy Validation

- [ ] **4. Walk-forward validated ALL strategies**
  - Verification: `output/walk_forward_results.json` — all deployed strategies VALIDATED or BORDERLINE
  - Criteria: OOS/IS > 0.5, >= 50% windows profitable

- [ ] **5. Kill switch tested and calibrated**
  - Verification: Monte Carlo simulation, false positive < 10%
  - Criteria: Each strategy has calibrated threshold

- [ ] **6. Circuit breaker tested**
  - Verification: Simulate -5% daily DD, verify all positions close
  - Criteria: Response time < 30 seconds

- [ ] **7. Bracket orders tested (SL/TP)**
  - Verification: Place test orders on both brokers, verify SL/TP execution
  - Criteria: 100% of bracket orders have both legs

### Risk Management

- [ ] **8. Futures margin monitoring active**
  - Verification: `core/futures_margin.py` health check returns GREEN
  - Criteria: Margin used < 50% at start, alerts configured for 70%

- [ ] **9. Multi-market stress tests pass**
  - Verification: `pytest tests/test_stress_multi_market.py`
  - Criteria: All 4 scenarios DD < 8%

- [ ] **10. Cross-timezone allocation verified**
  - Verification: `python scripts/roc_analysis.py`
  - Criteria: 18h+ coverage, no dead zone > 3h

### Infrastructure

- [ ] **11. Railway worker stable 30+ days**
  - Verification: Uptime logs, 0 crashes in 30 days
  - Criteria: Heartbeat Telegram received every 30min

- [ ] **12. Telegram alerts working**
  - Verification: Test alert sent and received
  - Criteria: Heartbeat + circuit breaker + kill switch alerts all working

- [ ] **13. Reconciliation script verified**
  - Verification: `python scripts/reconciliation.py`
  - Criteria: Alpaca + IBKR positions match internal state

### Operational

- [ ] **14. Per-market alerting verified**
  - Verification: EU, FX, Futures each have separate alert channels
  - Criteria: Alert received from each market

- [ ] **15. Roll manager tested with real contract roll**
  - Verification: At least 1 futures roll completed in paper
  - Criteria: Old position closed, new position opened, slippage logged

- [ ] **16. Disaster recovery plan tested**
  - Verification: Simulate broker disconnect, verify graceful degradation
  - Criteria: Other broker continues, positions preserved, alert sent

- [ ] **17. Capital sizing verified at target level**
  - Verification: Kelly calculator run with live capital ($10K-$25K)
  - Criteria: No position > 15% of capital, margin adequate for futures

---

## Go/No-Go Decision

- ALL 17 items must be checked
- Sign-off date: ___________
- Capital allocated: $________
- Brokers active: [ ] Alpaca [ ] IBKR

### Criteres NO-GO (bloquants)

- Tout item non coche ci-dessus
- Score CRO < 8/10
- Kill switch declenche dans les 7 derniers jours
- Bug non resolu dans le pipeline d'execution
- Reconciliation avec divergence dans les 48h precedentes
- Margin utilise > 60% au demarrage

---

## Post-validation : premiers pas en live

1. Deployer le worker live en PARALLELE du paper (pas de remplacement)
2. Capital initial : $10K-$25K (quart-Kelly sizing)
3. Strategies les plus robustes seulement (Tier S + Tier A sur chaque broker)
4. Monitoring intensif les 48 premieres heures (alerts sur telephone)
5. Review quotidienne du PnL pendant les 2 premieres semaines
6. Pas d'ajout de nouvelles strategies pendant 30 jours
7. Futures : commencer avec 1 contrat MES uniquement (pas ES full-size)
8. FX : commencer avec les positions les plus petites possibles

### Rollback procedure (retour paper)

```bash
# 1. Stopper le worker live
railway stop --service live-worker

# 2. Annuler tous les ordres pendants (Alpaca)
python -c "
from core.alpaca_client.client import AlpacaClient
client = AlpacaClient()
client.cancel_all_orders()
print('Tous les ordres Alpaca annules')
"

# 3. Annuler tous les ordres pendants (IBKR)
python -c "
from core.ibkr_client.client import IBKRClient
client = IBKRClient()
client.cancel_all_orders()
print('Tous les ordres IBKR annules')
"

# 4. Fermer toutes les positions (si necessaire)
# ATTENTION : verifier manuellement avant d'executer
python scripts/paper_portfolio.py --close-all --confirm
python scripts/paper_portfolio_eu.py --close-all --confirm

# 5. Basculer PAPER_TRADING=true sur les deux brokers
# 6. Redemarrer le worker paper
```
