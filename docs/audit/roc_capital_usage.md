# ROC & Capital Usage — Mesure + Cible

**Date** : 2026-04-19
**But** : rendre lisible le capital alloue vs utilise vs contributeur au ROC. Diagnostic honnete.

---

## 1. Capital actuel (snapshot 2026-04-19)

### Disponible vs en-risque

| Book | Equity total | Cash/spot dispo | Position live (notional) | Risk-if-stopped | % book at risk |
|---|---|---|---|---|---|
| ibkr_futures | $11,012.79 | $58,073.75 buying power | $7,881 (MCL 1) | $228 (stop -3.0%) | **2.07%** |
| binance_crypto | $9,843 | $1,000 spot + $8,865 earn | 0 | 0 | **0.00%** |
| alpaca_us (paper) | $99,495 | $397,981 BP | 0 live | 0 | 0 |
| ibkr_eu | n/a (paper-only) | — | — | — | — |
| ibkr_fx | 0 (disabled) | — | — | — | — |
| **TOTAL live** | **$20,856** | **$11,000 cash dispo** | **$7,881** | **$228** | **1.09%** |

### Diagnostic
- **Capital live deployable** : $20,856.
- **Capital en-risque actuellement** : $228 (1.09%).
- **Capital idle (cash + earn passif Binance)** : **$19,628 (94.1%)**.

**VERDICT** : platform under-utilisee. 1 seule position live sur 2 strats live_core actives. Le book binance_crypto est 100% idle (earn passif $8.87K + $1K cash sans strat active).

---

## 2. ROC contribution observee (30 derniers jours)

### Source : `data/tax/classified_trades.jsonl` + `data/state/ibkr_futures/positions_live.json`.

| Strat | Book | Trades 30j | PnL realise | PnL unrealized | Notes |
|---|---|---|---|---|---|
| cross_asset_momentum | ibkr_futures | 1 entree (MCL 2026-04-17) | 0 | **+$295.23** | Position ouverte, en profit |
| gold_oil_rotation | ibkr_futures | 0 (signal dormant) | 0 | 0 | Attente momentum spread |
| STRAT-001 (legacy) | binance_crypto | 2 trades (2026-04-16, 04-17) | 0 (crypto non taxable FR) | 0 | Legacy pre-drain, BTC<->USDC |
| STRAT-015 (legacy) | binance_crypto | 1 trade (2026-04-08) | 0 | 0 | Legacy pre-drain |
| **TOTAL** | — | **4 trades** | **$0** | **+$295** | |

**ROC annualise observe (sur periode 13j)** :
- IBKR futures : +$295 / $11,013 = +2.68% (13j) = **+75% annualise brut** (signal unique, trop court pour conclure).
- Binance : 0% (pas de strat active).
- Global : +1.41% sur $20,856 en 13j.

**Caveat** : PnL unrealized non-capture. Signal unique pas representatif. Pour significant ROC, besoin de >= 30-60 trades minimum.

---

## 3. Cible ROC 30% annualise (directive user)

### Decomposition honnete

Pour atteindre **30% portfolio annualise sur $20K** = **+$6,000/an** = **+$500/mois moyenne** :

#### Scenario conservative (hypothese backtest -50% haircut)

| Source | Capital alloue | Sharpe attendu | Return annualise | Contribution $/an |
|---|---|---|---|---|
| CAM + GOR (live_core) | $7K | 0.85 portfolio | 12-15% | +$1,050 |
| mes_monday post probation | $1K (1 contrat) | 0.9 | 8-12% | +$110 |
| gold_trend_mgc V1 post WF | $1K | 1.5 | 15-20% | +$180 |
| alt_rel_strength live_probation | $3K gross | 1.1 | 12-18% | +$450 |
| mcl_overnight_fri re-WF | $0.5K | 0.8 | 8-12% | +$50 |
| mib_estx50 (si EUR 3.6K fund) | $EUR 13.5K (~$14K) | 3.9 backtest / 2.0 haircut | 20-30% | +$3,500 |
| **TOTAL conservative** | **~$27K** | — | — | **+$5,340/an** |
| **Return sur $20K portfolio** | — | — | **+26.7%** | |

#### Scenario realiste (sans mib_estx50, sans funding supplementaire)

| Source | Contribution $/an |
|---|---|
| CAM + GOR | +$1,050 |
| mes_monday | +$110 |
| gold_trend_mgc V1 | +$180 |
| alt_rel_strength | +$450 |
| mcl_overnight_fri | +$50 |
| **TOTAL realiste** | **+$1,840/an** |
| **Return sur $20K** | **+9.2%** |

**VERDICT** : 30% annualise sur **$20K seul** = **non atteignable** sans mib_estx50 (+EUR 3.6K funding) ou sans strat high-Sharpe supplementaire.

**Recommendation** :
- Cible M3 realiste : **+10-15% annualise sur $20K** avec 5 strats live diversifiees.
- 30% annualise atteignable uniquement post funding mib_estx50 OU scaling capital a ~$50K avec portfolio elargi.

---

## 4. Capital occupancy — metrique canonique

### Definition

**Capital occupancy** = `sum(gross notional moyen des positions live) / equity total du book`.

Mesure sur fenetre glissante 30 jours. Neutre au PnL (separation performance / utilisation).

### Benchmarks cibles

| Book | Occupancy M1 | Occupancy M3 | Seuil alerte |
|---|---|---|---|
| ibkr_futures | >= 20% | >= 50% | <= 10% pendant 2 sem = revue |
| binance_crypto | >= 15% | >= 40% | <= 5% pendant 2 sem = revue |
| **Portfolio combine** | >= 15% | >= 35% | <= 5% pendant 2 sem = flag P1 |

### Implementation (script a scaffolder)

**Fichier** : `scripts/capital_occupancy_report.py` (proposition, pas encore ecrit).

Lit :
- `data/state/ibkr_futures/positions_live.json` (positions instantanees).
- `data/tax/classified_trades.jsonl` (historique fills).
- `data/state/binance_crypto/equity_state.json` (equity book).
- IBKR API snapshot (via broker client authenticate) pour positions temps-reel.

Produit :
- Tableau occupancy % par strat par jour (30j glissant).
- Heatmap capital idle time.
- Alertes Telegram si occupancy < seuil 14j.

**Status** : **NON IMPLEMENTE**. Peut etre scaffolde en 1h. Proposition : apres classification strats faite + 3-4 strats live probation actives.

---

## 5. Allocation cible 20K (proposition decision user)

### V1 conservative (pre-mib_estx50)

| Book | Strat | Risque-if-stopped max | Max gross notional | Rationale |
|---|---|---|---|---|
| ibkr_futures | CAM | $500 (5%) | $10K (1-2 contrats) | Moteur principal, keep |
| ibkr_futures | GOR | $500 (5%) | $10K | Complement CAM decorrele |
| ibkr_futures | mes_monday (post 2026-05-16) | $300 | 1 contrat MES ~$6K | Probation minimal |
| ibkr_futures | gold_trend_mgc V1 (post WF) | $500 | 1 contrat MGC ~$20K buying power | Recalibre V1, sizing reduit |
| ibkr_futures | **cap global** | **max $1,500 risk simultane** | **max 4 contrats** | cf `config/limits_live.yaml` |
| binance_crypto | alt_rel_strength | $980 (10% DD abs) | $3K gross (6x$500) | Probation sleeve #1 |
| binance_crypto | btc_asia long-only (post B5) | $800 (8%) | $5K notional | Sleeve #2 optionnel |
| binance_crypto | **cap global** | **max $1,800 risk** | **max $8K gross** | |
| alpaca_us | **paper-only** | 0 | 0 | Waive PDT requis, cf `alpaca_go_25k_rule.md` |
| ibkr_eu | **paper-only** | 0 | 0 | mib_estx50 attend funding EUR 3.6K |
| **PORTFOLIO TOTAL** | — | **max $3,300 risk** (16.5% equity total) | ~$25K gross max | 20K equity : leverage brut acceptable |

### V2 si funding +EUR 3.6K mib_estx50

Ajouter :
- **mib_estx50_spread** : EUR 13.5K margin (60-70% equity book ibkr_eu si dedicated). Kill criteria DD spread EUR 14K.
- Book IBKR EU passe de paper_only a live_allowed (decision formelle + runtime entrypoint whitelist-aware a dev/confirmer).

### V3 si PDT waiver Alpaca + $25K deposit

- us_sector_ls_40_5 live_probation apres 30j paper + re-WF ETF data.
- Capital Alpaca live $25K dedicated.
- Portfolio total ~$45K equity, diversifie 3 brokers.

---

## 6. Trajectoire 20K -> 100K (qualitative)

### Etape 1 : 20K -> 30K (ROC + performance)
- 5-7 strats live diversifiees.
- +10-15% ROC annualise = +$2-3K/an.
- Build up capital via reinvestissement profits sur 12-24 mois.
- **Sans injection capital supplementaire**.

### Etape 2 : 30K -> 60K (injection capital + Alpaca PDT)
- Dep Alpaca +$25K pour PDT waiver.
- Activation us_sector_ls + us_stocks_daily (paper -> probation).
- Books : IBKR futures + Binance + Alpaca US + EU optionnel.

### Etape 3 : 60K -> 100K (scaling + redondance)
- Augmentation sizing sur strats graded A+.
- Envisager 2e VPS (redondance ops) a partir de $75K.
- Alerting multi-canal (Slack + SMS fallback).
- Monitoring renforce + post-mortem hebdo.

### Conditions de go
A chaque etape, exiger :
- ROC observe >= 10% annualise sur 6 mois minimum.
- Max drawdown < 15% sur 12 mois.
- 0 incident P0 sur 3 mois.
- Decision user explicite.

**Principe directeur** : scaler **seulement apres preuve machine-readable** (feedback_prove_profitability_first).

---

## 7. Metriques a implementer (roadmap)

1. **Capital occupancy tracker** (P1) : script lit positions + equity, output timeseries + alertes.
2. **ROC par strat** (P1) : agreger PnL par strategy_id sur fenetre 30j/90j/365j.
3. **Marginal contribution analyzer** (P2) : corr portfolio, marginal Sharpe ajoute.
4. **Dashboard widget occupancy** (P2) : live graphique dans dashboard v2.
5. **Weekly report auto** (P3) : rapport email/Telegram dim soir.

---

## 8. Diagnostic final

**Score ROC / capital usage : 4.0 / 10**

| Dimension | Note | Justification |
|---|---|---|
| Capital deploye lisible | 7.0 | Registries canoniques OK, mais pas de vue agregee live_pnl_tracker (2j data) |
| Occupancy observee | 3.0 | 1.09% actuellement. Massive gap vs cible 15-30%. |
| ROC mesurable par strat | 4.0 | Pas de metric par strat 30j, seulement snapshot positions. |
| Contribution marginale | 2.0 | Pas d'analyse corr portfolio par strat live. Quant work manquant. |
| Alignement capital cible / reel | 5.0 | Allocation proposee dans allocation.yaml mais pas re-baseline post-drain. |

**Cause principale** : plateforme solide gouvernance mais **trop peu de strats actives** -> capital idle. La trajectoire de resolution passe par **promotion 3-5 nouvelles strats sur 4-8 semaines**, pas par refactor technique supplementaire.

**Accord avec directive user** : ne **PAS** augmenter le capital a risque pour "ameliorer l'occupancy". Promouvoir vraies strats, mesurees, probation-gate-validated.
