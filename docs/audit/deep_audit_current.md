# Deep Audit — Trading Platform

**Date** : 2026-04-19 (iteration 0)
**Mode** : comite senior impitoyable. Aucune complaisance.
**Auditeurs virtuels** : CTO + CRO + Quant + Produit + Ops.

---

## 1. Comprehension globale (10 lignes)

Plateforme de trading algo multi-broker (Binance crypto + IBKR futures/EU + Alpaca paper) operee en solo par Marc depuis VPS Hetzner. Capital reel EUR 18.6K / $20,854 live snapshot 2026-04-19. **16 strategies canoniques** dans quant_registry, dont **2 live_core** (cross_asset_momentum + gold_oil_rotation sur ibkr_futures), **9 paper READY**, **2 AUTHORIZED sans WF** (gold_trend_mgc V1 recalibration, us_stocks_daily meta-portfolio), **2 DISABLED** (fx_carry ESMA, btc_dominance REJECTED), **15 archived REJECTED**. Post plan 9.0: registries alignes, gouvernance stricte (promotion_gate + pre_order_guard fail-closed), kill switch per-strategy isolate, OSM wire crypto hot path, boot preflight fail-closed, incident JSONL timeline, runtime_audit CLI, dashboard widget computed status. **3667 tests pass 0 fail**.

**Thèse sous-jacente** : desk perso discipline ou la rigueur process compense la petitesse du capital. Live uniquement apres paper + preuve + governance.

**Avantage competitif réel** : qualité code et discipline XXL (atomic persistence, WF Deflated Sharpe + grade, canonical registries), rare chez solo dev.

**Avantage illusoire** : richesse documentaire + multiplicite de strats paper/READY qui peuvent donner l'impression d'un moteur plus large que la reality (2 strats live reel only).

**Point de rupture** : reactiver du live prematurement avant que les nouvelles candidates accumulent 30j paper + WF canonique structure.

---

## 2. Architecture & Tech

**Score CTO : 8.2/10**

| Dimension | Score | Constat |
|---|---|---|
| Modularite | 8.5 | Extractions XXL OK (worker.py 5402 LOC, futures_runner/paper_cycles extraits). Registries canoniques alignes. |
| Tests | 9.0 | **3667 pass 0 fail**, suite collectable clean (A1 plan 9.0). +13 tests E2, +12 test_quant_registry, +12 test_strategy_status, +10 test_boot_preflight. |
| Persistance | 9.5 | DDBaselines atomic tempfile+fsync+replace. OrderTracker atomic save_state sur chaque transition. quant_registry mtime cache invalidation. |
| Gouvernance | 9.0 | promotion_gate wf_source blocking, pre_order_guard fail-closed sur book_health exception, E2 per-strategy disable. |
| Observabilite | 7.5 | Telegram V2 anti-panic + JSONL fallback + syslog + incident JSONL timeline + metrics SQLite + runtime_audit.py. Dashboard widget D3 status. Gap: D3 pas encore deploye VPS, pas de coverage.py CI. |
| SPOFs | 7.0 | Solo dev. VPS unique Hetzner. Telegram unique channel (fallback JSONL OK mais pas multi-channel actif). Gateway IBKR unique. |

### Top 5 risques techniques restants

1. **worker.py 5402 LOC** : encore monolithe. `run_crypto_cycle` ~900 LOC contient toute la logique signal -> order -> OSM -> fidelity. Refacto Phase 2 ChatGPT pas encore fait.
2. **Dashboard D3 pas deploye VPS** : widget build localement (commit 614c20f) mais pas de nginx reload/vite build sur Hetzner. Runtime dashboard continue d'afficher ancien state.
3. **E2 wire partial** : kill switch per-strategy disponible cote API (disable_strategy/is_strategy_disabled) + wire dans pre_order_guard check 6b. Mais wire dans worker.run_live_risk_cycle uniquement pour STRATEGY_LOSS trigger; pas de wire dans run_crypto_cycle pour crypto strategy_id scoped disable (si on veut kill une crypto strat sans toucher portfolio crypto).
4. **C2 wire partial** : OSM wire hot path crypto uniquement. futures_runner.py (1176 LOC) ne passe PAS par OrderTracker encore. Asymetrie entre brokers.
5. **Pas de coverage.py CI** : 3667 tests pass mais coverage % inconnue. Gap potentiel dans modules peu testes.

### Refactos recommandes prioritaires

1. **Deploy dashboard D3 VPS** (30 min) : vite build + copy dist -> nginx
2. **OSM wire futures_runner** (1h) : parite crypto -> futures pour trace end-to-end
3. **coverage.py en CI** (30 min) : measure real coverage, identify gaps
4. **worker.run_crypto_cycle extraction** : -> `core/worker/cycles/crypto_runner.py` (Phase 2)

---

## 3. Audit ligne par ligne (gaps critiques)

| Bloc | Probleme | Impact | Action |
|---|---|---|---|
| [runtime_audit.py](scripts/runtime_audit.py) | 2 incoherences PAPER_WITHOUT_WF: gold_trend_mgc (V1 pending) + us_stocks_daily (meta-portfolio) | Promotion bloquee par design, mais signal "meta" devrait etre distingue de "pending" | Etiquetter us_stocks_daily comme `is_meta_portfolio: true` dans quant_registry (pas compte PAPER_WITHOUT_WF) |
| [worker.py](worker.py) L5402 | Monolithe 5402 lignes (remontee de 5287 apres C2+E2) | Maintenance difficile, couplage hot path | Phase 2 extraction run_crypto_cycle |
| [core/worker/cycles/futures_runner.py](core/worker/cycles/futures_runner.py) | OrderTracker pas wire | Asymetrie end-to-end trace | C2 futures wire |
| [dashboard/api/routes_v2.py](dashboard/api/routes_v2.py) | /api/strategies/status OK mais dist frontend pas build+deploy sur VPS | Widget D3 non visible en prod | vite build + scp dist/ + nginx reload |

---

## 4. Audit strategies (perimetre canonique quant_registry)

### live_core (is_live=true, portfolio actif)

| strat | grade | verdict |
|---|---|---|
| cross_asset_momentum | A | ✅ Exploitable (A-grade, WF 4/5 backfill) |
| gold_oil_rotation | S | ✅ Exploitable (S-grade, WF 5/5 backfill) |

### paper_only READY (wf_artifact present, gaps documented)

| strat | grade | gaps | verdict |
|---|---|---|---|
| mes_pre_holiday_long | B | trades rares 8-10/an | ⚠️ ameliorer (WF 5/5 mais edge trop saisonnier) |
| mcl_overnight_mon_trend10 | B | data stale + friday trigger re-WF | ⚠️ fix data pipeline avant promotion |
| alt_rel_strength_14_60_7 | B | hebdo 4 trades/30j max | ⚠️ besoin tier C pour strats rare-signal |
| btc_asia_mes_leadlag_q70_v80 | B | data BTCUSDT_1h stale + mode both incompatible Binance France | ⚠️ variante long_only_q80_v80 a wirer |
| eu_relmom_40_3 | B | shorts EU pas de plan | ⚠️ solution CFD ou futures mini requis |
| mib_estx50_spread | S | margin EUR 13.5K requise, dispo 9.9K | ⚠️ earliest live 2026-05-02 si capital |
| us_sector_ls_40_5 | B | shorts sectors PDT rule | ⚠️ re-WF ETF data requis |
| mes_monday_long_oc | B | - | ⚠️ WF 3/5 limite |
| mes_wednesday_long_oc | B | MC P(DD>30%)=28% tres limite | ⚠️ sur-fragilite regime, wait more paper |

### AUTHORIZED (no wf_artifact)

| strat | gap |
|---|---|
| gold_trend_mgc | WF V1 recalibration pending (MC validation in flight) |
| us_stocks_daily | meta-portfolio (aggregat de sous-strats), pas WF unique |

### DISABLED

| strat | raison |
|---|---|
| btc_dominance_rotation_v2 | grade=REJECTED, logique historiquement cassee |
| fx_carry_momentum_filter | ESMA EU leverage limits reglementaire |

### archived_rejected (15 strats)

Bucket A (11 binance) + Bucket C (4 ibkr_eu). Tous retires post-drain 19 avril. Ne doivent JAMAIS etre re-promus sans nouveau WF VALIDATED.

---

## 5. Risque systemique

**Correlations** : CAM + GOR sur ibkr_futures sharent MGC/MCL. Stress test August 2024-like = both lose simultanement (memorise). Kill switch daily -5% / 5d -8% proportionnels capital $11K couvre mais close TOUT portfolio IBKR futures.

**Fix E2 inserted** : STRATEGY_LOSS -> scoped disable d'UNE strat sans fermer portfolio. Daily/trailing/monthly = full activate (legitime).

**Effet domino potentiel** : IB Gateway Hetzner down pendant session US -> futures cycles skip (pre_order_guard check data+ibkr health block). Si crash mid-order, OSM.ERROR persiste via atomic save_state = crash recovery possible.

**Queue risk** : solo dev + VPS unique. Marc absent > 48h + trigger critique = no human intervention. Heartbeat watchdog cron 15min detecte mais pas de deputy.

---

## 6. Viabilite business

Cf [reports/research/PROMOTION_CANDIDATES_2026-04-19.md](reports/research/PROMOTION_CANDIDATES_2026-04-19.md).

- Capital $20.8K -> CAGR optimiste net 10.2% = $2.1K/an
- Break-even: ~€615/an frais (VPS + Binance fees)
- Earliest nouvelle strat live: 2026-05-02 (mib_estx50_spread S-grade fast-track 14j, besoin margin EUR 13.5K)

Doctrine acceptee: **prouver rentabilite LIVE avant scaling capital** (memory feedback_prove_profitability_first). Pas d'over-engineering $2M-setup sur $20K.

---

## 7. Failles critiques

### Top 10 risques restants vers 9.5/10

1. Dashboard D3 widget pas deploye VPS (frontend dist stale)
2. Suite de tests pass mais coverage % inconnue
3. worker.py 5402 LOC monolithe
4. OSM wire asymetrique (crypto OK, futures non)
5. 2 strats AUTHORIZED sans wf (gold_trend_mgc pending, us_stocks_daily meta)
6. E2 wire partial (live_risk_cycle OK, crypto cycle pas de scoped disable crypto)
7. No coverage.py in CI
8. worker 5402 LOC -> risque regression
9. Commentaires obsoletes "Railway" / "Phase 1.1" eparpilles
10. Gap restant IBKR gateway health pre-check granular (TCP ping = fine grained manquante)

### Top 5 erreurs conceptuelles evitees

- Ne PAS compter paper comme moteur live
- Ne PAS compter disabled comme active
- Ne PAS compter archived comme "pending re-promotion"
- Ne PAS fermer tout le portfolio sur loss d'une seule strat (E2 fixe)
- Ne PAS accepter wf_source declarative sans fichier physique (A2 fixe)

---

## 8. Plan vers 9.5/10 (iteration 1 suivante)

### Phase 1 urgent (iteration 1 — priorité maximale)

1. **Deploy dashboard D3 VPS** (30 min) : build + scp + nginx reload
2. **Annoter us_stocks_daily + gold_trend_mgc** : champ `meta_portfolio` / `wf_pending` dans quant_registry pour que runtime_audit distingue "meta" vs "incoherence"
3. **coverage.py integration** : lancer 1x, produire report, committer seuil minimum dans pyproject

### Phase 2 stabilisation (iteration 2)

4. **OSM wire futures** : parite crypto pour crash recovery symetrique
5. **E2 wire crypto cycle** : scoped disable lisible dans run_crypto_cycle aussi

### Phase 3 post-9.5 (hors scope court terme)

6. Extraction worker.run_crypto_cycle -> crypto_runner.py
7. Multi-channel alerting (Slack + Email fallback)
8. Coverage CI gate dans pytest addopts

---

## 9. Test de realite

- Le projet survivra-t-il en reel ? **Oui**, pour desk perso avec CAM + GOR + paper accumulating paper data.
- Probabilite succes **en tant que desk perso sain** : 75% (hausse vs 65% ChatGPT grace plan 9.0)
- Probabilite succes **en tant que PnL significatif a $20K** : 25% (inchange — c'est un plafond capital, pas gouvernance)
- Temps avant probleme majeur : **6-12 mois** si gouvernance strict maintenue, **1-3 mois** si relax

---

## 10. Verdict final iteration 0

**Score global : 8.8 / 10**

**Niveau** : 🟢 **Solide** (vs Fragile ChatGPT 6.7)

**Recommandation** : **CONTINUE**

**Gap vers 9.5** : 0.7 pt via Phase 1 urgent (dashboard deploy + us_stocks meta + coverage).

### Justification honnete du score 8.8

- **+3.1 pts vs ChatGPT 6.7** grace a plan 9.0 livre (A1-A5, B1-B4, C1, D1-D2, E1, E3, F1, F2) + C2+E2 stretch
- **-0.7 pt vers 9.5** : gaps Phase 1 listes ci-dessus, aucun bloc structurel
- **NE peut pas claim 9.5** sans :
  - Deploy dashboard D3 VPS (user-visible feature)
  - Distinction meta/pending vs incoherence (runtime_audit propre)
  - Coverage % measurable

### Je ne peux PAS donner 9.5 maintenant car :

1. Dashboard frontend D3 live sur VPS n'est pas verifie (commit local only)
2. runtime_audit affiche 2 incoherences warnings qu'on pourrait squelch legitimement
3. Coverage.py pas execute, % coverage reel inconnu
4. Mandat antitrust explicite: pas de gonflage.

**Next step** : ITERATION 1 batch corrections Phase 1 urgent.
