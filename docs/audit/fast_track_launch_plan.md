# Fast-Track Launch Plan

**As of** : 2026-04-19T17:30Z
**Doctrine** : [fast_track_promotion_policy.md](fast_track_promotion_policy.md)
**Review** : [fast_track_candidates_review.md](fast_track_candidates_review.md) (verdict 1 seule strat `FAST_TRACK_NOW`)
**Portée** : actions concrètes 2026-04-20 → 2026-05-18 pour armer et piloter le 1er fast-track du desk.

---

## 0. Résumé exécutif — 30 secondes

**On fast-track UNE SEULE strat : `gold_trend_mgc V1` sur `ibkr_futures`.**

- **Pas 2.** La reco user "gold_trend_mgc + mes_monday" est refusée sur mes_monday (grade B + fréquence 2 trades en 14j + slot book déjà pris par gold_trend_mgc). Cf. [fast_track_candidates_review.md](fast_track_candidates_review.md) §2.
- **Pas binance_crypto.** Aucune candidate crypto ne passe la doctrine §3 actuellement (toutes grade B + infra_gaps + fréquence insuffisante). Le capital Binance reste idle volontairement (cf. [roc_reporting_contract.md](roc_reporting_contract.md) §5.2).
- **Fenêtre** : arming gate 2026-04-30 (J+14 paper), trade first 2026-04-30 ou lundi suivant 2026-05-04 si arming décale, exit J+14 fast-track = 2026-05-14. Décision upward ou rollback à cette date.

**Gain** : ~16 jours de live réel gagnés vs parcours standard (2026-05-16 → 2026-04-30), avec sizing 1 contrat MGC + signal seuil ≥ 1.25σ.
**Coût** : review 2×/semaine pendant 14j + 1 slot ibkr_futures occupé + ~$100-130 risk-if-stopped par trade.

---

## 1. Strat armée : `gold_trend_mgc V1`

| Dimension | Valeur |
|---|---|
| **Strat ID** | `gold_trend_mgc` |
| **Book** | `ibkr_futures` |
| **Grade** | A (iter3-fix B2) |
| **WF manifest** | `data/research/wf_manifests/gold_trend_mgc_v1_2026-04-19.json` |
| **Instrument** | MGC (Micro Gold, 10 oz, CME) |
| **Paper start** | 2026-04-16 |
| **Earliest fast-track arming** | **2026-04-30** (J+14) |
| **Target fast-track exit** | 2026-05-14 (J+14 fast-track) |
| **Sizing** | 1 contrat MGC (min broker), avec filtre signal `≥ 1.25σ` |
| **Risk-if-stopped estimé** | $100-130 / trade (SL défini par strat) |
| **probation_mode** | `fast_track` (nouveau champ `live_whitelist.yaml`) |
| **fast_track_start_at** | sera `2026-04-30` dans `quant_registry.yaml` |

---

## 2. Pré-requis bloquants (à livrer avant 2026-04-30)

### 2.1 Code/config minimum (P0 doctrine §10)

| Item | Type | Responsable | Deadline |
|---|---|---|---|
| Ajouter champ `probation_mode` au schéma `live_whitelist.yaml` (+ validation) | code | Marc | 2026-04-28 |
| Ajouter champ `fast_track_start_at` au schéma `quant_registry.yaml` | code | Marc | 2026-04-28 |
| Extension `scripts/promotion_check.py --mode fast_track --min-paper-days 14` | code | Marc | 2026-04-28 |
| `pre_order_guard.check_6` lit `sizing_cap_fast_track` quand `probation_mode=fast_track` | code | Marc | 2026-04-28 |
| Extension `kill_switch_live` pour kill_criteria fast_track strict | code | Marc | 2026-04-29 |

### 2.2 Dégradation acceptable si code non livré

Si les 5 items P0 ne sont pas livrés au 2026-04-30 : **arming gate manuel strict** (17 conditions §3 vérifiées à la main en ligne de commande) + monitoring renforcé journalier. Pas d'impact safety, juste charge supervision accrue. Pas de rollback automatique kill_switch fast_track → rollback manuel obligatoire sur signal.

**Recommandation forte** : livrer au moins les items 1, 2, 3 (schéma + gate) avant arming. Les items 4 et 5 peuvent être P1 et livrés dans les 7 premiers jours fast-track sans bloquer l'arming.

### 2.3 Corrélation GOR / gold_trend_mgc

- **Observation** : GOR (gold_oil_rotation) est ACTIVE avec signal dormant (attend spread ≥ 2%). Quand GOR signal s'active, il prendra position gold (leg long MGC ou équivalent). Simultanéité avec gold_trend_mgc = **double exposition gold**.
- **Mitigation** : ajouter règle `pre_order_guard.check_7` (**nouveau check**, optionnel P1) : "si position GOR ouverte ET ordre gold_trend_mgc incoming → skip ou downsize". Alternative moins invasive : documenter dans `notes` live_whitelist et laisser les 2 strats coexister (risque gold accepté car sizing MGC faible).
- **Décision par défaut** : laisser coexister, sizing MGC faible = risque absolu plafonné. Ne pas bloquer fast-track sur cette règle.

---

## 3. Date d'arming — timeline

### 3.1 Séquence

```
2026-04-16 ────────┬──── paper_start_at gold_trend_mgc
                   │
                   │ (paper 14j)
                   │
2026-04-30 (jeudi) ┴──── J+14 paper — gate §3 arming
2026-04-30 fin jour ──── décision arming OUI/NON
                   │
                   │ arming SI toutes conditions §3 OK
                   │
2026-05-01 (ven) ──────── premier trade potentiel (signal >= 1.25σ)
                   │
                   │ (fast_track 14j)
                   │
2026-05-14 (mer) ────── J+14 fast-track — gate §7.1 exit
                   │
                   │ décision upward → live_probation standard
                   │ OU rollback → paper_only
                   │
2026-05-16 ───────────── si rollback fast-track, earliest gate standard 30j comparable
                   │
                   ▼
```

### 3.2 Check-list arming 2026-04-30 (matin, ~15 min)

Toutes doivent être vertes. **Si une seule échoue, on repousse le fast-track à 2026-05-16 (gate standard).**

```bash
# 1. Runtime audit (30s)
ssh vps "cd /opt/trading-platform && source .venv/bin/activate && \
  PYTHONPATH=. python scripts/runtime_audit.py --strict"
# Attendu : exit 0, "No incoherences detected"

# 2. Gate §3 fast-track (30s — nécessite livraison P0 §2.1 item 3)
ssh vps "cd /opt/trading-platform && \
  python scripts/promotion_check.py gold_trend_mgc --mode fast_track --min-paper-days 14"
# Attendu : exit 0, grade A, manifest OK, 14j paper, divergence OK

# 3. Paper PnL net positif (30s)
ssh vps "python scripts/live_pnl_tracker.py --strategy gold_trend_mgc --mode paper --window 14d"
# Attendu : pnl_net >= 0

# 4. Divergence paper/backtest (1 min — manuel dashboard)
curl -s "http://178.104.125.74:8000/api/strategies/gold_trend_mgc/divergence?window=14d"
# Attendu : |divergence| <= 1σ

# 5. Nombre trades observés (30s)
ssh vps "grep gold_trend_mgc logs/worker/worker.log | grep 'paper_trade' | wc -l"
# Attendu : >= 4 (sinon signal que fréquence insuffisante → no-go)

# 6. Book health (10s)
curl -s "http://178.104.125.74:8000/api/governance/books/ibkr_futures/health"
# Attendu : {"status":"GREEN"}

# 7. Incidents 7d (15s)
grep -c "severity:P0\|severity:P1" /opt/trading-platform/data/incidents/*.jsonl
# Attendu : 0 incidents P0/P1 récents sur ibkr_futures

# 8. Kill switch inactif (10s)
ssh vps "cat /opt/trading-platform/data/state/kill_switch_state.json | jq .is_active"
# Attendu : false

# 9. Slots fast-track disponibles (manuel)
grep probation_mode: config/live_whitelist.yaml
# Attendu : 0 slot occupé fast-track (premier du desk)

# 10. Systemd services (10s)
ssh vps "systemctl is-active trading-worker trading-dashboard trading-telegram ibgateway.service"
# Attendu : active × 4

# 11. Corrélation GOR actuelle (30s)
ssh vps "cat data/state/ibkr_futures/positions_live.json | jq '.positions[] | select(.strategy_id==\"gold_oil_rotation\")'"
# Attendu : vide (pas de position GOR) OU position GOR déjà décision prise en amont
```

Si 10/11 OK → **arming GO**. Si 1 rouge → **arming NO-GO**, défer à gate standard.

### 3.3 Écritures de configuration

Si arming GO :

```yaml
# config/live_whitelist.yaml (extrait, sous books.ibkr_futures.strategies)
gold_trend_mgc:
  status: live_probation
  probation_mode: fast_track        # ← NOUVEAU
  runtime_entrypoint: worker.py:_run_futures_cycle
  sizing_policy:
    mode: fast_track
    sizing_cap_fast_track: 1_contract_MGC
    signal_threshold_sigma: 1.25
  kill_criteria:
    fast_track:                      # ← NOUVEAU, strict
      max_consecutive_losses: 2
      max_drawdown_pct: -5.0
      max_divergence_sigma: 1.0
      rollback_on_ops_p0_p1: true
      rollback_on_data_stale_hours: 12
      rollback_on_runtime_audit_fail: true
  notes: "v8 2026-04-30: fast_track probation start (doctrine fast_track_promotion_policy.md). Exit 2026-05-14 decision."
```

```yaml
# config/quant_registry.yaml (extrait)
- strategy_id: gold_trend_mgc
  book: ibkr_futures
  status: live_probation
  paper_start_at: "2026-04-16"
  fast_track_start_at: "2026-04-30"   # ← NOUVEAU
  live_start_at: "2026-04-30"         # promoted (fast_track count as live)
  wf_manifest_path: data/research/wf_manifests/gold_trend_mgc_v1_2026-04-19.json
  grade: A
  last_wf_run_at: "2026-04-19"
  is_live: true
  infra_gaps: []
  notes: "B2 iter3 + fast_track start 2026-04-30. Sizing 1 contrat MGC seuil signal >= 1.25 sigma. Exit gate 2026-05-14."
```

### 3.4 Commit d'arming

```bash
git add config/live_whitelist.yaml config/quant_registry.yaml
git commit -m "feat(gold_trend_mgc): fast_track probation arming 2026-04-30

Strat promue paper_only -> live_probation (probation_mode=fast_track).
Doctrine: docs/audit/fast_track_promotion_policy.md
Plan: docs/audit/fast_track_launch_plan.md
Sizing: 1 contrat MGC, seuil signal >= 1.25 sigma.
Exit gate 2026-05-14 (J+14 fast_track).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

Puis déploiement VPS (pull + restart trading-worker), vérification `runtime_audit.py --strict` immédiat post-restart.

---

## 4. Revues J+3 / J+7 / J+14

### 4.1 Revue J+3 (2026-05-04 si arming 2026-04-30, ou 2026-05-05 si ven 2026-05-01 first trade)

**Objectif** : détection précoce anomalie ops. 5 min.

```bash
# 1. Nombre trades observés depuis arming
ssh vps "grep gold_trend_mgc logs/worker/worker.log | grep -c 'fast_track_order_submitted'"

# 2. PnL depuis arming
ssh vps "python scripts/live_pnl_tracker.py --strategy gold_trend_mgc --since 2026-04-30"

# 3. Divergence preliminary (1 point de donnée minimum)
curl http://178.104.125.74:8000/api/strategies/gold_trend_mgc/divergence?since=2026-04-30

# 4. Incident P0/P1 ?
grep "severity:P0\|severity:P1" /opt/trading-platform/data/incidents/*.jsonl | tail -5
```

**Décision** :
- 0 trades : normal si signal pas déclenché, observer encore.
- 1-2 trades perdants consécutifs : **WATCH**, continuer mais alerter user.
- 2 trades perdants consécutifs : **ROLLBACK IMMÉDIAT** (§5.1 doctrine).
- Divergence > 1σ sur trades observés : **ROLLBACK**.
- Ops incident P0/P1 ibkr_futures : **ROLLBACK**.

**Log** : 1 ligne dans `docs/audit/fast_track_review_log.md`.

### 4.2 Revue J+7 (2026-05-07)

**Objectif** : mid-checkpoint. 10 min.

```bash
# 1-4 comme J+3

# 5. Divergence robustness (sur >= 3 trades idéalement)
curl http://178.104.125.74:8000/api/strategies/gold_trend_mgc/divergence?since=2026-04-30

# 6. Corrélation vs CAM + GOR depuis arming
ssh vps "python scripts/marginal_contribution.py --strategy gold_trend_mgc --since 2026-04-30"
# Note : script non livré §10 P1. Alternative : calcul manuel depuis journal.

# 7. Runtime audit strict
ssh vps "python scripts/runtime_audit.py --strict"
```

**Décision** :
- `capital_at_risk_gold_trend_mgc < $150` respecté ? → sinon alerte sizing.
- Si < 2 trades à J+7 : **alerte fréquence**. Probable no-go à J+14 si < 4.
- Si PnL < -3% strat-level : **WATCH renforcée** (kill seuil -5%).

**Log** : 1 ligne.

### 4.3 Revue J+14 (2026-05-14) — gate exit

**Objectif** : décision upward ou rollback. 30 min.

```bash
# Gate §7.1 doctrine exit
python scripts/promotion_gate.py --mode=fast_track_exit --strat gold_trend_mgc
# Vérifie : fast_track_days=14, pnl_net >= 0, 0 kill trigger, trades >= 4, divergence <= 1σ, runtime exit 0, 0 P0/P1.
# Exit 0 : GO upward → probation_mode=standard
# Exit > 0 : NO-GO → rollback paper_only
```

**Si GO upward** :
- Édition `live_whitelist.yaml` : `probation_mode: standard`
- Édition `quant_registry.yaml` : `fast_track_start_at` conservé, note "fast_track_exit_at 2026-05-14 success"
- Commit + push + VPS deploy
- Sizing peut passer à 50-100% (i.e. retirer le filtre signal 1.25σ).
- Fenêtre live_probation standard 30j additionnels : exit 2026-06-13.

**Si NO-GO rollback** :
- Kill_switch trigger ou demote manuel via doctrine §7.2
- Fermeture position MGC ouverte si applicable
- Édition `live_whitelist.yaml` : retrait `probation_mode`, `status: paper_only`
- Édition `quant_registry.yaml` : `fast_track_start_at` archivé, `is_live: false`, `live_start_at` archivé
- Incident JSONL écrit
- Post-mortem obligatoire dans 48h (template §10 doctrine)
- Gate standard 30j pas avant 2026-06-14 (30j supplémentaires paper post-rollback).

---

## 5. Quand on décide promotion / rollback

| Date | Événement | Action concrète |
|---|---|---|
| 2026-04-28 | Deadline livraison P0 §2.1 (schéma + gate) | Git commits + push |
| 2026-04-30 matin | Gate arming §3.2 (11 checks) | Exécuter check-list, décision GO/NO-GO |
| 2026-04-30 après-midi | Si GO : édition YAMLs + commit + deploy VPS | §3.3–3.4 |
| 2026-05-01 | Premier trade potentiel (signal dep.) | Monitoring continu |
| **2026-05-04** | Revue J+3 | 5 min check, décision continue/rollback |
| **2026-05-07** | Revue J+7 | 10 min check, décision continue/rollback |
| **2026-05-14** | Gate §7.1 exit | 30 min check, **décision upward ou rollback** |
| 2026-05-16 | Si rollback : earliest gate standard 30j (standard timeline) | Reprise parcours standard |
| 2026-06-13 | Si upward : fin live_probation standard 30j | Gate → live_core ou continue probation |

---

## 6. Ce qui peut VRAIMENT trader dans les 7 prochains jours

**Rien de nouveau ne trade dans les 7 prochains jours en fast-track.**

- Paper_start `gold_trend_mgc` = 2026-04-16. Fast-track earliest arming = 2026-04-30 (J+14). On est le 2026-04-19. J+7 = 2026-04-26 → encore 4 jours avant J+14 arming.
- `gold_trend_mgc` continue donc en **paper** les 7 prochains jours. On observe fréquence, divergence, PnL paper.
- Les 2 LIVE existantes (CAM MCL + GOR) continuent leur trajectoire actuelle (MCL position ouverte +$295 unrealized, GOR signal dormant).

**Capital at-risk dans 7 jours** : inchangé ~$228 (MCL). Pas d'augmentation.

**Capital at-risk au moment de l'arming (2026-04-30, si GO)** : $228 + ~$100-130 (MGC SL) = **~$330-360** = **1.60-1.73%** capital total.

---

## 7. Combien de capital supplémentaire utilisé sans dégrader le risk

### 7.1 Pendant fast-track (2026-04-30 → 2026-05-14)

- **Additif** : +$100-130 risk-if-stopped MGC (1 contrat).
- **Cumul capital_at_risk desk** : $228 + $130 = **$358 max** = 1.72%.
- **Marge vs hard cap §4.4** : $358 < 5% × $20,856 = $1,043 ✅.
- **Marge vs limit futures global** : 2 contrats simultanés (MCL + MGC) < 4 max ✅.
- **Marge vs limit CAM per-symbol** : MCL=1 (CAM) + MGC=1 (gold_trend) ≠ même symbol ✅.

### 7.2 Après fast_track exit success (2026-05-14)

- Sizing peut passer au niveau `live_probation standard` : ~2 contrats MGC autorisés si l'allocation évolue. Risque + $200-260.
- Cumul desk : $228 + $260 = $488 = 2.34% capital total. Toujours < 5%.

### 7.3 Réponse directe à la question

- Fast-track : **+$130 at-risk en pointe (0.63% du capital)**.
- Standard post-fast-track success : **+$260 (1.25%)**.
- **Aucune dégradation risk** sur aucun seuil `config/limits_live.yaml`.

---

## 8. Est-ce que ça aide le ROC ou juste l'occupation ?

### 8.1 Ça aide le ROC — conditions

**Oui si** :
- Les trades fast-track sont **profitables nets** après slippage Alpaca/IBKR.
- La **décorrélation** gold_trend vs CAM + GOR se confirme (|corr| < 0.60 estimée).
- Edge WF V1 (Sharpe 2.625 OOS) se traduit live avec divergence < 1σ.

### 8.2 Ça n'aide pas — symptômes

**Non (juste occupation) si** :
- PnL net ≤ 0 sur fenêtre.
- Divergence > 1σ (edge dilué).
- Trades < 4 en 14j (échantillon nul).

Dans tous ces cas, la doctrine §5 trigger rollback, et on capitalise le learning sans avoir occupé gratuitement.

### 8.3 Réponse directe

**Ça aide le ROC marginal contribution** (cf. [roc_reporting_contract.md](roc_reporting_contract.md) §2.4) seulement si gold_trend_mgc affiche un `marginal_contribution > 3% annualisé` avec `corr_factor > 0.5`. À mesurer au J+14 et au-delà via `scripts/marginal_contribution.py` (P1 backlog, absent aujourd'hui — mesure manuelle via live_pnl_tracker en attendant).

**Si pas d'impact ROC** mesurable à J+14, alors fast-track = occupation, et rollback §7.2 s'applique. On ne maintient pas une strat "pour remplir".

---

## 9. Scénario d'échec le plus probable

### 9.1 Scénario échec #1 (probabilité estimée ~25-30%) : "gold range-bound"

- Gold cote en fourchette sans trend fort sur 14j.
- Signal trend >= 1.25σ ne déclenche pas → **0-2 trades observés**.
- Fast-track invalidé §3.3 règle 14 (< 4 trades) → rollback auto à J+14.
- **Capital perdu** : 0 (pas de trade perdant). Capital d'opportunité : 14 jours perdus + charge review 2×/semaine pour rien.
- **Leçon** : assouplir seuil signal (1.0σ au lieu de 1.25σ) au prochain arming ? ou accepter que fréquence est structurellement limite pour fast-track.

### 9.2 Scénario échec #2 (probabilité ~15-20%) : "2 pertes consécutives début"

- Signal déclenche 2-3 fois en début fast-track, mais entries malchanceuses.
- 2 pertes consécutives → kill §5.1 trigger → rollback immédiat à J+5-7.
- **Capital perdu** : 2 × ~$130 = **~$260 réalisé** (1.25% capital total).
- **Leçon** : le grade A WF n'est pas garantie ; le bucket échantillon réduit (2 trades vs 10 en paper 30j) = variance haute.
- Post-mortem : incident #1 du desk, documenté. Strat retourne paper_only.

### 9.3 Scénario échec #3 (probabilité ~5-10%) : "ops incident"

- Incident infra ibkr_futures (IB Gateway, data freshness, etc.) → rollback auto doctrine §5.1.
- **Capital perdu** : variable selon timing (position ouverte fermée à la market).
- **Leçon** : la doctrine kill strict (§5) limite les dégâts. Pas catastrophique, mais frustration ops.

### 9.4 Scénario échec #4 (probabilité ~5%) : "corrélation GOR simultanée"

- GOR activation signal simultanée → double exposition gold non anticipée.
- Si move gold adverse : pertes cumulées dépassent attendu.
- **Mitigation par design** : sizing MGC faible (1 contrat, $100-130 risk) = plafond absolu sur l'exposition supplémentaire.

### 9.5 Pire cas réaliste

**$260-400 réalisé perdu** (2% du capital) + 14 jours sans information nouvelle.

**Pire cas ≠ catastrophique**. La doctrine est construite pour que la pire perte = acceptable vs le gain d'information si ça marche.

### 9.6 Meilleur cas réaliste

- 5-8 trades observés, PnL net +$150-300, divergence < 1σ, 0 kill.
- Exit upward le 2026-05-14 → passage `live_probation standard` avec sizing relâché.
- **Gain informationnel** : 14 jours de live réel en avance sur gate standard + sizing relâché 2 semaines plus tôt.
- **Gain P&L** : +$150-300 sur fenêtre = +0.75-1.5% capital sur 14j = projection annualisée +20-40% pour cette strat seule (à haircut ensuite).

---

## 10. Checklist final avant arming (à cocher le 2026-04-30)

```
PRÉ-REQUIS CODE (§2.1)
[ ] probation_mode dans schéma live_whitelist.yaml
[ ] fast_track_start_at dans schéma quant_registry.yaml
[ ] promotion_check.py --mode fast_track --min-paper-days 14
[ ] pre_order_guard.check_6 sizing_cap fast_track (peut être différé)
[ ] kill_switch_live kill_criteria.fast_track strict (peut être différé)

GATE §3.2 (§3.2 check-list 11 items)
[ ] runtime_audit exit 0
[ ] promotion_check fast_track exit 0
[ ] paper pnl_net >= 0
[ ] divergence <= 1σ
[ ] trades paper >= 4
[ ] book_health ibkr_futures GREEN
[ ] 0 incidents P0/P1 7j
[ ] kill_switch inactif
[ ] 0 slot fast_track occupé
[ ] 4 systemd services active
[ ] pas de position GOR ouverte simultanée (ou gérée)

ÉCRITURE CONFIG (§3.3)
[ ] live_whitelist.yaml édité (probation_mode, sizing_policy, kill_criteria)
[ ] quant_registry.yaml édité (fast_track_start_at, live_start_at, is_live)

COMMIT + DEPLOY
[ ] git commit avec message doctrine (§3.4)
[ ] git push origin main
[ ] VPS pull + restart trading-worker
[ ] runtime_audit post-restart exit 0

NOTIFICATION
[ ] Entry dans docs/audit/fast_track_review_log.md (1re ligne)
```

---

## 11. Rôle des docs canoniques pendant fast-track

| Doc | Rôle | Action |
|---|---|---|
| [desk_operating_truth.md](desk_operating_truth.md) | Entry operateur | Mettre à jour §1 "qui trade maintenant" : 3 strats (CAM + GOR + gold_trend_mgc fast_track) post-arming |
| [strategy_inventory_clean.md](strategy_inventory_clean.md) | Inventaire | Déplacer gold_trend_mgc de §2.2 vers §2.1 avec annotation `(fast_track)` post-arming |
| [canonical_truth_map.md](canonical_truth_map.md) | Precedence/invariants | Aucun changement (les invariants I1-I12 restent valides) |
| [roc_reporting_contract.md](roc_reporting_contract.md) | ROC | Capital_at_risk passe de 1.09% à ~1.72% post-arming. Capital idle passe de 98.9% à 98.3% |
| [runtime_hygiene_matrix.md](runtime_hygiene_matrix.md) | Autorité | Mention `probation_mode=fast_track` dans statuts canoniques dérivés |
| [ops_hygiene_checklist.md](ops_hygiene_checklist.md) | Ops | Ajouter check jeudi matin pendant 14j fast-track |

---

## 12. Ce qui **n'est pas** dans ce plan (explicite)

- **mes_monday_long_oc** : parcours standard 30j, gate 2026-05-16. Pas fast-track. Cf. [fast_track_candidates_review.md](fast_track_candidates_review.md) §2.
- **btc_asia_q80_long_only** : parcours standard 30j, gate 2026-05-20. Pas fast-track. Cf. §7 review.
- **alt_rel_strength** : parcours standard 30j, gate 2026-05-18 + levée B6r data BTCUSDT alts. Pas fast-track (fréquence structurelle hors scope).
- **Binance_crypto fast-track** : **pas envisagé** tant qu'aucune candidate grade A/S. Le capital reste idle volontairement (cf. [fast_track_candidates_review.md](fast_track_candidates_review.md) §7 challenge §11.4).
- **Assouplissement doctrine §3.1 règle 1 (grade A/S)** : **refusé** par défaut. Si user veut le rouvrir, discussion explicite nécessaire — ne pas assouplir par défaut.
- **2e fast-track simultané** : **interdit** par §3.4 règle 16 tant que gold_trend_mgc est en fast-track.

---

## 13. DoD plan de lancement

- ✅ Quelles strats fast-track maintenant : **1 seule** (gold_trend_mgc V1). Justifié §1 + review §1 doc candidates.
- ✅ Sur quel book : `ibkr_futures`.
- ✅ Sizing : 1 contrat MGC + seuil signal ≥ 1.25σ.
- ✅ Date d'arming : **2026-04-30** (J+14 paper, pas avant).
- ✅ Vérifications avant 1er trade : check-list §3.2 (11 items) + §10 (code P0 + gate + édition config + deploy).
- ✅ Revues J+3 / J+7 / J+14 : §4.
- ✅ Décision promotion / rollback : date fixe **2026-05-14** au gate §7.1 exit.
- ✅ Test de réalité — 4 questions :
  - *Qu'est-ce qui peut trader dans 7 jours ?* Rien de nouveau (§6).
  - *Capital supplémentaire utilisé sans dégrader le risk ?* +$130 à-risk, 0.63% capital (§7).
  - *Aide le ROC ou juste l'occupation ?* ROC si marginal_contribution > 3%, sinon rollback (§8).
  - *Scénario d'échec le plus probable ?* Range-bound / 0-2 trades à J+14 → rollback auto sans perte capital, juste perte temps (§9.1, 25-30%).

---

**Fin plan de lancement.** Prochaine action user : décider GO/NO-GO **sur cette doctrine** (pas sur gold_trend_mgc lui-même — cela ce sera décidé 2026-04-30). Si GO : livrer les 3 items P0 code (§2.1) dans la semaine.
