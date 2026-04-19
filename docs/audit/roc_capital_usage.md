# ROC & Capital Usage — Mesure + Cible

**As of** : 2026-04-19T14:33Z
**But** : rendre lisible le capital alloue vs deployable vs utilise vs a-risque + ROC par strat, **honnetement** post iter3-fix.

---

## 1. Taxonomie capital (4 niveaux distincts)

| Niveau | Definition | Valeur 2026-04-19 | source_of_truth |
|---|---|---|---|
| **Capital alloue** | Budget cible par book dans `books_registry.yaml` | $10K ibkr_futures + $10K binance + $100K alpaca paper | config/books_registry.yaml |
| **Capital deployable** | Equity live broker accessible pour trade | $11,013 IBKR + $9,843 Binance = **$20,856** live. $99,495 Alpaca paper. | broker API via worker authenticate log VPS |
| **Capital utilise** | Positions ouvertes notional | $7,881 (MCL 1) | `data/state/ibkr_futures/positions_live.json` VPS |
| **Capital a risque** | Risk-if-stopped sum positions | **$228** (MCL entry-SL = $75.85 - $73.57 × 100) | calcul positions_live.json |

**Ratios** :
- utilise / deployable = $7,881 / $20,856 = **37.8% brut** (mais single position, concentre MCL)
- a-risque / deployable = $228 / $20,856 = **1.09%**

Nuance critique : notional $7.9K suggere occupation brute 37%, mais risk-if-stopped = 1.09% = **vraie mesure du capital engage**. Ceci reflete la nature futures : effet de levier important sur notional sans capital equivalent a risque.

---

## 2. ROC contribution observee (30 derniers jours)

### Source : `data/tax/classified_trades.jsonl` VPS + positions live.

| Strat | Book | Trades 30j fermes | PnL realise | PnL unrealized | source_of_truth |
|---|---|---|---|---|---|
| cross_asset_momentum | ibkr_futures | 0 ferme (1 ouvert MCL) | $0 | **+$295.23** | positions_live.json + classified_trades |
| gold_oil_rotation | ibkr_futures | 0 | $0 | $0 | - |
| STRAT-001 (legacy BTCUSDC) | binance_crypto | 2 crypto<->stable (04-16, 04-17) | $0 (non taxable) | $0 | classified_trades.jsonl |
| STRAT-015 (legacy) | binance_crypto | 1 (2026-04-08) | $0 | $0 | classified_trades.jsonl |
| **TOTAL** | — | **4 trades** | **$0 realise** | **+$295** | |

**ROC annualise observe (sur 13 jours echantillon, n=1 position signifiant pas concluant)** :
- IBKR futures : +$295 / $11,013 = +2.68% en 13j = **+75% annualise brut** (1 trade seul, non significatif statistiquement)
- Binance live : 0%
- **Global live** : +$295 / $20,856 = +1.41% sur 13j

**Caveat** : fenêtre trop courte, 1 position seule, non significatif statistiquement. Nombre de trades minimum pour inference ROC fiable : >= 30-50 trades par strat.

---

## 3. ROC realiste a 20K — scenario conservative

### Hypothese : **-50% haircut vs backtest** (transaction costs reels, slippage, overfitting partiel).

| Source | Capital alloue | Sharpe backtest | Return annualise backtest | Return annualise haircut | Contribution $/an |
|---|---|---|---|---|---|
| CAM + GOR (live_core) | $7K risk-budget | 0.85 blended portfolio | 12-15% | 6-7.5% | **+$490** |
| mes_monday_long_oc (post probation 2026-05-16) | $1K (1 contrat) | 0.9 | 8-12% | 4-6% | **+$55** |
| gold_trend_mgc V1 (post probation 2026-05-16) | $1K | 2.625 OOS (V1 iter3-fix) | 15-20% | 7-10% | **+$90** |
| alt_rel_strength (post probation 2026-05-18) | $3K gross | 1.11 | 12-18% | 6-9% | **+$225** |
| mcl_overnight_fri (post re-WF, earliest 2026-05-30) | $0.5K | 0.80 | 8-12% | 4-6% | **+$25** |
| mib_estx50 (SI funding +EUR 3.6K) | EUR 13.5K (~$14K) | 3.91 (haircut fort 2x) | 20-30% | 10-15% | **+$1,750** (si funde) |
| **TOTAL conservative sans funding** | ~$12K live | — | — | — | **+$885/an** |
| **Return sur $20K portfolio sans funding** | — | — | — | — | **+4.4%/an** |
| **TOTAL conservative AVEC funding mib_estx50** | ~$26K | — | — | — | **+$2,635/an** |
| **Return sur ~$24K portfolio post-funding** | — | — | — | — | **+10.9%/an** |

### Hypothese optimiste (haircut -25% vs backtest, best case)
Memes lignes, haircut reduit. Total sans funding = +$1,550/an = +7.75%. Avec funding = +$4,600/an = +19.1%.

---

## 4. ROC cible 30% (directive user) — atteignable ou non ?

**Cible** : +30% annualise sur $20K = +$6,000/an.

**Conditions necessaires** :
- Funding mib_estx50 (+EUR 3.6K) OU capital Binance scale > $15K.
- Haircut optimiste (-25% vs backtest) confirme par 90j+ de live probation.
- 5-6 strats live diversifiees concurremment.
- Sharpe portfolio blended >= 1.5 (exigeant).

**Atteignable** :
- Avec mib_estx50 + optimiste : **+19%/an** ($3,800) → pas 30% mais acceptable.
- Pour 30% : besoin d'**une** strat haut-Sharpe additionnelle grade A/S non encore identifiee, OU scaling Binance a $20K.

**Non atteignable a $20K sans** :
- `mib_estx50` funde
- Nouvelle strat grade A/S non actuellement READY

**Recommendation honnete** :
- **Cible M3 realiste** : **+10-15% annualise sur $20K** (scenario conservative avec funding) — **ambitieux mais credible**.
- **Cible 30%** : post-trajectory 60K equity ou decouverte strat A/S additionnelle.

---

## 5. ROC cible a 100K — trajectoire honnete

**100K** = $20K actuel + $80K injection capital + reinvestissement ROC cumule.

### Etape 1 : 20K -> 30K (ROC + performance reel, 6-18 mois)
- 4-5 strats live diversifiees.
- +10-15% ROC annualise = +$2-3K/an.
- Build-up capital via reinvestissement profits sur 12-24 mois.
- Sans injection supplementaire.

### Etape 2 : 30K -> 60K (injection + Alpaca PDT)
- Dep Alpaca +$25K pour PDT waiver.
- Activation us_sector_ls + us_stocks_daily (apres 30j paper + gate GO).
- Books : IBKR futures + Binance + Alpaca + ibkr_eu optionnel.

### Etape 3 : 60K -> 100K (scaling + redondance ops)
- Augmentation sizing sur strats grade A+.
- Envisager 2e VPS redondant a partir de $75K (PAS avant).
- Alerting multi-canal (Slack + SMS fallback).
- Post-mortem hebdo + monitoring renforce.

### Conditions de go a chaque etape
- ROC observe >= 10% annualise sur **6 mois minimum** (pas 1 trade isole).
- Max drawdown < 15% sur 12 mois.
- 0 incident P0 sur 3 mois.
- Decision user explicite.

**Principe directeur** : scaler **seulement apres preuve machine-readable** (feedback_prove_profitability_first).

---

## 6. Sleeves qui augmentent le ROC vs sleeves qui augmentent juste l'occupation

### Augmentent le ROC (Sharpe + decorrelation)

| Strat | Pourquoi | Impact ROC attendu |
|---|---|---|
| **gold_trend_mgc V1** (iter3-fix B2) | Sharpe 2.625 OOS, MC P(DD>30%)=0.15%, decorrelation CAM/GOR acceptable | HAUT (+7-10%) |
| **alt_rel_strength_14_60_7** | Corr portfolio -0.014, bull+bear robuste | MOYEN (+6-9%) |
| **mib_estx50** (si funde) | Sharpe 3.91 backtest, EU indices decorrele US | TRES HAUT (+10-15%) |

### Augmentent occupation mais edge marginal (a surveiller)

| Strat | Pourquoi | Risque |
|---|---|---|
| **mes_wednesday_long_oc** | MC P(DD>30%)=28.3% limite | Surveiller 45j vs 30j |
| **mes_pre_holiday_long** | Trade rare 8-10/an seul | Inutile seul, OK en cohorte |
| **mcl_overnight_mon_trend10** | Friday trigger re-WF pending | Non promouvable sans re-WF |

### Ne PAS promouvoir pour l'occupation seule

- **btc_dominance_rotation_v2** : REJECTED, ne pas reactiver.
- **us_stocks_daily** : meta-portfolio, pas de WF unique, bloque par PDT.
- Toute strat sans manifest physique grade >= B.

---

## 7. Metriques manquantes a implementer

| Metrique | Priorite | Status | Impact business |
|---|---|---|---|
| Capital occupancy tracker (timeseries par strat) | P1 | **NON IMPLEMENTE** | Sans cette metrique, impossible mesurer KPI "occupancy" du mandat user |
| ROC par strat (30j/90j/365j glissant) | P1 | **NON IMPLEMENTE** | Impossible attribuer ROC individuel |
| Marginal contribution analyzer (corr portfolio) | P2 | **NON IMPLEMENTE** | Impossible justifier promotion diversification |
| Dashboard widget occupancy | P2 | **NON IMPLEMENTE** | Visibility reduite |
| Weekly report auto (Telegram + email) | P3 | **NON IMPLEMENTE** | Manuel hebdo |

**Gap** : 5 items non livres. Score "observabilite ROC" est **4.0 / 10** (non modifie iter3-fix).

---

## 8. Allocation cible 20K (decision user)

### V1 conservative (pre-mib_estx50, post iter3-fix)

| Book | Strat | Risk max | Max gross | Commentaire |
|---|---|---|---|---|
| ibkr_futures | CAM | $500 (5%) | $10K BP | Moteur principal, `ready now` |
| ibkr_futures | GOR | $500 (5%) | $10K BP | Complement decorrele, `ready now` |
| ibkr_futures | mes_monday (post 2026-05-16) | $300 | 1 contrat MES | `blocked by paper time` |
| ibkr_futures | gold_trend_mgc V1 (post 2026-05-16) | $500 | 1 contrat MGC | `blocked by paper time` (WF V1 iter3-fix B2 livre) |
| ibkr_futures | **cap global** | **max $1,500 risk simultane** | **max 4 contrats** | cf `config/limits_live.yaml` |
| binance_crypto | alt_rel_strength | $980 (10% DD abs) | $3K gross | Candidate #1 probation 2026-05-18 |
| binance_crypto | btc_asia q80_long_only (B5 iter3-fix) | $800 | $5K notional | Candidate #2, earliest 2026-05-20 |
| binance_crypto | **cap global** | **max $1,800 risk** | **max $8K gross** | |
| alpaca_us | **paper-only** | 0 | 0 | Waive PDT requis, voir `alpaca_go_25k_rule.md` |
| ibkr_eu | **paper-only** | 0 | 0 | mib_estx50 attend funding EUR 3.6K |
| **PORTFOLIO TOTAL** | — | **max $3,300 risk** (16.5% equity) | ~$25K gross max | Leverage brut acceptable |

### V2 si funding +EUR 3.6K mib_estx50
- mib_estx50_spread : EUR 13.5K margin (60-70% equity book ibkr_eu). Kill DD EUR 14K.
- Book ibkr_eu passe paper_only -> live_allowed (decision + whitelist-aware runner a dev).

### V3 si PDT waiver Alpaca + $25K deposit (gate GO_25K obtenu)
- us_sector_ls_40_5 live_probation apres gate + 30j paper + re-WF ETF.
- Capital Alpaca live $25K dedicated.
- Portfolio ~$45K equity 3 brokers.

---

## 9. Diagnostic score ROC / capital usage

| Dimension | Note (post iter3-fix) | Justification |
|---|---|---|
| Capital deploye lisible | 7.0 | Registries canoniques OK, live_pnl_tracker 1j data insuffisant |
| Occupancy observee | 3.0 | 1.09% actuellement. Gap massif vs cible 15-30%. Inchange iter3. |
| ROC mesurable par strat | 3.5 | Pas de script metric par strat 30j/90j. Inchange iter3. |
| Contribution marginale | 2.0 | Pas d'analyse corr portfolio par strat live. Inchange iter3. |
| Alignement capital cible / reel | 5.5 | Allocation propose dans V1, baselines post-drain alignees. |

**Note ROC / capital usage** : **4.0 / 10** (inchange vs iter3 initial — iter3-fix n'a pas livre de metriques nouvelles, juste du code gov et 2 strats additionnelles).

**Cause principale** : plateforme solide gouvernance mais **trop peu de strats actives** -> capital idle. Resolution : **promotion 3-5 nouvelles strats sur 4-8 semaines**. Pas par refactor technique.

**Accord avec directive user** : ne **PAS** augmenter le capital a risque juste pour l'occupation. Promouvoir vraies strats, mesurees, gate-validated. 30% est ambitieux mais crédible uniquement avec mib_estx50 funde + paper 30j validation.
