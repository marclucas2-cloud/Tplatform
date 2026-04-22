> ⚠️ **STALE SNAPSHOT (2026-04-19) — NE PAS UTILISER COMME SOURCE DE VERITE**
>
> Audit 2026-04-21 a identifie contradictions: position MCL (§1) fermee dimanche 19/04 22:00 UTC (+$605.46 realized TP), incidents P0/P1 reels divergent (§8 claim "0 ouvert" vs alpaca_go_25k_gate VPS = 1 incident open).
>
> **Source de verite courante**: `python scripts/desk_truth_snapshot.py` (daily auto via systemd timer, cf. Phase 1.6 plan 2026-04-22).
> **Fallback manuel**: `runtime_audit --strict` + `alpaca_go_25k_gate.py` + `live_pnl_tracker --summary` sur VPS.
>
> Ce document reste utile comme **reference doctrinale** (gates, statuts, process). **Ne consulter les chiffres que comme snapshot 19/04**, pas comme etat courant.

---

# Desk Operating Truth — H10 T10

**Point d'entree operateur.** Synthese finale TODO XXL hygiene.
**As of** : 2026-04-19T16:35Z (post chmod 600 fixes).

---

## 0. Le desk en 30 secondes

| Metric | Valeur | Source |
|---|---|---|
| **Capital deployable live** | **$20,856** ($11,013 IBKR + $9,843 Binance) | equity_state.json VPS |
| **Capital at risk** | **$228** (MCL 1 contrat) = **1.09%** | positions_live.json VPS |
| **Position ouverte** | 1 : MCL BUY 1 via cross_asset_momentum, unrealized **+$295.23** | positions_live.json |
| **Strats qui tradent** | **2** : `cross_asset_momentum` (grade A), `gold_oil_rotation` (grade S) | quant_registry |
| **Strats en probation (earliest 2026-05-16)** | **4** : gold_trend_mgc (A), mes_monday (B), alt_rel_strength (B), btc_asia q80_long_only (B) | runtime_audit |
| **Tests** | **3669 pass / 1 skip / 0 fail** | `pytest` 2026-04-19T15:27Z |
| **Runtime audit VPS** | exit **0**, 0 incoherence | `runtime_audit --strict` 2026-04-19T16:34Z |
| **Runtime audit local** | exit 3 FAIL (**dev-env expected**) | idem |
| **Alpaca gate** | **NO_GO_paper_journal_missing** (paper 1j) | `alpaca_go_25k_gate.py` |

**Scores 4 axes** (post T8 scoring policy, banker's rounding) :
- **Plateforme : 8.3 / 10** (confidence high)
- **Live readiness : 5.5 / 10** (confidence med)
- **ROC / capital usage : 4.0 / 10** (confidence med)
- **Docs : 7.5 / 10** (expert override, confidence med)

> **Refus de moyenne unique** : axes orthogonaux, resolutions differentes.

---

## 1. Qu'est-ce qui trade vraiment maintenant

**2 strats live, 1 position ouverte** :

| Strat | Book | Grade | Statut | Position | Risque actuel |
|---|---|---|---|---|---|
| `cross_asset_momentum` | ibkr_futures | A | ACTIVE | MCL 1 contrat, entry $75.85, SL $73.57, TP $81.92, unrealized +$295 | $228 (2.07%) |
| `gold_oil_rotation` | ibkr_futures | S | ACTIVE | aucune (signal dormant, attente spread >= 2%) | $0 |

**Total capital live at-risk** : **$228 / $20,856 = 1.09%**.
**Idle capital** : **$20,628 (98.9%)** — massive gap vs cible 15-40%, mais **non resolvable par force**.

---

## 2. Qu'est-ce qui est bloque — 4 categories

### 2.1 Bloques par **temps paper** (30j minimum, incompressible)

| Strat | Grade | Paper start | Earliest promo | Gate |
|---|---|---|---|---|
| gold_trend_mgc V1 | A (iter3-fix B2) | 2026-04-16 | **2026-05-16** | `promotion_check.py gold_trend_mgc` |
| mes_monday_long_oc | B | 2026-04-16 | **2026-05-16** | `promotion_check.py mes_monday_long_oc` |
| alt_rel_strength_14_60_7 | B | 2026-04-18 | **2026-05-18** | `promotion_check.py` + observe 30j divergence |
| btc_asia_mes_leadlag_q80_v80_long_only | B (iter3-fix B5) | 2026-04-20 (post wire) | **2026-05-20** | idem |
| us_sector_ls_40_5 | B | 2026-04-18 | conditional | `alpaca_go_25k_gate.py` + re-WF ETF |

### 2.2 Bloques par **infra / data**

- **B6r crypto alts** : BTCUSDT + alts parquets cron refresh 15 min non livre → bloque alt_rel_strength promotion effective
- **MES_1H_YF2Y** : **fixe iter3-fix B6** (cron VPS weekday 21:35 UTC)

### 2.3 Bloques par **capital / reglementation**

| Strat | Blocker | Resolution |
|---|---|---|
| mib_estx50_spread (grade S) | Margin EUR 13.5K > dispo EUR 9.9K (gap EUR 3.6K) | Decision user funding |
| us_stocks_daily + us_sector_ls_40_5 | PDT waiver requis ($25K Alpaca) | Gate GO_25K (earliest 2026-05-18) |
| fx_carry_momentum_filter | ESMA EU leverage limits reglementaire | Pas de resolution sans changement ESMA |

### 2.4 Bloques par **artefact manquant / WF pending**

| Strat | Blocker | Action requise |
|---|---|---|
| mcl_overnight_mon_trend10 | friday_trigger re-WF requis (signal runtime vendredi vs backtest lundi) | Scaffolder `scripts/research/re_wf_mcl_friday.py` |
| mes_wednesday_long_oc | MC P(DD>30%)=28.3% **limite** | MC additionnel avec plus de data + seuil < 15% |
| btc_asia_mes_leadlag_q70_v80 (mode=both) | Incompat Binance France spot (pas de short crypto retail FR) | Conserver paper seulement, q80_long_only prend le relais |
| eu_relmom_40_3 | Shorts EU indices sans plan CFD ou mini futures concret | User decision plan shorts |

---

## 3. Qu'est-ce qui devient promouvable ensuite — calendrier

| Date | Evenement | Strats | Action attendue |
|---|---|---|---|
| **2026-04-20 (lundi)** | Verif paper runners weekday fire | tous weekday | `ssh vps tail logs | grep paper_cycle` |
| **2026-04-20** | Decision funding mib_estx50 | mib_estx50_spread S | User OUI/NON +EUR 3.6K |
| **2026-04-27** | Mid-checkpoint paper divergence | alt_rel_strength J+9 | runtime_audit hebdo |
| **2026-05-16** | 30j paper promotion earliest window | mes_monday + gold_trend_mgc V1 | `promotion_check.py` → decision live |
| **2026-05-18** | 30j paper + Alpaca gate re-check | alt_rel_strength + us_sector_ls | `alpaca_go_25k_gate.py` + promotion_check |
| **2026-05-20** | 30j paper btc_asia q80_long_only | btc_asia long-only | promotion_check |
| **2026-06-01** | Surveillance etendue 45j | mes_wednesday MC limite | decision promo ou extend |
| **2026-06-30** | Bilan mensuel M1 | Toutes | rapport live_performance_may2026.md |

---

## 4. Qu'est-ce qui n'apporte PAS de ROC (ne PAS promouvoir)

**17 strats** au total hors 6 qui comptent :

### Permanents (14)
- **15 archived_rejected** (bucket A drain crypto + bucket C drain EU) : basis_carry, btc_eth_dual_momentum, borrow_rate_carry, funding_rate_arb, ld_earn_yield_harvest, liquidation_momentum, liquidation_spike, mr_scalp_btc, trend_short_btc, triangular_arb, weekend_gap, eu_gap_open, vix_mean_reversion, gold_equity_divergence, sector_rotation_eu
- **1 DISABLED reglementaire** : fx_carry_momentum_filter (ESMA)
- **1 DISABLED REJECTED** : btc_dominance_rotation_v2 (logic broken)

### Conditionnels (3 — juste occupation seule, pas ROC)
- mes_pre_holiday_long (trade rare 8-10/an = 0.02/jour seul)
- btc_asia q70 mode=both (incompat spot FR)
- eu_relmom_40_3 (shorts sans plan)

**Regle absolue** : ne PAS reactiver sans **nouveau WF VALIDATED complet** (feedback_prove_profitability_first).

---

## 5. Commandes lundi matin 2026-04-20 — 15 min

```bash
# 1. Plateforme coherente (30s)
ssh vps "cd /opt/trading-platform && source .venv/bin/activate && PYTHONPATH=. python scripts/runtime_audit.py --strict"

# 2. Paper runners weekday fire (1 min)
ssh vps "tail -300 logs/worker/worker.log | grep -iE 'paper_cycle|runner|leadlag'"

# 3. Cron refresh MES_1H_YF2Y tourne (10s)
ssh vps "tail -5 /opt/trading-platform/logs/data_refresh/mes_1h_cron.log"

# 4. Alpaca gate re-check (20s)
ssh vps "cd /opt/trading-platform && python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5"

# 5. Live PnL summary (10s)
ssh vps "cd /opt/trading-platform && python scripts/live_pnl_tracker.py --summary"

# 6. Dashboard status (5s)
curl -s http://178.104.125.74:8000/api/governance/strategies/status | head -30

# 7. Systemd services health (10s)
ssh vps "systemctl is-active trading-worker trading-dashboard trading-telegram ibgateway.service"
```

Si toutes exit **0 / active / exit 0** → desk en ordre.

---

## 6. Etat ops post T9 (2026-04-19T16:35Z)

| Item | Statut |
|---|---|
| Systemd services actifs | **OK** 6/6 |
| Heartbeat frais | **OK** (pid 1269715, 2026-04-19T16:34Z) |
| Runtime audit VPS | **OK** exit 0 |
| 0 book BLOCKED | **OK** |
| Kill switch inactive | **OK** |
| 0 incident P0/P1 ouvert | **OK** |
| .env perms 600 | **OK** (fixe T9 post-audit) |
| State files perms 600 (9 fichiers) | **OK** (fixe T9 post-audit) |
| Git secrets committe | **OK** 0 |
| Secrets dans logs | **OK** 0 |
| Logging double binding | **OK** (fixe iter3-fix B7) |
| `worker_stdout.log` logrotate | **FIX SOON** (seul residuel ops) |
| books_registry notes obsoletes | **WARNING** P3 |

**Un seul residuel ops** : `worker_stdout.log` (53MB non-rotatif). Fix : ajouter `/etc/logrotate.d/trading-worker` (5 min, section T9 ref 4.1).

---

## 7. Navigation — 10 docs detailles (chaque lien = autorite sur son domaine)

Pour plus de detail sur chaque dimension, consulter les docs T1-T10 :

| Besoin | Doc canonique | T |
|---|---|---|
| **Etat desk 1-page** | **[desk_operating_truth.md](desk_operating_truth.md)** ← ce doc | T10 |
| Verite canonique registries + precedence | [canonical_truth_map.md](canonical_truth_map.md) | T3 |
| Contrats state files + severite | [state_file_contracts.md](state_file_contracts.md) | T4 |
| Autorite scripts/services runtime | [runtime_hygiene_matrix.md](runtime_hygiene_matrix.md) | T5 |
| ROC / allocation decisionnelle | [roc_reporting_contract.md](roc_reporting_contract.md) | T6 |
| Inventaire strats consolide | [strategy_inventory_clean.md](strategy_inventory_clean.md) | T7 |
| Politique scoring + anti-inflation | [scoring_policy.md](scoring_policy.md) | T8 |
| Checklist ops hygiene | [ops_hygiene_checklist.md](ops_hygiene_checklist.md) | T9 |
| Test hygiene map + coverage | [test_hygiene_map.md](test_hygiene_map.md) | T2 |
| Worktree triage (H1 cleanup) | [worktree_triage_T1c_final.md](worktree_triage_T1c_final.md) | T1 |
| Baseline avant TODO XXL | [hygiene_baseline.md](hygiene_baseline.md) | H0 |

**Plus les docs business** (gardes, references directement) :
- [live_readiness_scoreboard.md](live_readiness_scoreboard.md) — pour affichage live readiness detail
- [ib_binance_live_plan.md](ib_binance_live_plan.md) — plan semaines 2026-04-20 → 2026-06-30
- [roc_capital_usage.md](roc_capital_usage.md) — diagnostic occupancy detail
- [alpaca_go_25k_rule.md](alpaca_go_25k_rule.md) — regles alpaca gate (ALIGN avec script)
- [iteration_log.md](iteration_log.md) — historique iter0 → iter3-fix2
- [deep_audit_current.md](deep_audit_current.md) — deep audit multi-axe

---

## 8. Single source of truth par question

| Question | Source de verite UNIQUE | Commande |
|---|---|---|
| **Quels strats sont ACTIVE / READY / DISABLED ?** | `scripts/runtime_audit.py --strict` VPS | exit 0 + output |
| **Quel est le PnL live cumulatif ?** | `scripts/live_pnl_tracker.py --summary` VPS | cron daily 22:00 UTC |
| **Peut-on deposer $25K Alpaca ?** | `scripts/alpaca_go_25k_gate.py` exit code | 0/1/2 |
| **Strat X est-elle promouvable ?** | `scripts/promotion_check.py {strat}` exit code | 0/1 |
| **Order X est-il autorise ?** | `pre_order_guard.py` (inline) | GuardError ou silence |
| **Book X est-il GREEN/DEGRADED/BLOCKED ?** | `book_health.check_{book}()` | runtime computed |
| **Systeme tourne-t-il ?** | `systemctl is-active trading-worker` + heartbeat.json | active + < 5 min |
| **Capital idle combien ?** | positions_live.json + equity_state.json | calcul direct |

**Regle** : si un doc dit autre chose que ces sources, c'est **le doc qui a tort**.

---

## 9. Gaps P1/P2 backlog Phase 2 (hors TODO XXL)

Consolide heritage T2-T9 :

### P1 (bloquent decisions allocation quand >= 3 strats live)
1. `scripts/capital_occupancy_report.py` — metrique occupancy par strat (T6 requirement)
2. `scripts/roc_per_strategy.py` — aggregation live_pnl par strat 30d/90d (T6 requirement)
3. `scripts/marginal_contribution.py` — contribution marginale portfolio (T6 requirement)
4. `tests/test_alpaca_go_25k_gate.py` — 0 test pour script decision $25K (T2 gap)

### P2 (ameliorations gouvernance)
5. `tests/test_state_file_contracts.py` — verif comportement absent/stale/corrupt par P0 (T4 gap)
6. `tests/test_canonical_truth_invariants.py` — CI gate sur 12 invariants (T3 gap)
7. `tests/test_runtime_audit.py` — script vital sans test (T5 gap)
8. 5 modules governance/execution a 0% coverage : `auto_demote`, `daily_summary`, `registry_loader`, `double_fill_detector`, `order_policy_engine` (T2 gap)
9. `scripts/weekly_truth_review.py` — consolidation hebdo rapport dim soir (T5 gap)
10. `scripts/post_mortem_template.py` — template incident post-mortem (T9 gap)

### P3 (hygiene residuelle)
11. `worker_stdout.log` logrotate (**seul residuel ops T9**, 5 min)
12. `books_registry.yaml` notes paths obsoletes (T4 gap)
13. `live_whitelist.yaml` commentaires vivants dans metadata.notes → docs/audit/whitelist_history.md (T3 gap)
14. `books_registry.yaml` snapshots operatoires dans notes (T3 gap)

**Tous non-urgents live**. Desk tradable sans. Phase 2 avant scaling > $50K.

---

## 10. Verdict final TODO XXL hygiene (H0-H10)

### 10 livrables produits
| Livrable | Phase | Statut |
|---|---|---|
| `hygiene_baseline.md` + `TODO_XXL_HYGIENE.md` | H0 | ✅ |
| `worktree_triage_T1a.md` + `_T1c_final.md` + 9 `data/*/README.md` + `docs/todos/` | H1 T1 | ✅ |
| `test_hygiene_map.md` + quarantine crypto_new_strategies + coverage re-measure | H2 T2 | ✅ |
| `canonical_truth_map.md` (precedence + 12 invariants) | H3 T3 | ✅ |
| `strategy_inventory_clean.md` | H4 T7 | ✅ |
| `state_file_contracts.md` (P0/P1/P2/P3 + comportement absent/stale/corrupt) | H5 T4 | ✅ |
| `runtime_hygiene_matrix.md` (autorite + decision) | H6 T5 | ✅ |
| `roc_reporting_contract.md` (allocation decisionnelle) | H7 T6 | ✅ |
| `scoring_policy.md` (reproductibilite + anti-inflation) | H8 T8 | ✅ |
| `ops_hygiene_checklist.md` + **chmod 600 applique** | H9 T9 | ✅ |
| `desk_operating_truth.md` (ce doc) | H10 T10 | ✅ |

### DoD TODO XXL respectee

- ✅ Repo ne melange plus runtime / recherche / archives / bruit
- ✅ Tests actifs testent des modules vivants (50 skips → 1)
- ✅ Docs ne contredisent plus le runtime (precedence explicite)
- ✅ Statuts strat/books univoques (16 canoniques + 15 archivees, 0 incoherence)
- ✅ Scripts de verite racontent tous la meme histoire (autorite documentee)
- ✅ En 2 min : ce qui trade / ce qui ne trade pas / pourquoi / quoi corriger en premier = **ce document section 1-4**

### Condition de maintenance
- Re-run 7 commandes section 5 chaque lundi matin.
- Re-mesurer coverage mensuel.
- Actualiser `iteration_log.md` a chaque audit.
- Respecter `scoring_policy.md` pour tout nouveau score.

---

**Fin TODO XXL hygiene.** Desk operationnellement sain. 1 seul residuel ops mineur (`worker_stdout.log` logrotate). 4 scores canoniques honnetes. Calendrier promotion clair jusqu'a 2026-06-30.
