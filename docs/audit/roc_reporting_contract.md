# ROC Reporting Contract — H7 T6

**As of** : 2026-04-19T16:15Z
**Phase** : H7 TODO XXL hygiene. Allocation decisionnelle, pas reporting financier cosmetique.
**Livrable** : ce document. Contrat de mesure + regles d'allocation.
**Couplage** : consomme `live_pnl_tracker.py` (PRIMAIRE PnL, T5 matrix) + state files P0 (T4 contracts) + registries canoniques (T3 truth map).

---

## 0. Principe directeur T6

> **Empecher 2 derives business fondamentales** :
> 1. Confondre **capital occupe** avec **capital bien utilise**
> 2. Confondre **PnL brut** avec **ROC utile au portefeuille**
>
> Le contrat doit etre un **outil de decision allocation**, pas un reporting financier cosmetique.
> On ne remplit pas le capital pour "faire occupancy". On remplit avec **edge prouve + decorrelation**.

**Anti-principe** : metriques gadgets. Si une metrique ne pilote pas une decision d'allocation, elle n'est pas dans ce contrat.

---

## 1. Definitions canoniques — 5 niveaux capital

Distinction **stricte** a tous les niveaux. Chaque niveau differe du suivant.

| Niveau | Definition | Source canonique | Exemple 2026-04-19 |
|---|---|---|---|
| **capital_allocated** | Budget cible par book dans `config/books_registry.yaml.capital_budget_usd`. **Declaratif PR**. | `books_registry.yaml` | ibkr_futures: $10K, binance_crypto: $10K, alpaca_us: $100K paper |
| **capital_deployable** | Equity live broker accessible pour trader. Dynamique. | `data/state/{book}/equity_state.json` (VPS) via broker API | ibkr_futures: $11,013, binance_crypto: $9,843 |
| **capital_used** | Notional positions ouvertes. | `data/state/{book}/positions_live.json` | MCL 1 contrat = $7,881 notional |
| **capital_at_risk** | Risk-if-stopped (entry - SL) × multiplier par position. **Vraie mesure futures**. | calcul depuis positions_live + SL | MCL : ($75.85 - $73.57) × 100 = $228 |
| **capital_idle** | `capital_deployable - capital_at_risk` | derive | $20,856 - $228 = **$20,628** |

### Pieges semantiques a eviter

- **NE PAS** confondre `capital_used` (notional) et `capital_at_risk` (risk-if-stopped). Futures = levier → notional >> risk.
- **NE PAS** utiliser `capital_allocated` pour juger occupation reelle. Le budget n'est pas l'utilisation.
- **NE PAS** compter `earn_total_usd` Binance (passif) comme `capital_at_risk`. Earn = idle-equivalent.

### Cascade a tous les niveaux

```
capital_allocated (doctrine)
  >= capital_deployable (observe broker)
    >= capital_used (notional ouvert)
      >= capital_at_risk (risk-if-stopped)
      
capital_idle = capital_deployable - capital_at_risk
```

Pour futures, **capital_at_risk << capital_used** (levier).
Pour spot, **capital_at_risk ≈ capital_used** (pas de levier).

---

## 2. Metriques ROC et occupancy — formules + sources

### 2.1 ROC par strategie (period T)

```
ROC_strategy[T] = (PnL_realise[T] + PnL_unrealized_delta[T]) / capital_allocated_strategy[T]
```

**Sources autorisees** :
- `PnL_realise[T]` : `data/live_pnl/daily_pnl.jsonl` (agregation par strategy_id + period)
- `PnL_unrealized_delta[T]` : snapshot positions_live.json debut/fin T
- `capital_allocated_strategy[T]` : `config/allocation.yaml` ou equivalent. **Si absent** : capital_deployable book / N_strats_active (approx acceptable)

**Fenetres canoniques** : 30d rolling, 90d rolling, YTD, since-live (since_live_start_at).

**Seuil decision** :
- ROC_strategy 30d < -5% → flag pour review / demote candidate
- ROC_strategy 90d < +2% annualise → candidate demote si occupe capital
- ROC_strategy > +10% annualise 90d → candidate scale-up

### 2.2 ROC par book

```
ROC_book[T] = sum(ROC_strategy[T] × weight_strat) pour strats du book
```

Ou plus simplement (et robuste) :
```
ROC_book[T] = (equity_fin[T] - equity_debut[T]) / equity_debut[T]
```

Cela inclut frais, slippage, fills partiels. Plus honnete.

### 2.3 Occupancy par book

```
occupancy_book[T] = avg(capital_at_risk_book / capital_deployable_book) sur fenetre T
```

Fenetres : 7d / 30d glissant. Pas de moyenne arithmetique sur jour-par-jour — moyenne time-weighted.

**Seuils decisionnels** :
- occupancy < 5% durable 14j → **P1 flag** : trop idle, capital mal utilise
- occupancy > 80% durable 7j → **P1 flag** : risque concentration
- occupancy stable 15-40% → **normal** pour desk diversifie

### 2.4 Contribution marginale

```
marginal_contribution_strategy = ROC_portfolio_avec_strat - ROC_portfolio_sans_strat
```

**Ou approximation** (plus pratique) :
```
marginal_contribution = ROC_strategy × corr_factor
corr_factor = 1 - abs(corr(returns_strategy, returns_portfolio))
```

Une strat avec `corr = -0.01` (alt_rel_strength backtest) a `corr_factor ≈ 1.0` → contribution quasi-pure.
Une strat avec `corr = 0.80` a `corr_factor ≈ 0.20` → contribution fortement diluee.

**Seuil decision** :
- `marginal_contribution < 0.5%` annualise → strat redondante, ne merite pas capital supplementaire
- `marginal_contribution > 3%` annualise + `corr_factor > 0.5` → scale-up candidate

### 2.5 Metriques a NE PAS tracker (gadgets)

**Bannies du contrat** (ne pilotent aucune decision) :
- Nombre de trades total (frequency n'est pas edge)
- Win rate sans PnL moyen (WR 90% avec avg loss >> avg win = trap)
- Sharpe ratio brut sans drawdown context (Sharpe 3.0 avec MaxDD 40% = fragile)
- PnL brut en $ absolu sans reference capital
- "days since last trade" sauf si > 60j (signal dead strategy)

**Tolerees pour audit seul** (pas allocation) :
- Volume trade (info ops)
- Latence execution (info ops)
- Nombre orders rejetees (info ops)

---

## 3. Frequences de calcul — contrat de mesure

| Metrique | Frequence calcul | Consumer | Autorite |
|---|---|---|---|
| `capital_deployable` par book | daily (a 22:00 UTC cron) | live_pnl_tracker + dashboard | `live_pnl_tracker.py` |
| `capital_used` | realtime (positions snapshot) | dashboard | `positions_live.json` |
| `capital_at_risk` | realtime | pre_order_guard, dashboard | calcul inline |
| `capital_idle` | daily | reporting hebdo | derive |
| `ROC_strategy` 30d | daily rolling | reporting + promotion decisions | `live_pnl_tracker` agg |
| `ROC_strategy` 90d | daily rolling | scaling decisions | idem |
| `ROC_strategy` since_live | daily | historique | idem |
| `ROC_book` 30d / 90d | daily rolling | book allocation decisions | idem |
| `occupancy_book` 7d / 30d | daily | flag under-/over-utilisation | **NON IMPLEMENTE** — script backlog |
| `marginal_contribution` | weekly (dimanche soir) | scaling decisions | **NON IMPLEMENTE** — P2 backlog |

**Regle** : toute metrique de ce tableau doit etre **automatiquement produite sur VPS** (cron). Pas de calcul manuel Excel.

---

## 4. Sources autorisees (qui ecrit / qui lit)

| Metrique | Producteur autorite | Fichier canonique | Consommateurs |
|---|---|---|---|
| `capital_deployable` | `live_pnl_tracker.py` (daily 22:00 UTC) | `data/live_pnl/daily_equity.csv` | dashboard + reporting |
| `capital_used` + `capital_at_risk` | runtime observation | `data/state/{book}/positions_live.json` | pre_order_guard + dashboard |
| `PnL_realise` | broker fills → journal | `data/tax/classified_trades.jsonl` + `data/live_pnl/daily_pnl.jsonl` | live_pnl_tracker + post_trade_check |
| `PnL_unrealized` | broker API snapshot | `positions_live.json` (marketPrice + unrealizedPNL) | live_pnl_tracker |
| `ROC_strategy` | live_pnl_tracker aggregation | `data/live_pnl/summary.json` (par strat si > 10 trades) | reporting |
| `occupancy_book` | **script a scaffold** `scripts/capital_occupancy_report.py` | `data/live_pnl/occupancy_{book}.jsonl` | dashboard + scaling |
| `marginal_contribution` | **script a scaffold** `scripts/marginal_contribution.py` | `data/live_pnl/marginal.jsonl` weekly | scaling |

**Regle** : une metrique sans source canonique est **interdite** dans les docs de pilotage. On flag `[metric non sourced]` et on demande l'implementation.

---

## 5. Regles de decision d'allocation — 3 sections explicites

### 5.1 Sleeves qui AUGMENTENT le ROC (merite capital supplementaire)

**Critere** : haut `marginal_contribution` + faible `corr_factor` au portefeuille existant + edge robuste bull/bear.

Candidates 2026-04-19 :

| Strat | Grade | Justification | Capital supp recommande |
|---|---|---|---|
| **gold_trend_mgc V1** | A (iter3-fix B2) | Sharpe OOS 2.625, MC P(DD>30%)=0.15%, decorrelation acceptable CAM/GOR | $500-1000 risk-if-stopped post 2026-05-16 promotion |
| **alt_rel_strength_14_60_7** | B | Sharpe +1.11, corr portfolio -0.014 (forte decorrelation), bull+bear robuste | $3K gross post 2026-05-18 |
| **mib_estx50_spread** (conditional) | S | Sharpe 3.91 backtest EU indices, decorrele US | EUR 13.5K IF funding +EUR 3.6K user |
| **btc_asia_mes_leadlag_q80_long_only** | B (iter3-fix B5) | Compat Binance FR spot, Sharpe +1.08 | $3K gross apres 2026-05-20 30j paper |

**Regle** : ces strats passent par promotion_gate + 30j paper. Scaling **apres** preuve live > 60j (feedback_prove_profitability_first).

### 5.2 Sleeves qui AUGMENTENT l'occupation sans suffisamment d'edge

**Critere** : occupe capital mais `marginal_contribution < 0.5%` annualise OR faible grade OR fragile MC.

Sleeves a risque :

| Strat | Risque | Action |
|---|---|---|
| **mes_wednesday_long_oc** | MC P(DD>30%)=**28.3%** limite | Surveillance 45j (pas 30j), promotion seulement si MC additionnel recalcule < 15% |
| **mes_pre_holiday_long** | Trade rare 8-10/an = 0.02/jour | **Inutile seul** (occupe slot 1 contrat). OK en **cohorte** avec mes_monday pour freq. Ne pas promouvoir en isole. |
| **btc_asia_mes_leadlag_q70_v80** (mode=both) | Incompat Binance France spot | **Conserver paper** seulement. Pas de capital alloue. Seule q80_long_only variante merite live. |
| **mcl_overnight_mon_trend10** | friday_trigger re-WF pending | **Bloque jusqu'a re-WF**. Si promote sans re-WF = edge non prouve. |
| **eu_relmom_40_3** | Shorts EU indices CFD ou mini futures sans plan concret | **Bloque** tant que plan short non implemente. |

**Regle** : ne **PAS** donner plus de capital pour "remplir occupancy". Attendre que les blockers edge soient leves.

### 5.3 Sleeves qui doivent RESTER a 0 allocation

**Critere** : REJECTED, DISABLED, ou structurellement incompatible.

| Strat | Raison | Action |
|---|---|---|
| `btc_dominance_rotation_v2` | grade REJECTED, logic broken | **JAMAIS promouvoir**. Archived_rejected list |
| `fx_carry_momentum_filter` | ESMA EU leverage limits reglementaire | **JAMAIS tant qu'ESMA inchangee** |
| Les 15 strats `archived_rejected` | Post bucket A + C drains | **JAMAIS** sans nouveau WF VALIDATED complet |
| `us_stocks_daily` | meta-portfolio sans WF unique + PDT waiver requis | **Paper uniquement** tant que Alpaca gate NO_GO |
| `us_sector_ls_40_5` | Shorts sectors + PDT + re-WF ETF pending | **Paper uniquement** tant que re-WF ETF + PDT waiver (gate GO_25K) |

**Regle** : aucune promotion par exception au mandat. Feedback user : "ne pas reactiver une strategie rejetee sans nouvelle preuve machine-readable".

---

## 6. Ce que le book merite comme prochain euro

### Ordre de priorite funding (as_of 2026-04-19)

| Rang | Action funding | Conditions | Upside estime annualise | Downside |
|---|---|---|---|---|
| **1** | +EUR 3.6K IBKR → mib_estx50_spread | Grade S confirmed + capital EUR 13.5K margin | +$1,750 (haircut -50%) | Capital immobilise EUR 13.5K |
| **2** | Scale alt_rel_strength live (+ $2K gross) | Post 30j paper + gate VALIDATED | +$300-600 | Risk -$980 (kill_criteria) |
| **3** | +$25K Alpaca (depot PDT waiver) | Gate GO_25K (earliest 2026-05-18) | +$300-800 | $25K immobilise, PDT waived |
| **4** | gold_trend_mgc V1 → live (1 contrat MGC) | Post 30j paper + WF V1 stable | +$180 | Risk $500 |
| **5** | mes_monday live (1 contrat MES) | Post 30j paper | +$110 | Risk $300 |

**Regle** : scaling seulement **apres** preuve machine-readable (30j paper minimum + 0 divergence). Pas d'exception au `promotion_gate`.

### Books qui ne doivent PAS etre remplis artificiellement

- **alpaca_us** : $99K paper disponible n'est pas un manque d'edge. Ne depenser les $25K que si gate GO_25K (pas avant).
- **ibkr_fx** : ESMA bloque. Zero capital.
- **binance_crypto** : earn_total $8.87K actuellement idle/passif. Tentation d'activer borrow pour leverage = **interdit** tant que alt_rel_strength pas promue.

---

## 7. DoD — 6 questions user (reponses < 2 min)

### Q1 : Combien de capital est idle maintenant ?

**$20,628** sur $20,856 deployable = **98.9% idle**.

Detail :
- `capital_at_risk` = $228 (MCL 1 contrat SL 73.57)
- `capital_idle` = $20,856 - $228 = **$20,628**
- IBKR futures : $11,013 - $228 = $10,785 idle (97.9%)
- Binance crypto : $9,843 - $0 = $9,843 idle (100%)
- Alpaca paper : $99K non comptabilise (paper)

Source : `data/state/ibkr_futures/equity_state.json` + `positions_live.json` VPS.

### Q2 : Quelles strategies utilisent vraiment du capital ?

**1 seule** : `cross_asset_momentum` via la position MCL ouverte ($228 risk).
Toutes les autres strats :
- 14 strats paper/READY : **0 capital utilise**
- 1 strat ACTIVE (gold_oil_rotation) mais signal dormant : 0 capital utilise actuellement

Source : `positions_live.json` VPS.

### Q3 : Lesquelles apportent du ROC ?

Mesure honnete sur 13j (echantillon non significatif) :
- **cross_asset_momentum** : +$295 unrealized MCL = +2.68% return book 13j = estimateur naif +75% annualise brut (statistiquement non significatif avec n=1)
- **gold_oil_rotation** : 0% (signal dormant)
- Autres : 0% (paper ou non live)

**Conclusion honnete** : **echantillon trop petit** pour attribuer ROC. Besoin >= 30 trades par strat minimum.

### Q4 : Lesquelles apportent juste de l'occupation ?

**Aucune** actuellement (seule 1 strat a du capital, CAM). Mais **si on promeut sans edge** :
- Promotion mes_pre_holiday seul (8-10 trades/an) = occupation 1 contrat slot pour trade rare
- Promotion mes_wednesday sans MC additionnel (P(DD>30%)=28.3%) = occupation fragile
- Reactivation btc_asia q70 mode both (incompat FR) = occupation impossible techniquement

**Regle preventive** (section 5.2) applied.

### Q5 : Quel book merite le prochain euro de capital ?

**Ordre (section 6)** :
1. `ibkr_eu` via mib_estx50 IF user funde +EUR 3.6K → +$1,750/an estimated
2. `binance_crypto` via alt_rel_strength probation post 2026-05-18 → +$300-600/an
3. `alpaca_us` via PDT waiver post gate GO_25K → +$300-800/an

### Q6 : Quel book ne doit surtout pas etre rempli artificiellement ?

- **`ibkr_fx`** : ESMA bloque, **zero capital** justifie.
- **`binance_crypto` avec earn passif** : tentation d'activer margin borrow sans strat promouvable = **interdit**.
- **`alpaca_us` sans gate** : ne pas depenser $25K avant gate GO_25K.
- **`ibkr_futures` au-dela de 4 contrats** simultanes : cf `limits_live.yaml#futures_limits.max_contracts_per_symbol=2` + global 4.

---

## 8. Gaps implementation critiques (backlog ordered)

### P1 — scripts metriques absents (bloquent la decision allocation)

| Script a scaffolder | Consumer | Fenetre cible |
|---|---|---|
| **`scripts/capital_occupancy_report.py`** | dashboard + reporting hebdo | Avant fin mai 2026 (avant 2e promotion) |
| **`scripts/roc_per_strategy.py`** (agregation live_pnl par strat) | scaling decisions | Avant 90j live |
| **`scripts/marginal_contribution.py`** | scaling decisions Phase 2 | Avant 5 strats live |

**Impact si non livres** : on peut **pas mesurer** occupancy ni ROC par strat. Allocation decisions se basent sur backtest + instinct → risque **biais narratif**.

### P2 — tests + contrats

- `tests/test_live_pnl_tracker.py` existe mais couvre peu (T2 finding).
- Pas de test `tests/test_capital_occupancy_report.py` (script n'existe pas).

### P3 — docs / dashboards

- Dashboard D3 n'a pas widget occupancy.
- Pas de weekly report auto (script `weekly_truth_review.py` absent T5 finding).

---

## 9. Anti-metriques (bannies du contrat)

Recapitulatif section 2.5 :
- PnL brut $ absolu seul (sans reference capital)
- Sharpe brut (sans drawdown context)
- Win rate sans avg PnL
- Nombre trades total (frequency ≠ edge)
- Occupancy brute sans contribution marginale

**Regle** : tout doc de pilotage qui cite l'une de ces metriques seule est flagge `narrative drift`.

---

## 10. Ligne rouge T6 respectee

- ✅ Allocation decisionnelle, pas reporting cosmetique
- ✅ 5 niveaux capital distingues strictement (allocated ≠ deployable ≠ used ≠ at_risk ≠ idle)
- ✅ ROC et PnL distingues (brut vs pondere allocation)
- ✅ Sections explicites : ROC-contributive / occupation-seule / zero-allocation (section 5)
- ✅ Decision funding ordonnee (section 6)
- ✅ DoD 6 questions user repondues (section 7)
- ✅ Anti-metriques bannies (section 9)
- ✅ Gaps implementation P1 scripts listes (section 8)
- ✅ Pas de metriques gadgets

**Prochain** : T7 H4 strategy inventory clean. Consolidation post T1-T6 nettoyages. Synthese canonique 16 strats + 15 archived.
