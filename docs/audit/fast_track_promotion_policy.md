# Fast-Track Promotion Policy — `live_fast_track_probation`

**As of** : 2026-04-19T17:00Z
**Owner** : PO + governance
**Couplage obligatoire** : [canonical_truth_map.md](canonical_truth_map.md), [strategy_inventory_clean.md](strategy_inventory_clean.md), [roc_reporting_contract.md](roc_reporting_contract.md), [runtime_hygiene_matrix.md](runtime_hygiene_matrix.md), [desk_operating_truth.md](desk_operating_truth.md).
**Verdict** : doctrine active. Tout promo fast-track qui ne respecte pas ce doc est **invalide**.

---

## 0. Raison d'être

Le desk a ~$20K déployables, 1 position ouverte ($228 at-risk), **98.9 % capital idle**. La rentabilité ne viendra pas de nouveaux backtests : elle vient **d'observer en live réel** ce qui a été validé en paper, avec petit sizing et kill strict.

La fenêtre standard promotion (30j paper + gate complet) est bonne pour `live_core` mais **trop lente** pour augmenter le nombre de strats live actives avant fin mai. Objectif : **ajouter 1 à 2 strats live à sizing réduit en < 3 semaines**, sans bouger le niveau de preuve quant requis.

**Ce doc n'autorise PAS à court-circuiter la validation quant.** Il accélère uniquement la **fenêtre d'observation paper** (30j → 14j) en contrepartie d'un sizing écrasé, d'un kill strict, et d'une revue manuelle haute fréquence.

---

## 1. Définition canonique `live_fast_track_probation`

`live_fast_track_probation` est un **mode opérationnel** de `live_probation`, pas un 5ᵉ statut dans l'enum canonique.

### Matérialisation technique (compatible registries existants)

| Champ | Fichier | Valeur fast-track | Valeur standard |
|---|---|---|---|
| `status` | `live_whitelist.yaml` + `quant_registry.yaml` | `live_probation` | `live_probation` |
| `probation_mode` | `live_whitelist.yaml` (**nouveau champ**) | `fast_track` | `standard` ou absent |
| `fast_track_start_at` | `quant_registry.yaml` (**nouveau champ**) | `YYYY-MM-DD` | `null` ou absent |
| `live_start_at` | `quant_registry.yaml` | date de bascule paper → live | idem |
| `sizing_policy` | `live_whitelist.yaml` | `sizing_cap_fast_track: X` (règle §4) | sizing standard |
| `kill_criteria` | `live_whitelist.yaml` | **strictes** (règle §5) | standard |

**Justification choix minimaliste** : 0 modification des 4 registres canoniques (enum `status` intact), 0 invariant I1-I12 cassé, tests existants préservés. Le statut canonique dérivé par `runtime_audit.py` sera `ACTIVE (fast_track)` — suffixe dérivé, pas nouveau enum.

### Source de vérité finale

- **Décision "X est en fast-track"** = `live_whitelist.{strat}.probation_mode == "fast_track"` (autorité PR + governance).
- **Décision "X peut trader maintenant en fast-track"** = chaîne AND canonique (cf. [canonical_truth_map.md](canonical_truth_map.md) §9 Q1) **+** `sizing_cap_fast_track` respecté **+** `kill_switch` lit kill_criteria fast_track.
- **Sortie fast-track → live_probation standard** = décision `promotion_gate.py --mode=fast_track_exit` (§7) ; `probation_mode` passe à `standard`, sizing normal débloqué.
- **Sortie fast-track → paper_only** (rollback) = `kill_switch` trigger OR décision manuelle ; `status` passe à `paper_only`, `probation_mode` réinitialisé à absent.

---

## 2. Différences `paper_only` / `fast_track` / `probation` / `core`

| Dimension | `paper_only` | `live_fast_track_probation` | `live_probation` standard | `live_core` |
|---|---|---|---|---|
| **Trade réel** | Non (simulation) | **Oui** | Oui | Oui |
| **Capital at-risk engagé** | 0 | ≤ 25–33 % sizing normal | 50–100 % sizing normal | Full sizing |
| **Fenêtre observation min.** | 30j calendaires | **14j calendaires** (~10 weekdays actifs) | 30j calendaires | Permanent |
| **Divergence max paper/backtest tolérée** | n/a | 1σ strict | 2σ | n/a (obs continue) |
| **Grade quant requis** | S / A / B / REJECTED (REJECTED bloque) | **S ou A uniquement** | S / A / B | S / A / B |
| **WF manifest physique requis** | Oui (sauf exempt) | **Oui, sans exemption** | Oui (sauf exempt) | Oui |
| **Kill criteria** | n/a (paper) | **Strictes** (§5) | Standard | Standard |
| **Revue humaine** | Hebdo (lundi) | **2×/semaine (lundi + jeudi)** | Hebdo | Mensuelle |
| **Slots simultanés max** | Illimité | **1 par book, 2 au total desk** | 2 par book | Par capital dispo |
| **Rollback vers paper_only** | n/a | Immédiat sur kill OR manuel | Sur kill OR décision comité | Rare, post-audit |
| **Transition upward** | → `fast_track` OU `probation` standard | → `live_probation` standard si 14j OK | → `live_core` si 60j OK + observation ≥ 30 trades | — |

### Règle cardinale (anti-flou)

`live_fast_track_probation` **n'est pas** :
- un moyen de bypasser le grade minimal (B toléré en standard ≠ B en fast-track → **B interdit**),
- un moyen de bypasser WF manifest,
- un moyen de promouvoir une strat REJECTED ou archived,
- un statut "joli" accroché à une strat sans validation quant.

Si une strat ne passe pas le gate §3, **elle reste paper_only**.

---

## 3. Conditions d'entrée (gate strict)

Une strat peut entrer `live_fast_track_probation` si et seulement si **toutes** ces conditions sont remplies simultanément :

### 3.1 Quant (conditions sine qua non)

1. `quant_registry.{strat}.grade` ∈ {`S`, `A`} — **B refusé**.
2. `quant_registry.{strat}.wf_manifest_path` existe physiquement sur disque.
3. `quant_registry.{strat}.wf_exempt_reason` est **null ou absent** (pas d'exemption en fast-track).
4. WF manifest atteste : ≥ 50% OOS windows profitables, DSR p < 0.01, MC P(DD>30%) < 15%.
5. `quant_registry.{strat}.paper_start_at` renseigné et `today - paper_start_at >= 14 jours`.
6. `paper_pnl_net ≥ 0` sur la fenêtre paper complète (observée via `live_pnl_tracker.py` paper mode).
7. Aucune divergence observée paper/backtest `> 1σ` sur la fenêtre paper.

### 3.2 Runtime (conditions sine qua non)

8. `books_registry.{book}.mode_authorized == "live_allowed"`.
9. `health_registry.{book}` → `book_health.check_{book}()` retourne `GREEN` (pas `DEGRADED`, pas `BLOCKED`).
10. `runtime_audit.py --strict` exit 0 sur VPS au moment de l'arming.
11. Aucun incident P0 ou P1 ouvert pour ce book dans les 7 jours précédents.
12. Kill switch book **inactif** (live ET fast-track scopes).
13. `infra_gaps` dans `quant_registry.{strat}` = **liste vide** (aucun blocker infra).

### 3.3 Stratégie-spécifique (conditions sine qua non)

14. Fréquence observable : la strat doit générer **≥ 4 trades en 14 jours** en paper sur fenêtre récente, sinon le fast-track n'apporte rien de statistiquement observable → refusé. (Une strat hebdo à 1 trade/semaine = 2 trades en 14j = échantillon nul, cf. §11.2 raison du refus mes_monday.)
15. Corrélation attendue avec les strats déjà `live_core` ou `live_probation` du même book : `|corr_rolling_90d| < 0.60`. Au-delà, l'ajout en fast-track n'augmente pas la décorrélation portfolio et ajoute du bruit ops sans ROC marginal (cf. [roc_reporting_contract.md](roc_reporting_contract.md) §2.4).

### 3.4 Slot capacité (conditions sine qua non)

16. Slot fast-track disponible : **max 1 slot par book**, **max 2 slots au total desk** (tous books confondus).
17. Capital at-risk total desk après arming ≤ **5%** du capital deployable total (pas 10%, cf. §4.4).

### Gate opérationnel

```bash
# À exécuter juste avant l'arming. Exit 0 = GO, exit > 0 = NO_GO.
python scripts/promotion_check.py {strat} --mode fast_track --min-paper-days 14
```

**Note** : ce script n'existe pas encore en mode fast-track. Gap implémentation §10.

---

## 4. Sizing — cap strict

### 4.1 Principe

`sizing_cap_fast_track = max(0.25 × sizing_live_normal, min_broker_unit)` pour **grade A**.
`sizing_cap_fast_track = max(0.33 × sizing_live_normal, min_broker_unit)` pour **grade S**.

### 4.2 Contrainte broker incompressible

Certains instruments ont un **minimum broker de 1 contrat/unité** non divisible :
- Micro futures (MGC, MES, MCL) : 1 contrat = unité min. **Pas de fraction possible**.
- Actions : 1 share.
- Crypto spot Binance : $10 min notional par ordre.

Quand la règle 25%/33% donne `< 1 unité`, on applique **la règle de substitution** : **1 unité broker minimum, appliquer la réduction par la fréquence**.

### 4.3 Règle de substitution "fréquence réduite"

Quand sizing nominal = 1 unité broker :
- Arm la strat **uniquement sur signaux à force ≥ 1.25σ** au lieu du seuil paper standard → réduit fréquence de trades à ~40-50%.
- OR désactive les jours à faible edge historique (Ex : MES_monday ne trade pas si VIX > 25).

### 4.4 Hard caps de sécurité (cap global desk)

Quel que soit le calcul sizing ci-dessus :
- `capital_at_risk_per_trade_fast_track ≤ $150`
- `capital_at_risk_total_desk_fast_track ≤ 5% capital_deployable_total`
- Pour ibkr_futures : ne pas ajouter de slot fast-track si `capital_at_risk_book_futures > 2%` (CAM + GOR combined).

### 4.5 Table sizing par candidate (section 11)

| Strat | Sizing live normal | Sizing fast-track | Risk-if-stopped fast-track |
|---|---|---|---|
| `gold_trend_mgc V1` | 1 contrat MGC | 1 contrat MGC (min broker) + seuil signal 1.25σ | ~$100–130 |
| `btc_asia_mes_leadlag_q80_long_only` | $3K gross | $750 gross (25%) | ~$75 (stop 10%) |
| (autres candidates : refusées §11) | — | — | — |

---

## 5. Kill criteria — stricter que `live_probation` standard

### 5.1 Triggers rollback immédiat vers paper_only

| Trigger | Seuil fast-track | Seuil standard |
|---|---|---|
| **Pertes consécutives** | **2 trades perdants consécutifs** | 3–5 selon strat |
| **Drawdown strat-level** | **-5%** sur la fenêtre 14j | -10% sur 30j |
| **Divergence realized vs backtest** | **> 1σ sur 5+ trades observés** | > 2σ |
| **Incident ops P0 book** | Rollback immédiat | Rollback standard |
| **Incident ops P1 book** | Rollback immédiat | Monitoring renforcé |
| **Data freshness anormale** | Rollback immédiat si stale > 12h | Tolérance 24h |
| **Runtime audit exit ≠ 0** | Rollback immédiat | Monitoring |
| **Kill switch book triggered** | Rollback + désactivation fast-track slot | Standard |
| **Manuelle user** | Immédiat sur demande | Standard |

### 5.2 Kill hiérarchique

1. **Niveau strat** : kill_criteria strat-level → demote `paper_only`, capital libéré.
2. **Niveau book** : kill switch book → ferme positions, demote **tous** les fast-tracks du book, demote aussi live_probation et live_core selon policy.
3. **Niveau desk** : kill switch global → halt worker complet.

Un trigger fast-track **ne doit pas** cascade automatique sur les `live_core` du même book (le niveau 1 est isolé par design). Mais un trigger niveau 2 ou 3 affecte les fast-tracks.

### 5.3 Post-mortem obligatoire

Chaque rollback fast-track → paper_only déclenche un post-mortem minimum, template à venir (gap §10 = `scripts/post_mortem_template.py` déjà backlog Phase 2).

---

## 6. Durée d'observation

- **Minimum** : 14 jours calendaires (soit ~10 weekdays actifs IBKR futures / continu crypto).
- **Pas de prolongation** : à J+14, le gate §7 trancher upward/downward. Pas de "on prolonge encore 3 jours".
- **Raison du 14j** : au-delà, la valeur informative décroît (on attend un volume de trades qui ne vient pas pour les strats basse-fréquence), et on crée un statut intermédiaire flou.

### Extension exceptionnelle (cas rare)

Si la strat n'a pas généré **au moins 4 trades** en 14j (§3.3 règle 14) → le fast-track est invalidé, rollback `paper_only` automatique. Ne pas prolonger.

---

## 7. Conditions de sortie

### 7.1 Sortie upward : `fast_track` → `live_probation` standard

Toutes les conditions simultanément à J+14 :

1. `fast_track_days >= 14`.
2. `pnl_net_fast_track ≥ 0` (réalisé + unrealized).
3. Aucun kill trigger §5 déclenché.
4. Nombre de trades observés ≥ 4.
5. Divergence realized vs backtest `≤ 1σ`.
6. Runtime audit exit 0 **tous les jours** de la fenêtre (0 exception).
7. Aucun incident P0 ou P1 ouvert à J+14.

Action : `promotion_gate.py --mode=fast_track_exit --strat {strat}` → bascule `probation_mode=standard`, sizing passe à 50–100% normal, durée `live_probation` standard démarre (compteur remis à zéro, 30j additionnels).

### 7.2 Sortie downward : `fast_track` → `paper_only` (rollback)

Trigger **un seul** des §5 → rollback **immédiat**, même si J+14 pas atteint.

Action :
1. `kill_switch_live` ou `kill_switch_crypto` triggered → positions fermées.
2. `probation_mode` retiré de `live_whitelist`.
3. `status` passe à `paper_only`.
4. `fast_track_start_at` dans `quant_registry` est archivé (conservé à `null` + commentaire dans notes).
5. Incident JSONL écrit avec cause.
6. Post-mortem manuel dans les 48h.

### 7.3 Pas de passage direct `fast_track` → `live_core`

**Interdit**. Transitions autorisées :
- `paper_only` → `live_fast_track_probation` (via §3)
- `live_fast_track_probation` → `live_probation` standard (via §7.1)
- `live_probation` standard → `live_core` (gate standard 60j)
- Rollback depuis n'importe quel live → `paper_only` (§7.2)

Une strat **ne devient jamais `live_core` en < 44 jours** (14 fast-track + 30 probation). Raison : on a besoin d'au minimum 2 régimes marché observés.

---

## 8. Revue humaine 2×/semaine

### 8.1 Rythme fixe

- **Lundi matin** : check ops global + review fast-track J+N (session 15 min).
- **Jeudi matin** : check fast-track only (session 10 min).
- Fréquence inchangée pour `live_probation` et `live_core` (lundi seulement).

### 8.2 Contenu review lundi (15 min)

```bash
# 1. Runtime audit (30s)
ssh vps "cd /opt/trading-platform && python scripts/runtime_audit.py --strict"

# 2. Live PnL fast-track (30s)
ssh vps "python scripts/live_pnl_tracker.py --summary --mode fast_track"

# 3. Trades fast-track depuis la dernière revue (2 min)
ssh vps "tail -500 logs/worker/worker.log | grep -E 'fast_track|{strat_id}'"

# 4. Divergence paper_backtest (manuel, 5 min) — vérif visuelle dashboard
curl http://178.104.125.74:8000/api/strategies/{strat}/divergence?window=fast_track

# 5. Décision rollback ? (manuel, 5 min)
# - PnL négatif > 2 trades consécutifs ?
# - Divergence > 1σ ?
# - Ops incidents ?
```

### 8.3 Contenu review jeudi (10 min)

Versions allégées : seulement §8.2 items 1 et 3, lecture dashboard.

### 8.4 Documentation

Chaque review écrit **1 ligne** dans `docs/audit/fast_track_review_log.md` :
```
YYYY-MM-DD | strat | status J+N/14 | PnL $X | trades N | divergence σ | action (continue|rollback|escalate)
```

Fichier créé par la première review. Non versionné avant première entry (principe "pas d'artefact fantôme").

---

## 9. Source de vérité par question

| Question | Source canonique | Commande |
|---|---|---|
| Strat X est-elle en fast-track ? | `live_whitelist.{strat}.probation_mode == "fast_track"` | `grep -A2 {strat} config/live_whitelist.yaml` |
| Depuis quand ? | `quant_registry.{strat}.fast_track_start_at` | `grep -A5 {strat} config/quant_registry.yaml` |
| Peut-elle trader maintenant ? | Chaîne AND canonical_truth_map §9 Q1 **+** `book_health == GREEN` | `promotion_check.py {strat} --check-only` |
| Son sizing est-il correct ? | `pre_order_guard.py` check 6 | inline par ordre |
| Est-elle éligible upward à J+14 ? | Gate §7.1 | `promotion_gate.py --mode=fast_track_exit --dry-run` |
| A-t-elle subi rollback ? | `quant_registry.{strat}.fast_track_start_at` archived + incident JSONL | `grep fast_track_rollback data/incidents/` |

---

## 10. Gaps implémentation (backlog Phase 2)

Pour que la doctrine soit **opérationnellement active** (et pas juste doctrinale), ces artefacts doivent être livrés :

### P0 (bloquant si on fast-track une 2ᵉ strat)

1. **`scripts/promotion_check.py --mode fast_track --min-paper-days 14`** — extension du script existant avec logique gate §3. Aujourd'hui le script existe mais fait seulement le gate 30j standard.

### P1 (avant 2e fast-track)

2. **`scripts/promotion_gate.py --mode=fast_track_exit`** — transition upward (§7.1). Aujourd'hui `promotion_gate.py` existe mais ne gère que le gate 30j.
3. **Champ `probation_mode`** ajouté dans le schéma `live_whitelist.yaml` (+ validation `core/governance/live_whitelist.py`).
4. **Champ `fast_track_start_at`** ajouté dans le schéma `quant_registry.yaml` (+ validation `core/governance/quant_registry.py`).
5. **`pre_order_guard.check_6`** — vérifie `sizing_cap_fast_track` quand `probation_mode=fast_track`.
6. **`kill_switch_live.fast_track_criteria`** — lit les criteria stricter depuis live_whitelist en mode fast-track.

### P2 (avant 3e fast-track)

7. **`scripts/fast_track_review.py`** — génère l'entrée ligne §8.4 automatiquement.
8. **`scripts/post_mortem_template.py`** — déjà backlog P2, requis en cas rollback.
9. **Dashboard widget `/fast-track`** — affichage statut + PnL + divergence en temps réel.

### Tolérance pre-livraison P0

**Jusqu'à livraison du P0** (gap §10.1) : le gate fast-track se fait **manuellement** via exécution ligne-à-ligne des 17 conditions §3. Un seul fast-track simultané autorisé dans cette période (pas deux en parallèle sans gate automatisé).

---

## 11. Application aux candidates listées (résumé, détail dans [fast_track_candidates_review.md](fast_track_candidates_review.md))

### 11.1 `FAST_TRACK_NOW` (1 candidate)

| Strat | Book | Justification courte |
|---|---|---|
| `gold_trend_mgc V1` | ibkr_futures | Grade A, MC P(DD>30%)=0.15%, 0 infra_gaps, WF V1 propre, MGC micro sizing |

### 11.2 `DO_NOT_FAST_TRACK` (5 candidates)

| Strat | Raison dure |
|---|---|
| `mes_monday_long_oc` | Grade B (interdit §3.1 règle 1) **et** fréquence 1 trade/semaine = max 2 trades en 14j = zéro observation statistique (§3.3 règle 14) |
| `mes_wednesday_long_oc` | MC P(DD>30%)=28.3% > 15% (§3.1 règle 4) + grade B |
| `mes_pre_holiday_long` | Fréquence 8-10 trades/an = 0-1 trades en 14j (§3.3 règle 14) + grade B |
| `mcl_overnight_mon_trend10` | `infra_gaps` non vide (friday_trigger re-WF pending) (§3.2 règle 13) + grade B |
| `alt_rel_strength_14_60_7` | 2 infra_gaps (hebdo 4 trades max/30j + BTCUSDT alts stale) → **2 blockers cumulés** (§3.2 règle 13) + fréquence insuffisante |

### 11.3 `FAST_TRACK_IF` (1 candidate, conditionnel)

| Strat | Book | Prérequis déblocage |
|---|---|---|
| `btc_asia_mes_leadlag_q80_v80_long_only` | binance_crypto | Paper 2026-04-20 démarré effectivement + 14j paper clean + grade B accepté en fast-track **uniquement si §3.1 règle 1 assouplie** (cf. §11.4 challenge règle grade) |

### 11.4 Challenge règle "grade A/S seulement" pour binance_crypto

La règle §3.1 règle 1 (grade S ou A seulement) est stricte. Elle refuse TOUT grade B, y compris `btc_asia q80_long_only`. Cela signifie **zéro candidate binance_crypto éligible** actuellement.

**Challenge honnête** : si on maintient règle stricte, seul `gold_trend_mgc` entre en fast-track et on reste avec 2 LIVE + 1 FAST = 3 strats actives (seulement sur ibkr_futures). Capital binance reste 100% idle.

**Option d'assouplissement** : autoriser grade B en fast-track **uniquement si** :
- Sharpe WF ≥ 1.0 **et**
- MC P(DD>30%) < 10% **et**
- `infra_gaps` vide **et**
- fréquence ≥ 4 trades/14j observée en paper.

Avec cet assouplissement, `btc_asia q80_long_only` deviendrait éligible à partir de 2026-05-04.

**Ma reco** : **PAS d'assouplissement maintenant**. Raison : on vient de drain bucket A crypto (11 strats archivées REJECTED au 2026-04-19). Ouvrir le fast-track au grade B sur binance_crypto = prendre le risque d'une 12e archivée, juste pour occupation. Le capital Binance qui reste idle ($9,843) **n'est pas un problème** tant qu'on n'a pas d'edge prouvé grade A/S sur crypto.

Attendre une vraie candidate grade A crypto (une qui sort du pipeline recherche post 2026-05) avant d'ouvrir binance_crypto en fast-track.

---

## 12. Anti-règles (règles rouges absolues)

**Il est interdit de** :

1. Fast-tracker une strat `grade=REJECTED` ou `archived_rejected` (règle de section 2 `roc_reporting_contract.md`).
2. Fast-tracker une strat sans `wf_manifest_path` physique (règle invariant I11).
3. Fast-tracker avec `wf_exempt_reason` (ex : meta-portfolio). Les méta-portefeuilles passent par le gate standard 30j.
4. Ouvrir **plusieurs fast-tracks sur le même book simultanément** (§3.4 règle 16).
5. Prolonger un fast-track au-delà de 14 jours (§6). À J+14, upward ou rollback.
6. Maintenir en fast-track une strat qui a subi un trigger §5 "pour lui donner une chance". Rollback immédiat, pas de seconde chance en fast-track. Retour paper_only, 30j standard requis avant prochaine tentative.
7. Utiliser fast-track pour **"remplir l'occupation"** au sens [roc_reporting_contract.md](roc_reporting_contract.md) §5.2. Le fast-track est pour du ROC prouvé, pas pour du bruit.
8. Sauter le post-mortem obligatoire après rollback (§5.3).
9. Fast-tracker une strat dont le book n'a pas `mode_authorized == live_allowed` (§3.2 règle 8).
10. Éluder la revue 2×/semaine (§8) pendant la fenêtre fast-track.

---

## 13. Compatibilité avec docs canoniques (contrôle intégrité)

| Doc canonique | Contrainte à respecter | Vérif |
|---|---|---|
| [canonical_truth_map.md](canonical_truth_map.md) | §2 précédence + §3 invariants I1-I12 | **OK** : probation_mode est un attribut de live_whitelist, pas un override du status enum. I3 (couplage strat x status) reste valide. |
| [state_file_contracts.md](state_file_contracts.md) | `positions_live.json`, `equity_state.json` P0 comportement | **OK** : le fast-track écrit normalement dans ces fichiers P0, pas de nouvel artefact state. |
| [runtime_hygiene_matrix.md](runtime_hygiene_matrix.md) | `scripts/runtime_audit.py` = autorité statut canonique | **OK** : runtime_audit dérive `ACTIVE (fast_track)` en suffixe, pas nouveau enum. |
| [roc_reporting_contract.md](roc_reporting_contract.md) | §5.1 sleeves qui augmentent ROC, §5.2 pas d'occupation | **OK** : §11 ci-dessus applique strictement règle ROC-contrib uniquement, pas occupation. |
| [strategy_inventory_clean.md](strategy_inventory_clean.md) | Tableau canonique 16 strats | **IMPACT MINEUR** : ajouter colonne ou note "fast_track candidate" post-activation effective gold_trend_mgc. |
| [ops_hygiene_checklist.md](ops_hygiene_checklist.md) | Checklist lundi matin | **OK** : la review §8 s'ajoute sans concurrence, même commandes. |
| [scoring_policy.md](scoring_policy.md) | Pas d'inflation | **OK** : aucune décision ne donne un "score" automatique. Les décisions sont binaires (gate pass / fail). |

---

## 14. DoD doctrine

- ✅ Définition canonique matérialisée sans casser l'enum `status` (§1)
- ✅ Différences avec 3 autres statuts (tableau §2)
- ✅ 17 conditions d'entrée listées (§3)
- ✅ Sizing avec règle fallback unité broker (§4)
- ✅ Kill criteria stricter que standard, tableau comparatif (§5)
- ✅ Durée fixe 14j, pas de prolongation (§6)
- ✅ 2 voies de sortie claires + gate (§7)
- ✅ Revue 2×/semaine ritualisée (§8)
- ✅ Source de vérité par question (§9)
- ✅ Gaps implémentation listés P0/P1/P2 (§10)
- ✅ Application aux 7 candidates user, verdict par strat (§11)
- ✅ Anti-règles explicites (§12)
- ✅ Compatibilité canoniques vérifiée (§13)

---

**Fin doctrine.** Les 2 livrables suivants opérationnalisent : [fast_track_candidates_review.md](fast_track_candidates_review.md) (review strat-par-strat) et [fast_track_launch_plan.md](fast_track_launch_plan.md) (plan concret 2026-04-20 → 2026-05-18).
