# Fast-Track Candidates Review

**As of** : 2026-04-19T17:15Z
**Doctrine** : [fast_track_promotion_policy.md](fast_track_promotion_policy.md) (gate §3, sizing §4, kill §5, durée §6).
**Inventaire source** : [strategy_inventory_clean.md](strategy_inventory_clean.md) §2.2 + §2.3.
**Format** : chaque candidate → verdict dur ∈ {`FAST_TRACK_NOW`, `FAST_TRACK_IF`, `DO_NOT_FAST_TRACK`} + justification < 20 lignes.

---

## 0. Méthode d'évaluation

Pour chaque candidate, 6 dimensions :

1. **Statut actuel** (grade, book, paper_days, infra_gaps).
2. **Intérêt business** (accélérer live réel ? quel book ?).
3. **Intérêt ROC** (marginal contribution, décorrélation).
4. **Complexité ops** (corrélations avec actives, supervision charge).
5. **Risque spécifique** (MC, fréquence, data deps).
6. **Verdict** contre doctrine §3 gate.

Verdict final : **FAST_TRACK_NOW** (toutes §3 vertes) / **FAST_TRACK_IF** (gate OK si prérequis listés levés) / **DO_NOT_FAST_TRACK** (au moins 1 §3 rouge non-amendable).

---

## 1. `gold_trend_mgc V1` (ibkr_futures)

### Statut actuel
- **Grade** : A (iter3-fix B2, `wf_manifest_path=data/research/wf_manifests/gold_trend_mgc_v1_2026-04-19.json`)
- **Paper start** : 2026-04-16 → earliest promotion standard 2026-05-16 → earliest **fast-track 2026-04-30** (J+14)
- **infra_gaps** : `[]` (vide)
- **WF** : 4/5 OOS windows profitables, mean Sharpe 2.625, MC P(DD>30%)=0.15%, DSR p=0.0003

### Intérêt business
- Accélération **immédiatement** impactante : trade sur gold physique via MGC (micro contract = 10 oz × $0.1/move). Décorrélé des 2 autres LIVE (CAM = cross-asset mom, GOR = spread gold/oil).
- Permet d'observer en live un **edge trend gold pur** (pas spread, pas rotation).

### Intérêt ROC
- Projection standalone : +7 à 10% annualisé (haircut inclus, cf. [roc_reporting_contract.md](roc_reporting_contract.md) §5.1).
- Décorrélation estimée avec CAM et GOR : `|corr| ~0.30–0.50` (GOR contient exposition gold mais en spread → corrélation partielle).
- **Marginal contribution portfolio** positive : trend gold isolé ≠ spread gold-oil.

### Complexité ops
- Book ibkr_futures déjà surveillé (2 LIVE actives). 1 slot fast-track supplémentaire = 3e strat simultanée. Limite `max_contracts_per_symbol=2` intacte (MGC ≠ MCL ≠ MCL-MGC leg).
- Review 2×/semaine = +10 min/semaine.
- **Attention** : si GOR signal actif simultanément → double exposition gold. Doit être géré par `pre_order_guard` check corrélation ou par règle applicative "skip si GOR open".

### Risque spécifique
- MC **exceptional** (0.15% proba DD>30%).
- Sizing minimum broker MGC = 1 contrat (§4.2). Règle §4.3 substitution : arm uniquement sur signaux ≥ 1.25σ → fréquence réduite de ~50% → ~3-5 trades en 14j.
- Risk-if-stopped par trade ~$100–130 (cohérent hard cap §4.4 $150).

### Verdict : **FAST_TRACK_NOW**

**Chaîne §3 vérifiée** :
- §3.1 : grade A ✅, manifest ✅, pas d'exempt ✅, MC<15% ✅, paper 14j atteindra 2026-04-30 ✅, `paper_pnl_net` à vérifier J+14, divergence à vérifier J+14.
- §3.2 : book live_allowed ✅, book_health GREEN (à reconfirmer J+14), runtime_audit exit 0 ✅, incidents 0 P0/P1 ✅, kill switch OFF ✅, infra_gaps = [] ✅.
- §3.3 : fréquence estimée 3-5 trades / 14j ≥ 4 ✅ (seuil §3.3 règle 14 : **à vérifier au J+14, pas a priori**), corrélation avec CAM+GOR à mesurer mais présumée < 0.60.
- §3.4 : 1 slot ibkr_futures dispo ✅, 1 slot total desk ✅, capital_at_risk projection $330 (CAM $228 + MGC $100) = 1.58% < 5% ✅.

**Confirmation go à J+14 (2026-04-30) requiert** : runtime_audit exit 0 + paper_pnl_net ≥ 0 + divergence ≤ 1σ + trades ≥ 4. Si une seule condition échoue → `DO_NOT_FAST_TRACK` ce jour-là, attendre gate standard 2026-05-16.

---

## 2. `mes_monday_long_oc` (ibkr_futures)

### Statut actuel
- **Grade** : **B** (iter3-fix, WF 3/5 OOS, MC 9.8%)
- **Paper start** : 2026-04-16 → earliest standard 2026-05-16
- **infra_gaps** : `[]` (vide)

### Intérêt business
- Accélérer un edge calendaire simple sur MES lundi open→close. Low-freq (1 trade/semaine).

### Intérêt ROC
- Projection +4-6% annualisé. Décorrélation vs CAM/GOR présumée haute (horizon intraday lundi ≠ multi-day trend).
- **Marginal contribution estimée modeste** : capital immobilisé 1 jour/semaine = ROC diluée.

### Complexité ops
- Faible en principe (1 trade/semaine).
- **Mais** : 2 fast-tracks simultanés sur ibkr_futures (avec gold_trend_mgc) viole §3.4 règle 16 "max 1 slot par book".

### Risque spécifique
- Grade B → **refusé par doctrine §3.1 règle 1** (grade S ou A uniquement).
- Fréquence 1 trade/semaine = **2 trades max en 14j** → en-dessous du seuil §3.3 règle 14 (≥ 4 trades). Zéro observation statistiquement significative.
- Si les 2 trades sont perdants consécutifs → kill §5.1 trigger → rollback automatique. Le fast-track n'aura rien apporté.

### Verdict : **DO_NOT_FAST_TRACK**

**Raisons dures** :
1. **Grade B** violé par §3.1 règle 1.
2. **Fréquence insuffisante** violée par §3.3 règle 14 (2 trades max vs 4 requis).
3. **Slot déjà pris** par gold_trend_mgc sur ibkr_futures (§3.4 règle 16).

**Recommandation** : aller en **promotion standard 30j** le 2026-05-16, comme prévu. 30j permettra d'observer ~4 trades, seuil marginal mais observable.

### Challenge explicite de la reco user initiale

> La reco user était : `gold_trend_mgc V1` + `mes_monday_long_oc`.

**Je ne suis pas d'accord sur mes_monday_long_oc**, pour les 3 raisons ci-dessus. La reco user a une logique (2 strats ibkr_futures simultanément → plus d'activité), mais en fast-track elle se retourne : 2 trades en 14j = aucune décision statistique possible. La saturation slot ibkr_futures (3 strats live simultanées) + grade B + fréquence insuffisante = un empilement de petits risques qui cumule en gros risque d'échec sans enseignement.

**Meilleure voie** : 1 seul fast-track immédiat (gold_trend_mgc), et laisser mes_monday suivre le parcours standard (promotion live_probation 2026-05-16 avec le temps de 30j pour observer 4 trades).

---

## 3. `mes_wednesday_long_oc` (ibkr_futures)

### Statut actuel
- **Grade** : B
- **Paper start** : 2026-04-16
- **infra_gaps** : `[]`
- **MC** : P(DD>30%) = **28.3%** — **limite dangereuse**

### Intérêt business
- Edge calendaire mercredi, 1 trade/semaine.

### Intérêt ROC
- Faible si MC se concrétise (DD >30% probable à 28.3%).

### Risque spécifique
- **MC 28.3% > seuil §3.1 règle 4 (15%)** → **refusé doctrine**.
- Même problème fréquence 1 trade/semaine que mes_monday.
- Grade B.

### Verdict : **DO_NOT_FAST_TRACK**

**Raisons dures** : 3 violations cumulées :
1. §3.1 règle 1 (grade B).
2. §3.1 règle 4 (MC > 15%).
3. §3.3 règle 14 (fréquence < 4 trades / 14j).

**Recommandation** : attendre MC additionnel recalculé avec plus de données (cf. [strategy_inventory_clean.md](strategy_inventory_clean.md) §2.3). Surveillance étendue 45j prévue 2026-06-01. Pas avant.

---

## 4. `mes_pre_holiday_long` (ibkr_futures)

### Statut actuel
- **Grade** : B
- **Paper start** : 2026-04-16
- **Fréquence** : 8-10 trades/**an** = ~0.02 trade/jour = **0 trade probable en 14j**

### Risque spécifique
- Grade B.
- Fréquence absolument incompatible fast-track (pas de trade observé dans la fenêtre avec haute probabilité).

### Verdict : **DO_NOT_FAST_TRACK**

**Raisons dures** :
1. §3.1 règle 1 (grade B).
2. §3.3 règle 14 (0-1 trade/14j, très au-dessous de 4).

**Commentaire** : même en promotion standard 30j, cette strat ne génère probablement **0 trade** sur la fenêtre. Sa vraie utilité = **cohorte avec mes_monday** pour augmenter fréquence combinée. Hors scope fast-track.

---

## 5. `mcl_overnight_mon_trend10` (ibkr_futures)

### Statut actuel
- **Grade** : B
- **Paper start** : 2026-04-18
- **infra_gaps** : `["friday_trigger re-WF pending"]` (non vide → violation §3.2 règle 13)

### Risque spécifique
- **Décalage signal runtime (vendredi) vs backtest (lundi) non résolu** → edge non prouvé post-fix.
- Grade B.

### Verdict : **DO_NOT_FAST_TRACK**

**Raisons dures** :
1. §3.1 règle 1 (grade B).
2. §3.2 règle 13 (infra_gaps non vide, re-WF pending).

**Recommandation** : scaffolder `scripts/research/re_wf_mcl_friday.py`, re-run WF, re-checker grade. Éventuelle re-éligibilité post re-WF, pas avant.

---

## 6. `alt_rel_strength_14_60_7` (binance_crypto)

### Statut actuel
- **Grade** : B
- **Paper start** : 2026-04-18
- **infra_gaps** : `["strat_hebdo_4_trades_max_en_30j_pas_10", "data_stale_btcusdt_alts_parquets"]` — **2 blockers cumulés**

### Intérêt business
- Seule candidate binance_crypto notable. Book 100% idle ($9,843).
- Décorrélation backtest annoncée : **-0.014** avec portfolio → forte décorrélation (si confirmée live).

### Intérêt ROC
- Projection +6-9% annualisé si validée.
- **Mais** : fréquence `strat_hebdo_4_trades_max_en_30j_pas_10` = 4 trades max en **30j**, donc **~1-2 trades en 14j** → au-dessous de 4 (§3.3 règle 14).

### Risque spécifique
- **infra_gap 1** : data BTCUSDT alts parquets stale (observed stale ~21j au 2026-04-18). Cron refresh 15 min non livré → divergence paper/backtest probable.
- **infra_gap 2** : fréquence hebdo 4/30j confirmée par WF → **structurellement inobservable en 14j**.
- Grade B.

### Verdict : **DO_NOT_FAST_TRACK**

**Raisons dures** :
1. §3.1 règle 1 (grade B).
2. §3.2 règle 13 (2 infra_gaps).
3. §3.3 règle 14 (fréquence < 4/14j structurelle).

**Note** : même avec levée data BTCUSDT alts (P1 infra), la règle fréquence reste violée. La strat **n'est structurellement pas fast-trackable**. Attendre promotion standard 2026-05-18 (+30j paper = ~3-4 trades observables).

### Correction reco initiale

Dans [strategy_inventory_clean.md](strategy_inventory_clean.md) §2.2, alt_rel_strength est listée `LIVE_PROBATION_CANDIDATE earliest 2026-05-18`. Mon verdict **DO_NOT_FAST_TRACK** ne change pas cette trajectoire : la strat reste candidate live_probation standard, mais pas fast-track. Le calendrier standard est le bon parcours.

---

## 7. `btc_asia_mes_leadlag_q80_v80_long_only` (binance_crypto)

### Statut actuel
- **Grade** : B (iter3-fix B5)
- **Paper start** : **2026-04-20** (pas démarré, date dans 1 jour au moment d'écriture)
- **infra_gaps** : `[]` (vide post iter3-fix B6 cron MES_1H_YF2Y fix)
- **WF** : variante q80 long-only du manifest `btc_asia_mes_leadlag_q70_v80_2026-04-19_backfill.json`, Sharpe backtest +1.08

### Intérêt business
- Seule autre candidate binance_crypto. Permettrait d'activer un peu de capital crypto (book 100% idle).
- Compat Binance France spot (long-only) — le q70_v80 mode=both est incompatible.

### Intérêt ROC
- Projection +3-5% annualisé (Sharpe 1.08 modeste).
- Décorrélation portfolio à confirmer.

### Complexité ops
- Book binance_crypto sans autre LIVE actuel → 1 fast-track = 1re live crypto sur le desk post bucket A drain.
- Review 2×/semaine = charge supervision ajoutée.

### Risque spécifique
- **Grade B** → §3.1 règle 1 violée.
- Paper start 2026-04-20 → **paper J+14 = 2026-05-04**. Pas avant.
- Data freshness MES_1H_YF2Y OK post B6 fix.
- Sharpe modeste 1.08 vs gold_trend_mgc 2.625 → **edge moins robuste**.

### Verdict : **DO_NOT_FAST_TRACK** (refus strict doctrine) / alternative **FAST_TRACK_IF** (assouplissement discuté)

**Raisons dures** (refus strict) :
1. §3.1 règle 1 (grade B).

**Alternative (FAST_TRACK_IF)** : si la règle grade A/S est assouplie à grade B **avec conditions renforcées** (discussion [fast_track_promotion_policy.md](fast_track_promotion_policy.md) §11.4) :
- Sharpe WF ≥ 1.0 ✅ (1.08)
- MC P(DD>30%) < 10% : **à vérifier** (non explicité dans manifest résumé)
- infra_gaps = [] ✅
- fréquence ≥ 4 trades/14j paper : **à observer** entre 2026-04-20 et 2026-05-04

**Ma position** : **PAS d'assouplissement maintenant**. Raison : bucket A drain 2026-04-19 vient d'archiver 11 strats crypto REJECTED. Ouvrir fast-track grade B sur binance_crypto juste après = risque d'un 12e rejet. Edge Sharpe 1.08 = modeste. Mieux vaut parcours standard 30j (earliest 2026-05-20) qui donnera ~6-8 trades vs 2-3 en fast-track.

**Si user insiste pour ouvrir un fast-track crypto** : condition absolue = observer entre 2026-04-20 et 2026-05-04 effectivement ≥ 4 trades paper, divergence ≤ 1σ, book_health GREEN. Et acceptation explicite de l'assouplissement grade B (doctrine §11.4 à amender).

---

## 8. Synthèse

| Strat | Book | Verdict | Raison dominante |
|---|---|---|---|
| `gold_trend_mgc V1` | ibkr_futures | **FAST_TRACK_NOW** | Grade A, MC 0.15%, 0 gaps, fréquence projetée OK |
| `mes_monday_long_oc` | ibkr_futures | **DO_NOT_FAST_TRACK** | Grade B + fréquence 2/14j + slot déjà pris |
| `mes_wednesday_long_oc` | ibkr_futures | **DO_NOT_FAST_TRACK** | MC 28.3% + grade B + fréquence 2/14j |
| `mes_pre_holiday_long` | ibkr_futures | **DO_NOT_FAST_TRACK** | Fréquence 0.02/jour + grade B |
| `mcl_overnight_mon_trend10` | ibkr_futures | **DO_NOT_FAST_TRACK** | Re-WF friday pending + grade B |
| `alt_rel_strength_14_60_7` | binance_crypto | **DO_NOT_FAST_TRACK** | 2 infra_gaps + fréquence 1-2/14j + grade B |
| `btc_asia_q80_long_only` | binance_crypto | **DO_NOT_FAST_TRACK** (strict) / **FAST_TRACK_IF** (assouplissement grade B refusé par défaut) | Grade B + paper pas démarré + Sharpe modeste |

### Bilan quantitatif

- **1 seule candidate éligible** `FAST_TRACK_NOW` selon doctrine stricte : `gold_trend_mgc V1`.
- **6 candidates refusées** pour motifs durs (grade B, fréquence, infra_gaps, MC limite).
- **0 candidate binance_crypto éligible** selon doctrine stricte. Le capital crypto reste idle.

### Décision dure finale (anti-bullshit)

**La reco user initiale "gold_trend_mgc + mes_monday" ne passe PAS la doctrine.** Seul gold_trend_mgc passe. mes_monday est refusé sur 3 motifs cumulés.

**On fast-track 1 strat maintenant, pas 2.** Le plan est dans [fast_track_launch_plan.md](fast_track_launch_plan.md).

**Capital binance reste 100% idle.** C'est acceptable tant qu'on n'a pas de vraie candidate grade A/S crypto. Ce n'est **pas** un manque — c'est une discipline. Cf. [roc_reporting_contract.md](roc_reporting_contract.md) §5.2 : "ne **PAS** donner plus de capital pour remplir occupancy".

---

## 9. Ce qui changerait le verdict

### Pour gold_trend_mgc à J+14 (2026-04-30)

Si à cette date l'un de ces signaux apparaît, le verdict `FAST_TRACK_NOW` **devient `DO_NOT_FAST_TRACK` ce jour-là** :
- Paper_pnl_net < 0
- Divergence > 1σ
- Moins de 4 trades paper sur 14j
- runtime_audit exit ≠ 0
- Incident P0/P1 ouvert
- GOR signal actif simultané non géré

→ Alors parcours standard 2026-05-16.

### Pour btc_asia_q80_long_only à 2026-05-04 (J+14 paper)

Si user accepte assouplissement grade B (§11.4) ET :
- Paper 2026-04-20 → 2026-05-04 génère ≥ 4 trades
- Divergence ≤ 1σ
- `MC P(DD>30%) < 10%` confirmé manifesté
- Sharpe tenu

→ Candidate requalifiable `FAST_TRACK_IF` → `FAST_TRACK_NOW` à 2026-05-04. Sinon parcours standard 2026-05-20.

### Pour les 5 autres

**Parcours standard uniquement.** Pas d'assouplissement fast-track envisageable pour grade B + infra_gaps + fréquence structurelle.

---

**Fin review.** Plan concret d'arming pour `gold_trend_mgc V1` dans [fast_track_launch_plan.md](fast_track_launch_plan.md).
