# CHECKLIST PRE-LIVE

> Date de creation : 2026-03-27
> A valider avant TOUT passage paper -> live

---

## Conditions obligatoires (toutes doivent etre cochees)

### Performance (60 jours paper minimum)

- [ ] 60 jours paper trading positif (PnL net > 0)
- [ ] Walk-forward valide sur chaque strategie active (>= 60% fenetres OOS profitables)
- [ ] Reconciliation 0 divergence sur 14 jours consecutifs
- [ ] Stress tests passes (4 scenarios — voir docs/chaos_testing_report.md)
- [ ] Sharpe 60j paper > 1.0
- [ ] Max drawdown < 5% sur toute la periode paper
- [ ] Win rate > 45% sur l'ensemble des strategies
- [ ] >= 200 trades executes en paper

### Risk Management

- [ ] Kill switch calibre (Monte Carlo, voir output/kill_switch_calibration.json)
- [ ] Kelly sizing calcule (quart-Kelly pour Live L1)
- [ ] Circuit-breaker teste et fonctionnel (au moins 1 declenchement simule)
- [ ] Bracket orders (SL/TP) verifies cote broker sur 100% des trades
- [ ] Fermeture forcee 15:55 ET testee sur 20+ sessions
- [ ] Exposition nette jamais > 40% long / 20% short sur la periode
- [ ] VaR 95% daily jamais > 2% du portefeuille
- [ ] Aucune strategie avec kill switch declenche dans les 30 derniers jours

### Infrastructure

- [ ] Backup fonctionnel (voir docs/disaster_recovery.md)
- [ ] CI/CD fonctionnel (tests passent avant chaque deploy)
- [ ] Alerting externe fonctionnel (UptimeRobot + Telegram)
- [ ] Worker Railway uptime > 99.5% sur 60 jours
- [ ] Healthcheck endpoint actif et monitore
- [ ] Logs structures disponibles pour audit post-mortem
- [ ] Alpha decay monitor en place et sans alerte critique
- [ ] Reconciliation automatisee (cron horaire)

### Operationnel

- [ ] Plan de sizing progressif documente (voir docs/scaling_plan_v2.md)
- [ ] Audit CRO complet score >= 8/10
- [ ] Documentation a jour (strategies, allocation, risk limits)
- [ ] Plan de rollback documente (retour paper en < 5 min)
- [ ] Compte Alpaca live configure (PAPER_TRADING=false pret, pas active)
- [ ] Tax report framework teste sur les trades paper

### Legal / Compliance

- [ ] Regime fiscal du trading defini (PFU vs bareme)
- [ ] Obligation declarative comprise (formulaire 2086 pour plus-values)
- [ ] Seuils de declaration IFI verifies

---

## Validation finale

| Validateur | Date | Resultat |
|-----------|------|---------|
| Marc (dev) | | [ ] GO / [ ] NO-GO |
| CRO (audit) | | [ ] GO / [ ] NO-GO |
| Review pair (optionnel) | | [ ] GO / [ ] NO-GO |

### Criteres NO-GO (bloquants)

- Tout item non coche ci-dessus
- Score CRO < 8/10
- Kill switch declenche dans les 7 derniers jours
- Bug non resolu dans le pipeline d'execution
- Reconciliation avec divergence dans les 48h precedentes

---

## Post-validation : premiers pas en live

1. Deployer le worker live en PARALLELE du paper (pas de remplacement)
2. Capital initial : $25K (quart-Kelly sizing)
3. 7 strategies les plus robustes seulement (Tier S + Tier A)
4. Monitoring intensif les 48 premieres heures (alerts sur telephone)
5. Review quotidienne du PnL pendant les 2 premieres semaines
6. Pas d'ajout de nouvelles strategies pendant 30 jours

### Rollback procedure (retour paper)

```bash
# 1. Stopper le worker live
railway stop --service live-worker

# 2. Annuler tous les ordres pendants
python -c "
from core.alpaca_client.client import AlpacaClient
client = AlpacaClient()
client.cancel_all_orders()
print('Tous les ordres annules')
"

# 3. Fermer toutes les positions (si necessaire)
# ATTENTION : verifier manuellement avant d'executer
python scripts/paper_portfolio.py --close-all --confirm

# 4. Basculer PAPER_TRADING=true
# 5. Redemarrer le worker paper
```
