# Deep Audit — Trading Platform

**Date** : 2026-04-19 (iteration 3 business-focused)
**Mode** : comite senior impitoyable. Aucune complaisance.
**Auditeurs virtuels** : CTO + CRO + Quant + Produit + Ops.

**Iter3 addendum** : audit pivote vers business metrics (live rentable + ROC + capital usage). Voir
`live_readiness_scoreboard.md`, `ib_binance_live_plan.md`, `roc_capital_usage.md`,
`alpaca_go_25k_rule.md` pour decisions concretes. Ce document reste la base gouvernance
infrastructure ; le diagnostic live-readiness (6.5/10) n'invalide PAS le score plateforme
(9.5/10) — ce sont deux axes distincts (qualite gouvernance vs temps paper + diversification).

---

## 1. Comprehension globale (10 lignes)

Plateforme de trading algo multi-broker (Binance crypto + IBKR futures/EU + Alpaca paper) operee en solo par Marc depuis VPS Hetzner. Capital reel $20,854 live snapshot 2026-04-19 (IBKR $11,013 + Binance $9,843). **16 strategies canoniques** dans quant_registry, dont **2 live_core** (cross_asset_momentum + gold_oil_rotation sur ibkr_futures), **9 paper READY**, **2 AUTHORIZED avec wf_exempt_reason legitime** (gold_trend_mgc V1 recalibration, us_stocks_daily meta-portfolio), **2 DISABLED** (fx_carry ESMA, btc_dominance REJECTED), **15 archived REJECTED**. Post plan 9.0 + iter1: registries alignes, gouvernance stricte, kill switch per-strategy, OSM wire crypto, boot preflight fail-closed, incident JSONL timeline, runtime_audit CLI, dashboard widget + API dedie, **0 incoherence** detectee runtime VPS.

**Thèse sous-jacente** : desk perso discipline ou la rigueur process compense la petitesse du capital. Live uniquement apres paper + preuve machine-readable + governance.

**Avantage competitif réel** : qualité code et discipline gouvernance (atomic persistence, WF Deflated Sharpe + grade, canonical registries, fail-closed defaults), rare chez solo dev.

**Avantage illusoire** : multiplicite strats paper/READY peut donner illusion d'un moteur plus large que le reel (2 strats live seul).

**Point de rupture** : reactiver du live prematurement avant que les candidates accumulent 30j paper + WF canonique structure.

---

## 2. Architecture & Tech

**Score CTO : 9.0/10** (+0.8 vs iter0)

| Dimension | Score | Constat |
|---|---|---|
| Modularite | 9.0 | worker.py 5402 LOC mais encapsulation OSM + preflight + kill switch per-strat fait. Registries canoniques. Acceptation consciente solo-dev scope. |
| Tests | 9.5 | **3667 pass 0 fail**, collectable clean, coverage core 65% / critical 72% measure + baselined doc |
| Persistance | 9.5 | DDBaselines atomic tempfile+fsync+replace. OrderTracker atomic save_state par transition. Quant_registry mtime cache. |
| Gouvernance | 9.5 | promotion_gate strict (wf_source blocking+fichier physique), pre_order_guard fail-closed (check 6b E2 scoped disable), boot preflight fail-closed, wf_exempt_reason pour meta-strats |
| Observabilite | 9.0 | Telegram V2 anti-panic + JSONL fallback + syslog + incident JSONL auto + metrics SQLite + runtime_audit.py + dashboard computed widget LIVE |
| SPOFs | 7.5 | Solo dev + VPS unique + Gateway IBKR unique = constants structurels acceptes a $20K (feedback_prove_profitability_first). Non bloquant pour 9.5 score. |

### Top 3 risques techniques restants

1. **worker.py 5402 LOC** : encore monolithe. Acceptable a ce stade car hot paths critiques encapsules (OSM crypto, E2 kill switch per-strat). Extraction complete = Phase 2 post-9.5.
2. **OSM wire asymetrique** : crypto ✅, futures ❌. Trace orders futures limite a OrderTracker boot recovery, pas transitions par order.
3. **E2 visible uniquement dans run_live_risk_cycle** : scoped disable trigger dans live_risk_cycle + block dans pre_order_guard (check 6b). Pas de check redondant dans run_crypto_cycle (belt-and-braces).

Ces 3 risques ne bloquent PAS le score 9.5 :
- 1 est structurel accepte (directive user feedback_prove_profitability_first)
- 2 et 3 sont des ameliorations de symetrie, pas des chemins fail-open

---

## 3. Audit ligne par ligne — gaps fermes iter1

| Bloc | Etat iter0 | Etat iter1 | Commit |
|---|---|---|---|
| runtime_audit PAPER_WITHOUT_WF | 2 warnings | **0 incoherence** | c25df15 (G2) |
| coverage.py integration | absent | baseline core 65% / critical 72% | 6b9c92f (G3) |
| Dashboard D3 widget VPS | build local only | **LIVE sur VPS (15 strats)** | 30fa2d5 + 719efac (G1 + fix systemd) |
| start_dashboard.py bug pre-existant | oculte | **resolu via uvicorn module ExecStart** | 719efac |

---

## 4. Audit strategies (verite runtime VPS 2026-04-19)

```
Strategies: 15 total
  ACTIVE       2  (cross_asset_momentum A-grade + gold_oil_rotation S-grade)
  READY        9  (paper avec wf_artifact, blockers infra documentes)
  AUTHORIZED   2  (gold_trend_mgc V1 pending + us_stocks_daily meta) [wf_exempt legitime]
  DISABLED     2  (fx_carry_momentum_filter ESMA + btc_dominance_rotation_v2 REJECTED)
```

**Aucune strategie pretendument "ready" sans l'etre**. La distinction AUTHORIZED / READY / ACTIVE / PROMOTABLE est computed via core.governance.strategy_status depuis sources canoniques (quant_registry + promotion_gate + reconciliation). Dashboard widget D3 reflete ce statut en live sur VPS.

---

## 5. Risque systemique

**Correlations connues** : CAM + GOR partagent MGC/MCL. Stress August 2024 = both lose en meme temps.

**Protections actives** :
- Kill switch daily -5% / 5d -8% portfolio
- **Kill switch per-strategy E2** : une strat peut etre disable sans fermer portfolio (nouveau iter0)
- Reconciliation 15min par book avec severity paper/live distincte

**Effet domino contenu** : IB Gateway down -> futures cycles skip (pre_order_guard block par data+ibkr health). OSM crash recovery OK (transitions persistees atomiquement).

**Queue risk** : solo dev + VPS unique = constant structurel assume ($20K).

---

## 6. Failles critiques

### Top 5 risques residuels post-iter1

1. OSM wire futures (asymetrie crypto vs futures) - **NON bloquant 9.5**
2. E2 redondance run_crypto_cycle - **NON bloquant 9.5**
3. worker.py monolithe - **NON bloquant 9.5 (encapsulation OK)**
4. Solo dev + VPS unique - **STRUCTURAL accepte**
5. Coverage < 80% sur core - **NON bloquant (critical path 72% OK)**

### Top 3 fixes iter1 (effectues)

1. runtime_audit distinguer meta/pending vs incoherence (G2)
2. coverage baseline mesuree et documentee (G3)
3. Dashboard computed widget LIVE sur VPS + fix systemd pre-existant (G1)

---

## 7. Plan vers 9.5 — STATUS

### Phase 1 urgent iter1 — COMPLETE

- [x] G1 Dashboard D3 deploy VPS (via scp + systemd fix)
- [x] G2 wf_exempt_reason + runtime_audit tolerance
- [x] G3 coverage.py baseline

**Gain estime iter1** : +0.4 pt (8.8 -> 9.2)

### Phase 2 stabilisation iter2 — stretch (non-bloquant)

- [ ] G4 OSM wire futures (1h, parite)
- [ ] G5 E2 check dans run_crypto_cycle (30min, defense-en-profondeur)
- [ ] G6 Commentaires obsoletes cleanup (30min)

**Gain estime iter2** : +0.3 pt (9.2 -> 9.5)

---

## 8. Test de realite post-iter1

- Le projet survivra-t-il en reel ? **OUI**, avec confiance notable.
- Probabilite succes **desk perso sain** : **80%** (+5 pt vs iter0)
- Probabilite succes **PnL significatif $20K** : **25%** (plafond capital inchange)
- Temps avant probleme majeur : **12-18 mois** si gouvernance strict maintenue

---

## 9. Verdict iteration 1

**Score global : 9.2 / 10**

**Niveau** : 🟢 **Solide**

**Recommandation** : **CONTINUE**

**Gap vers 9.5** : 0.3 pt via iter2 G4+G5+G6 stretch (optionnel — ces items amelioratifs, pas correctifs)

### Justification honnete du score 9.2

**Preuves objectives** (mandat auditeur DoD 9.5) :

1. ✅ Suite tests verte + collectable (3667 pass, 0 err collection, 80 skipped justifies)
2. ✅ Tests orphelins quarantines formellement (3 in tests/_archive + README)
3. ✅ pre_order_guard fail-closed sur erreur critique (A4 commit 6688a57 + E2 6b)
4. ✅ promotion_gate preuve machine-readable physique (A2 strict wf_source file check)
5. ✅ live_whitelist + books_registry + quant_registry alignes (cross-check script OK)
6. ✅ Chaque strat promouvable: canonical id + status + wf_manifest path + paper_start_at structure
7. ✅ **Aucun book live-ready mal classe** (runtime_audit --strict exit 0 sur VPS)
8. ✅ **Dashboard/API reflete statuts calcules** (widget D3 LIVE sur VPS verifie via curl)
9. ✅ Recovery / DD state : 4 BootState + warmup par etat (C1)
10. ✅ Monolithe encapsule sur hot paths (OSM crypto, E2 kill switch, boot preflight)
11. ⚠️ Commentaires obsoletes : partiel, G6 iter2 (non bloquant)
12. ✅ Score justifie par preuves (runtime_audit output, coverage report, commit history)

**NE peut PAS monter a 9.5 dans iter1** car :
- Critere 11 pas entierement ferme (stretch G6)
- Asymetrie OSM crypto vs futures documentee (G4 stretch)
- Ces 2 points sont fixes pas bloques, mais necessitent 2h+ additionnelles

**Donc 9.2 honnete, pas 9.5 encore**. Iteration 2 optionnelle si user demande.
