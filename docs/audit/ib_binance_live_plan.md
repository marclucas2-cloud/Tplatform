# IB Futures + Binance — Plan Live Concret

**Date** : 2026-04-19
**Horizon court terme** : 2026-04-20 -> 2026-06-30 (6-10 semaines).
**Objectif** : passer de 2 strats live / 1.1% capital usage a 5-7 strats live / 30-40% capital usage, **sans sacrifier l'edge**.

---

## 1. IBKR futures — 1er moteur live, deja operationnel

### Etat actuel (2026-04-19)
- Account U25023333, equity **$11,012.79**, buying power $58K.
- **2 live_core** : cross_asset_momentum (grade A), gold_oil_rotation (grade S).
- **1 position ouverte** : MCL BUY 1 contrat, entry $75.85, SL $73.57, TP $81.92, mark $78.81, **unrealized +$295**.
- Risk used : ~$228 (entry - SL) x 100 = 2.1% du book.

### Classification proposee (decision PO requise)

| Strat | Classification proposee | Justification | Action concrete |
|---|---|---|---|
| cross_asset_momentum | **live_core** (maintenir) | Grade A, Sharpe portfolio 0.85, first-refusal sur 5 symboles. Position MCL ouverte genere +$295. | Rien. Continuer. |
| gold_oil_rotation | **live_core** (maintenir) | Grade S, Sharpe 0.87 portefeuille, 5/5 OOS. Corr faible CAM. Rotation MGC/MCL. | Rien. Signal dormant attendu. |
| gold_trend_mgc | **paper_only stricte tant que WF V1 pas livre** | V1 recalibration SL 0.4% / TP 0.8% pending WF + MC. Pas de manifest physique. V0 legacy toxique (backtest +$11.7K mais prod -32.7% MaxDD). | **B2** : lancer `scripts/wf_gold_trend_mgc_v1.py`. Produire manifest physique. Apres WF ok + 30j paper = candidate 1 promotion. |
| mes_monday_long_oc | **probation scheduled 2026-05-16** | WF 3/5 OOS, MC P(DD>30%)=9.8%. Paper stable jusqu'a 2026-05-16 = auto promote avec sizing minimal. | Surveiller paper journal. Pas de divergence > 2 sigma. |
| mes_wednesday_long_oc | **paper_only (prolonge a 45j)** | MC P(DD>30%)=28.3% **limite**. WF 4/5 OK. Risque a surveiller. | Surveillance 45j (vs 30j standard). Reconsiderer promotion 2026-06-01 si divergence contenue. |
| mes_pre_holiday_long | **paper_only (re-evaluer apres 90j)** | WF 5/5 parfait, MC P(DD>30%)=0% EXCELLENT, mais trade rare 8-10/an = 0.02-0.03 /jour. Inutile seul pour trade freq. | Garder paper actif. Sizing maintiendra edge. Envisager live avec 2 autres mes_*. |
| mcl_overnight_mon_trend10 | **paper_only stricte tant que B3 pas ferme** | Signal runtime vendredi (capture weekend gap) ≠ signal backtest lundi. Correlation ~0.99 mais re-WF "friday_trigger" requis. Data MCL_1D stale 18j observe ops. | **B3** : re-WF specifique friday trigger. Si PASS -> probation 2026-05-30. |

### Sizing recommande (conservateur)
- **live_core (CAM, GOR)** : max $500 risk-if-stopped par entry (deja en place via `risk_budget_5pct`).
- **probation (mes_monday post 2026-05-16)** : **1 contrat fixed**, SL -10% max, max 1 strat simultanee d'abord.
- **Portfolio cap global** : 4 contrats max simultanes (cf `config/limits_live.yaml#futures_limits`).

### Trajectoire IBKR futures
- **2026-04-19** : 2 strats live (CAM, GOR). Done.
- **2026-05-16** : +1 probation (mes_monday). 3 live, sizing minimal (1 contrat).
- **2026-06-01** : +1 si gold_trend_mgc V1 WF OK (mes_wednesday sous revue). 3-4 live.
- **2026-06-30** : re-evaluer cohorte complete. Cible 4-5 strats live simultanees, occupation 40-60% du book futures.

### KPI surveillance mensuelle
- Trade frequency total futures : >= 8/mois (vs ~1-2 actuel)
- Cumulative unrealized + realized : >= 0
- Max drawdown book : <= -10% (cible -5%)
- Correlation live inter-strats : <= 0.5 (moyenne)

---

## 2. Binance crypto — 1 sleeve candidate live_probation

### Etat actuel (2026-04-19)
- Equity **$9,843** (spot USDT $1K + earn $8.87K passif).
- **0 live_core** post bucket A drain 2026-04-19. 11 strats archives REJECTED.
- **2 paper_only candidates** : `alt_rel_strength_14_60_7` (grade B), `btc_asia_mes_leadlag_q70_v80` (grade B).
- **1 disabled** : `btc_dominance_rotation_v2` (REJECTED).

### Decision sleeve candidate live_probation : **`alt_rel_strength_14_60_7`**

#### Justification vs `btc_asia_mes_leadlag_q70_v80`

| Critere | alt_rel_strength_14_60_7 | btc_asia_mes_leadlag_q70_v80 |
|---|---|---|
| Grade WF | B | B |
| Sharpe backtest | +1.11 | +1.07 |
| MaxDD backtest | -7.8% | -7.7% |
| WF OOS | 3/5 | 4/5 (meilleur) |
| MC P(DD>30%) | 0.5% | 0% |
| Bull/Bear robust | **+1.42 / +0.29** (positif 2 regimes) | Non teste (data 2024-04 only) |
| Correlation portfolio | **-0.014** (forte decorrelation) | Non calculee |
| Compat Binance France spot | ✅ longs + shorts via margin isolated | ❌ mode both incompatible spot FR |
| Wire runtime production | ✅ `alt_rel_strength_runner` atomic 6-leg done | ❌ variante long-only q80_v80 a wirer |
| Data freshness bloquante | BTCUSDT + alts parquets stale | BTCUSDT_1h parquet stale |
| Trade frequency | 1 rebalance/semaine (6 positions moyennes) | ~0.3-0.5/jour theorique |

**Verdict** : `alt_rel_strength_14_60_7` **GAGNE** pour :
1. Compatible Binance France spot + margin isolated (pas besoin de dev supplementaire).
2. Decorrelation portefeuille exceptionnelle (corr -0.014).
3. Bull + bear robuste (rare).
4. Runner production-ready, atomic, deja paper.
5. Pas de refactor runtime necessaire.

Le seul avantage de `btc_asia` est la frequence trade plus elevee, mais il **bloque sur B5** (variante long-only a wirer) AVANT de pouvoir considerer live. Si B5 livre, `btc_asia` devient **2eme candidate** (pas remplacante).

#### Plan `alt_rel_strength_14_60_7` live_probation

**Prerequisites (blocker infra)** :
- **B6** : cron VPS refresh `BTCUSDT_1d.parquet` + 10 alts parquets (XRP, ETH, BNB, SOL, ADA, AVAX, DOT, LINK, NEAR, SUI) toutes les 15 minutes. Actuellement stale ~21j observe.
- Margin isolated account Binance active + verifie preflight.
- `market_caps` kwarg populate via CoinGecko ou suppression si filter inutile.

**Critere de promotion live_probation** :
- 30 calendar days paper minimum (start 2026-04-18 -> earliest **2026-05-18**).
- >= 4 cycles hebdomadaires observes sans divergence > 1 sigma vs backtest.
- Cumulative paper PnL >= 0 OR <= +1 sigma vs backtest expected.
- Kill switch crypto inactif 24h+ continu.
- Borrow rates cron actif (shorts margin isolated).
- **User manual greenlight explicit** (feedback_decision_authority).

**Sizing live_probation initial** :
- $500 per leg x 6 legs (3L + 3S) = **$3K gross** sur $9.8K equity = **31% gross leverage**.
- Risk-if-stopped : kill criteria drawdown_absolute -10% = **$980 max loss**.
- **Acceptable a $9.8K capital**. Si Binance equity scale a $15K, ajuster a $800/leg = $4.8K gross.

**Trajectoire** :
- 2026-04-18 -> 2026-05-18 : 30j paper. Surveillance quotidienne divergence.
- 2026-05-18 : decision promotion live_probation. Si PASS -> live margin isolated, sizing minimal $3K gross.
- 2026-06-01 : si live_probation 2 semaines clean + PnL >= backtest -1 sigma -> live_core classification.
- 2026-06-15 : reconsiderer sleeve #2 (btc_asia long-only si B5 livre).

### Binance FX hors scope live
FX IBKR **reste disabled** (ESMA). Aucune action. Code conserve.

---

## 3. Synthese portfolio live cible (2026-06-30)

### Hypothese conservative (P0 + probations realistes)

| Book | Strats live | Capital deploye | ROC contribution attendue |
|---|---|---|---|
| ibkr_futures | CAM + GOR + mes_monday + (mes_wednesday ou gold_trend_mgc) | ~$7-9K (70-80% du book) | +12-15% annualise blended |
| binance_crypto | alt_rel_strength_14_60_7 (+ btc_asia long-only optionnel) | ~$3-5K (30-50% du book) | +10-15% annualise alt_rel, +5-8% btc_asia |
| alpaca_us | — (paper-only tant que PDT waiver) | 0 live | 0 (paper seulement) |
| ibkr_eu | mib_estx50 si user funde +EUR 3.6K | EUR 13.5K | +15-25% annualise EUR (Sharpe 3.91 backtest) |
| **TOTAL** | **5-7 strats live** | **~$18-25K capital live** | **~12-15% portfolio annualise** |

### Hypothese optimiste (tous blockers leves)

| Book | Strats live | Capital deploye |
|---|---|---|
| ibkr_futures | 5 strats (+ gold_trend_mgc V1 + mcl_overnight_fri) | $10K (max usage) |
| binance_crypto | 2 strats (alt_rel + btc_asia long-only) | $6K |
| alpaca_us | 2 strats post PDT waiver | $25K (capital supplementaire) |
| ibkr_eu | mib_estx50 | EUR 13.5K |
| **TOTAL** | **10 strats live** | **~$45K capital live** |

---

## 4. KPI mensuel de suivi (objectif Mai 2026)

| KPI | Cible M1 | Cible M3 | Seuil stop-loss |
|---|---|---|---|
| Trade frequency (moyenne) | >= 0.3/jour | >= 0.6/jour | <= 0.05/jour -> revue plan |
| PnL net cumule | >= 0 | >= +3% capital deploye | <= -5% -> pause promotion |
| Max drawdown portfolio | <= -5% | <= -8% | > -10% = kill global |
| Capital occupancy moyen | >= 15% | >= 30% | <= 5% pendant >2 sem = revue |
| Correlation live inter-strat | <= 0.5 | <= 0.4 | > 0.7 = revue |
| Incidents P0/P1 ouverts | 0 | 0 | > 0 = pause promotion |

---

## 5. Check-list d'execution (action-ready)

### Semaine 2026-04-20 -> 2026-04-26
- [ ] **B2** : scaffold `scripts/wf_gold_trend_mgc_v1.py` + run + write `data/research/wf_manifests/gold_trend_mgc_v1_2026-04-21.json`.
- [ ] **B5** : creer `strategies/crypto/btc_asia_mes_leadlag_long_only.py` + wire dans `worker.py:run_btc_asia_mes_leadlag_paper_cycle` avec flag config `mode: long_only`.
- [ ] **B6** : valider/creer cron VPS `data_refresh_crypto_parquets.sh` toutes les 15min.
- [ ] **B9** : lundi 2026-04-20 10h30 CEST, verifier `tail -300 logs/worker/worker.log | grep paper_cycle` pour confirmer que les paper runners tournent.
- [ ] Checkpoint user : decision funding +EUR 3.6K mib_estx50 OUI/NON.

### Semaine 2026-04-27 -> 2026-05-03
- [ ] Run runtime_audit hebdo + dashboard D3 check.
- [ ] Measurer divergence vs backtest sur `alt_rel_strength_14_60_7` (J+9 paper).
- [ ] Si B5 livre, wire `btc_asia_long_only` en paper.

### Semaine 2026-05-04 -> 2026-05-10
- [ ] J+14 paper alt_rel_strength : mid-checkpoint divergence + kill switch healthy.
- [ ] Verifier gold_trend_mgc V1 WF reproduit + MC OK.

### Semaine 2026-05-11 -> 2026-05-17
- [ ] Preparer promotion_gate check pour mes_monday (J+30 paper = 2026-05-16).
- [ ] Preparer decision live_probation alt_rel_strength (J+30 = 2026-05-18).

### Semaine 2026-05-18 -> 2026-05-24
- [ ] **Go/no-go alt_rel_strength live_probation** sur 30j paper metrics.
- [ ] Promotion mes_monday si blockers = 0.
- [ ] Premier bilan mensuel formel (rapport `docs/audit/live_performance_may2026.md`).
