# IB Futures + Binance — Plan Live Concret

**As of** : 2026-04-19T14:33Z (post iter3-fix : B2 gold_trend_mgc grade A livre + B5 btc_asia long_only wire)
**Horizon** : 2026-04-20 -> 2026-06-30 (6-10 semaines).
**Objectif** : passer de 2 strats live / 1.09% capital usage a 5-7 strats live / 30-40% capital usage, **sans sacrifier l'edge**.

**Sources de verite** :
- VPS runtime_audit 0 incoherence (exit 0)
- quant_registry.yaml + live_whitelist.yaml (post iter3 commits 6e0ee7f, fdcb50d, 386e45e)
- wf_manifests physiques `data/research/wf_manifests/*.json`

---

## 0. Question centrale — qu'est-ce qui peut REELLEMENT trader lundi matin ?

**Reponse courte** : rien de nouveau n'active lundi 2026-04-21. Les 2 strats live (CAM + GOR) continuent. Les paper runners doivent tourner correctement (B9 a confirmer lundi). Aucune promotion live possible avant **2026-05-16 au plus tot**.

Detail :
| Strat | Etat lundi 2026-04-21 | Peut trader live ? | Raison |
|---|---|---|---|
| cross_asset_momentum | ACTIVE (deja) | ✅ oui (continuite) | live depuis 2026-04-07 |
| gold_oil_rotation | ACTIVE (deja) | ✅ oui (continuite) | live depuis 2026-04-08 |
| gold_trend_mgc | READY (grade A) | ❌ non | paper 30j requis (start 2026-04-16, earliest 2026-05-16) |
| mes_monday_long_oc | READY | ❌ non | paper 30j requis (start 2026-04-16, earliest 2026-05-16) |
| alt_rel_strength_14_60_7 | READY | ❌ non | paper 30j requis (start 2026-04-18, earliest 2026-05-18) |
| btc_asia q80_long_only | READY (iter3-fix B5) | ❌ non | paper 30j requis (start 2026-04-20, earliest 2026-05-20) |
| mib_estx50_spread | READY grade S | ❌ non | bloque par capital EUR 3.6K gap + paper_only book |
| autres | READY/AUTHORIZED | ❌ non | blockers varies (voir tableau) |

**=> Lundi matin : desk tourne identique. Pas de nouveau live. C'est la realite.**

---

## 1. Ce qui est theoriquement bon vs ce qui est executable maintenant

### 1.1 Theoriquement bon (validation WF + metriques OK)

| Strat | Grade | WF OOS | MC P(DD>30%) | Sharpe BT | Source manifest |
|---|---|---|---|---|---|
| cross_asset_momentum | A | 4/5 | n/a portfolio | 0.85 portfolio | cross_asset_momentum_2026-04-19_backfill.json |
| gold_oil_rotation | S | 5/5 | 0% | 0.87 portfolio | gold_oil_rotation_2026-04-19_backfill.json |
| gold_trend_mgc V1 | A | **4/5** | **0.15%** | **2.625 OOS** | gold_trend_mgc_v1_2026-04-19.json (**iter3-fix B2**) |
| mes_monday_long_oc | B | 3/5 | 9.8% | +0.22 dSharpe | mes_monday_long_oc_2026-04-19_backfill.json |
| mes_pre_holiday_long | B | **5/5** | **0%** | +0.07 dSharpe | mes_pre_holiday_long_2026-04-19_backfill.json |
| mcl_overnight_mon_trend10 | B | 4/5 | 0% | 0.80 | mcl_overnight_mon_trend10_2026-04-19_backfill.json |
| alt_rel_strength_14_60_7 | B | 3/5 | 0.5% | 1.11 | alt_rel_strength_14_60_7_2026-04-19_backfill.json |
| btc_asia_mes_leadlag_q70_v80 | B | 4/5 | 0% | 1.07 | btc_asia_mes_leadlag_q70_v80_2026-04-19_backfill.json |
| mib_estx50_spread | S | 4/5 | n/a | 3.91 | wf_mib_estx50_corrected (reports/research/) |

### 1.2 Executable MAINTENANT (lundi 2026-04-21)

- Position MCL (1 contrat) continue avec bracket SL 73.57 / TP 81.92 via CAM
- Signal GOR reste dormant jusqu'a spread momentum >= 2%
- **Pas d'autre ordre live possible sans promotion paper -> live**

### 1.3 Executable dans 30 jours (2026-05-18 fenetre)

Si paper journals clean et promotion_gate passe :
- mes_monday_long_oc 2026-05-16 (3 jours avant fenetre plus large)
- gold_trend_mgc V1 2026-05-16 (post paper ok)
- alt_rel_strength_14_60_7 2026-05-18
- btc_asia_q80_long_only 2026-05-20

---

## 2. IBKR futures — 1er moteur live, deja operationnel

### 2.1 Etat actuel (2026-04-19, source VPS)
- Equity $11,012.79, BP $58K (source: `data/state/ibkr_futures/equity_state.json` VPS).
- Position MCL BUY 1 contrat, entry $75.85, SL $73.57, TP $81.92, mark $78.81, unrealized +$295.23 (source: `data/state/ibkr_futures/positions_live.json` VPS).
- Risk-if-stopped : $228 (~2.07% du book).

### 2.2 Classification (post iter3-fix)

| Strat | Classification | Statut lundi 2026-04-21 | Action concrete |
|---|---|---|---|
| cross_asset_momentum | **live_core (maintenir)** | `ready now` — ACTIVE | Rien. Continuer. |
| gold_oil_rotation | **live_core (maintenir)** | `ready now` — ACTIVE (signal dormant) | Rien. Attente spread > 2%. |
| gold_trend_mgc | **live_probation_scheduled 2026-05-16** | `blocked by paper time` | iter3-fix B2 resolu : WF V1 grade A livre. Surveiller paper 30j. |
| mes_monday_long_oc | **live_probation_scheduled 2026-05-16** | `blocked by paper time` | Surveiller paper 30j, no divergence > 2 sigma. |
| mes_wednesday_long_oc | **paper_only_prolonge 45j** | `blocked by missing artifact` (MC P(DD>30%)=28.3% limite) | Surveillance 45j (pas 30j). Reconsider 2026-06-01. |
| mes_pre_holiday_long | **paper_only_revue_90j** | `blocked by frequency` (trade rare 8-10/an seul) | Garder paper. Utile en combine avec mes_monday. |
| mcl_overnight_mon_trend10 | **paper_only_stricte** | `blocked by missing artifact` (re-WF friday trigger requis) | Scaffold re-WF script friday vs monday correlation 0.99. |

### 2.3 Sizing cible (conservateur)
- `live_core` (CAM, GOR) : max $500 risk-if-stopped par entry (cf `config/limits_live.yaml#futures_limits`).
- `probation` (mes_monday post 2026-05-16) : **1 contrat fixed**, SL -10%, 1 strat promouvable simultanee d'abord.
- `cap global` : 4 contrats max simultanes.

### 2.4 Trajectoire IBKR futures
| Date | Evenement | Strats live | Action |
|---|---|---|---|
| 2026-04-19 | baseline iter3-fix | 2 (CAM, GOR) | `ready now` |
| 2026-04-21 (lundi) | verif paper runners weekday | 2 | `ready now` |
| 2026-05-16 | promotion gate check mes_monday + gold_trend_mgc V1 | +0 ou +1 ou +2 | depend paper |
| 2026-06-01 | re-eval mes_wednesday (MC additionnel) | +0 ou +1 | `blocked by missing artifact` jusqu'alors |
| 2026-06-30 | bilan mois 1 | cible 3-4 strats live | revue KPI |

---

## 3. Binance crypto — 1 sleeve candidate live_probation

### 3.1 Etat actuel (2026-04-19, source VPS)
- Equity $9,843 (spot USDT $1K + earn $8.87K passif). 0 live.
- 2 paper_only variants `btc_asia_mes_leadlag_*` + 1 `alt_rel_strength_14_60_7`.
- 1 disabled `btc_dominance_rotation_v2` (REJECTED).

### 3.2 Decision sleeve candidate live_probation : `alt_rel_strength_14_60_7`

#### Justification (inchangee post iter3-fix)

| Critere | alt_rel_strength_14_60_7 | btc_asia q80_v80_long_only (iter3-fix B5) |
|---|---|---|
| Grade WF | B | B |
| Sharpe backtest | +1.11 | +1.08 |
| MaxDD backtest | -7.8% | -7.7% |
| WF OOS | 3/5 | 4/5 |
| MC P(DD>30%) | 0.5% | 0% |
| Bull/Bear robust | **+1.42 / +0.29** (positif 2 regimes) | non teste (data 2024-04 only) |
| Corr portfolio | **-0.014** (forte decorrelation) | non calculee |
| Binance France spot compat | ✅ longs + shorts via margin isolated | ✅ long_only pur |
| Wire runtime | ✅ deja paper atomic 6-leg | ✅ iter3-fix B5 livre |
| Data freshness bloquante | ⚠️ BTCUSDT + alts parquets stale (B6r non resolu) | ⚠️ idem |
| Trade frequency | 1 rebalance/semaine | ~0.3-0.5/jour theorique |
| Paper start | 2026-04-18 (1j observe) | 2026-04-20 (J0 lundi) |
| Earliest promotion | 2026-05-18 | 2026-05-20 |

**Verdict** : `alt_rel_strength_14_60_7` reste candidate #1 pour la richesse decorrelation, bull/bear robust, atomic runner en place. `btc_asia_q80_long_only` = candidate #2 (wire livre, journalisera a partir de 2026-04-20).

### 3.3 Plan `alt_rel_strength_14_60_7` live_probation

#### Prerequisites bloquants (status apres iter3-fix)

| Blocker | Status | Action |
|---|---|---|
| **B6 MES_1H_YF2Y** | ✅ fixe (iter3) | cron crontab VPS 21:35 UTC Mon-Fri |
| **B6r BTCUSDT alts stale** | ❌ **NON FIXE** iter3 | Proposer cron 15min refresh AVANT promotion 2026-05-18 |
| margin isolated Binance | ✅ actif | confirmer preflight |
| borrow rates cron | ⚠️ a confirmer | check logs VPS |
| `market_caps` CoinGecko kwarg | ⚠️ a confirmer | populate ou suppression |

#### Critere de promotion live_probation (canonique)

1. >= 30 calendar days paper (2026-04-18 -> 2026-05-18 minimum)
2. >= 4 cycles hebdomadaires observes sans divergence > 1 sigma
3. `paper_pnl_net >= 0` OR `<= +1 sigma` vs backtest expected pour la periode
4. Kill switch crypto inactif 24h+ continu
5. Borrow rates cron actif (si shorts actives via margin)
6. **User manual greenlight explicit** (feedback_decision_authority)

#### Sizing live_probation initial
- $500 per leg x 6 legs = $3K gross sur $9.8K equity = 31% gross
- Kill `drawdown_absolute: -10%` = $980 max loss
- Acceptable a $9.8K.

#### Trajectoire
| Date | Evenement | Statut |
|---|---|---|
| 2026-04-18 | paper start | `blocked by paper time` |
| 2026-05-18 | decision promotion live_probation | IF paper_pnl_net >= 0 AND 0 incident : PROMOTE |
| 2026-06-01 | live_probation -> live_core si 2 sem clean | revue |
| 2026-06-15 | reconsiderer sleeve #2 (btc_asia long-only) | depend paper 30j btc_asia (2026-05-20 earliest) |

### 3.4 FX / ibkr_fx hors scope live
FX reste **disabled** (ESMA EU leverage limits). Code conserve. Rien a faire.

---

## 4. Synthese portfolio live cible 2026-06-30

### 4.1 Hypothese conservative (sans funding supplementaire)
| Book | Strats live | Capital deploye | ROC contribution attendue (haircut -50% vs backtest) |
|---|---|---|---|
| ibkr_futures | CAM + GOR + mes_monday + gold_trend_mgc V1 | ~$7-9K (70-80% du book) | +12-15% annualise blended |
| binance_crypto | alt_rel_strength | ~$3K gross (30% du book) | +10-15% annualise |
| alpaca_us | — | 0 live | 0 (paper only, gate NO_GO) |
| ibkr_eu | — | 0 live | 0 (mib_estx50 bloque par capital) |
| **TOTAL** | **4-5 strats live** | **~$10-12K capital live** | **~9-13% portfolio annualise** |

### 4.2 Hypothese optimiste (tous blockers leves, +funding mib_estx50 et/ou Alpaca)
| Book | Strats live | Capital deploye |
|---|---|---|
| ibkr_futures | 5 strats (+ mcl_overnight_fri post re-WF) | ~$10K |
| binance_crypto | 2 strats (alt_rel + btc_asia long_only) | ~$5-6K |
| alpaca_us | 2 strats post PDT waiver | $25K funded |
| ibkr_eu | mib_estx50 post +EUR 3.6K funding | EUR 13.5K |
| **TOTAL** | **10 strats live** | **~$45K equity** |

---

## 5. KPI mensuel (objectif Mai 2026)

| KPI | Cible M1 | Cible M3 | Seuil stop-loss | Source de verite |
|---|---|---|---|---|
| Trade frequency (moyenne) | >= 0.3/jour | >= 0.6/jour | <= 0.05/jour 2 sem = revue plan | classified_trades.jsonl + positions_live.json VPS |
| PnL net cumule | >= 0 | >= +3% | <= -5% = pause promotion | live_pnl_tracker.py |
| Max drawdown portfolio | <= -5% | <= -8% | > -10% = kill global | DDBaselines state files |
| Capital occupancy moyen | >= 15% | >= 30% | <= 5% 2 sem = revue | scripts/capital_occupancy_report.py (**non implemente**) |
| Correlation live inter-strat | <= 0.5 | <= 0.4 | > 0.7 = revue | a calculer ex-post |
| Incidents P0/P1 ouverts | 0 | 0 | > 0 = pause promotion | data/incidents/*.jsonl |

---

## 6. Check-list executable (action-ready, etat reel)

### Semaine 2026-04-20 -> 2026-04-26

- [ ] **Verif lundi 10h30 CEST** : `ssh vps "tail -300 logs/worker/worker.log | grep -iE 'paper_cycle|runner|leadlag'"` → confirmer paper runners fire. Source verifiable.
- [x] **B2** iter3-fix : WF gold_trend_mgc V1 manifest livre. Grade A VALIDATED.
- [x] **B5** iter3-fix : btc_asia long_only variante wiree en paper. paper_start 2026-04-20.
- [x] **B6** iter3-fix (partie MES) : cron MES_1H_YF2Y.parquet weekday 21:35 UTC deploye.
- [ ] **B6r residuel** : cron crypto alts 15min refresh AVANT promotion alt_rel_strength (deadline 2026-05-18).
- [ ] Checkpoint user : decision funding +EUR 3.6K mib_estx50 OUI / NON.

### Semaine 2026-04-27 -> 2026-05-03
- [ ] Run runtime_audit hebdo VPS + dashboard D3 check.
- [ ] Mesurer divergence vs backtest `alt_rel_strength_14_60_7` (J+9 paper). Source : paper_journal.jsonl VPS.

### Semaine 2026-05-04 -> 2026-05-10
- [ ] J+14 paper alt_rel_strength mid-checkpoint.
- [ ] Si plan : scaffold re-WF `mcl_overnight_friday_trigger` script.

### Semaine 2026-05-11 -> 2026-05-17
- [ ] Preparer promotion_gate check mes_monday (J+30 paper = 2026-05-16).
- [ ] Preparer decision live_probation gold_trend_mgc V1 (J+30 paper = 2026-05-16).

### Semaine 2026-05-18 -> 2026-05-24
- [ ] **Go/no-go alt_rel_strength live_probation** sur 30j paper metrics.
- [ ] Promotion mes_monday si blockers = 0.
- [ ] Premier bilan mensuel formel (rapport `docs/audit/live_performance_may2026.md`).

---

## 7. Synthese honnete

| Question | Reponse | Confidence |
|---|---|---|
| Combien de strats peuvent trader live lundi 2026-04-21 ? | **2** (CAM + GOR inchange) | high (observe VPS) |
| Combien peuvent dans 30 jours (fenetre 2026-05-16/20) ? | **2-4 selon paper stability** | med |
| Combien dans 60 jours ? | **4-6 avec blockers B6r + funding** | low |
| Quel est le 1er blocker business ? | Temps paper (structurel, pas code) | high |
| Quel est le 2e blocker ? | B6r cron crypto alts stale | high |
| Quel est le 3e ? | User decision funding mib_estx50 | high |
