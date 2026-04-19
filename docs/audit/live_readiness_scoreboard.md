# Live Readiness Scoreboard — Trading Platform

**Date** : 2026-04-19 (iter3 business-focused audit)
**Mode** : comite senior CTO+CRO+Quant+PO+Ops. Anti-bullshit, anti-inflation.
**Source de verite** : `config/books_registry.yaml` + `config/quant_registry.yaml` +
`config/live_whitelist.yaml` + VPS runtime (`scripts/runtime_audit.py --strict`)
+ VPS worker log `logs/worker/worker.log` + state files `data/state/*/`.

---

## 1. Snapshot capital live (source: broker APIs, pas notes)

| Book | Broker | Equity live | Buying power | Positions ouvertes | Unrealized PnL |
|---|---|---|---|---|---|
| ibkr_futures | IBKR U25023333 | **$11,012.79** | $58,073.75 | 1 (MCL 1 contrat via CAM) | **+$295.23** |
| binance_crypto | Binance France | **$9,843** (cash $1K + earn $8.87K) | $1,000 (spot USDT) | 0 | $0 |
| alpaca_us | Alpaca paper | **$99,495.42** | $397,981.68 | 0 live (paper SPY simule) | $0 |
| ibkr_eu | IBKR paper | n/a (EUR 9.9K equity compte mere) | n/a | 0 | 0 |
| ibkr_fx | DISABLED (ESMA) | 0 | 0 | 0 | 0 |

**Total live capital deployable** : **$20,855** (IBKR $11K + Binance $9.8K).
**Alpaca paper** = $99K mais non live, PDT waiver requis ($25K min).
**IBKR EU** = paper uniquement tant que live_portfolio_eu.py pas whitelist-aware.

**Occupation capital live actuelle** :
- IBKR futures : 1 MCL position = notional ~$7,881, risk-if-stopped = ($75.85 - $73.57) x 100 = **$228** (~2.1% du book).
- Binance : **0%** (aucune position).
- Globalement : ~1.1% du capital live a risque, **98.9% idle**. **Massive gap**.

---

## 2. Classification honnete par strategie (16 canoniques)

### Axes de lecture

- **AUTHORIZED** = `books_registry.mode_authorized = live_allowed` + strat dans whitelist (≠ live ready)
- **READY** = preuves machine-readable completes (WF manifest physique + paper_start_at + kill_criteria + grade S/A/B + pas d'infra_gaps bloquants)
- **ACTIVE** = is_live=true dans quant_registry ET position live observee ou cycle live tournant
- **PROMOTABLE** = READY + 30j paper sans divergence > 1-2 sigma + promotion_gate vert
- **CAPITAL_ALLOCATED** = alloue dans `config/allocation.yaml` ou equivalent
- **CAPITAL_USED** = observe en position (moyenne 30j)
- **ROC_CONTRIBUTIVE** = CAGR contribue > 0 + Sharpe > 0 + corr portfolio < 0.5

### Tableau (evidence-based)

| strategy_id | book | AUTH | READY | ACTIVE | PROMOTABLE | CAPITAL_USED | ROC_CONTRIBUTIVE |
|---|---|---|---|---|---|---|---|
| cross_asset_momentum | ibkr_futures | ✅ | ✅ | ✅ **LIVE** | — (deja live) | ~$7.9K (MCL) | ✅ (A, +$295 unrealized) |
| gold_oil_rotation | ibkr_futures | ✅ | ✅ | ✅ **LIVE** | — | 0 actuel (signal inactif) | ✅ (S, Sharpe 0.87 portfolio) |
| gold_trend_mgc | ibkr_futures | ✅ | ❌ (V1 recalib pending) | ❌ (paper) | ❌ (WF/MC pending) | 0 | ❌ (bloque WF) |
| mes_monday_long_oc | ibkr_futures | ✅ | ✅ (WF 3/5, MC 9.8%) | ❌ (paper) | 🟡 apres 2026-05-16 | 0 | 🟡 si promote (~$300 risk/trade) |
| mes_wednesday_long_oc | ibkr_futures | ✅ | 🟡 (WF 4/5 OK, MC 28.3% **limite**) | ❌ | 🟡 sous surveillance | 0 | 🟡 fragile |
| mes_pre_holiday_long | ibkr_futures | ✅ | ✅ (WF 5/5, MC 0%) | ❌ | ❌ trade rare 8-10/an | 0 | 🟡 freq trop basse seul |
| mcl_overnight_mon_trend10 | ibkr_futures | ✅ | 🟡 (friday trigger re-WF requis) | ❌ | ❌ bloque re-WF | 0 | 🟡 edge weekend gap |
| btc_dominance_rotation_v2 | binance_crypto | ❌ **DISABLED** | ❌ REJECTED | ❌ | ❌ jamais | 0 | ❌ |
| alt_rel_strength_14_60_7 | binance_crypto | ✅ | 🟡 (paper 1j seul, WF OK) | ❌ (paper actif 2026-04-19) | ❌ 30j paper requis (2026-05-18) | ~$3K gross simule | 🟡 (+1.11 Sharpe backtest) |
| btc_asia_mes_leadlag_q70_v80 | binance_crypto | ✅ | 🟡 (mode both long/short incompatible spot FR) | ❌ (paper wire mais pas de journal) | ❌ variante long-only a wirer | 0 | 🟡 Sharpe 1.07 mais bloque mode |
| eu_relmom_40_3 | ibkr_eu (paper-only book) | ❌ (book paper_only) | ✅ | ❌ | ❌ (shorts indices CFD ou mini futures sans plan) | 0 | n/a paper book |
| mib_estx50_spread | ibkr_eu (paper-only book) | ❌ (book paper_only) | ✅ grade S | ❌ (paper mais pas de state journal VPS observe) | ❌ margin EUR 13.5K > dispo EUR 9.9K | 0 | ✅ Sharpe 3.91 si capital dispo |
| fx_carry_momentum_filter | ibkr_fx | ❌ **DISABLED** (ESMA) | ❌ | ❌ | ❌ reglementaire | 0 | ❌ |
| us_stocks_daily | alpaca_us (paper-only book) | ❌ | ❌ (meta, wf_exempt) | ❌ | ❌ PDT waiver capital $25K requis | 0 | 🟡 paper |
| us_sector_ls_40_5 | alpaca_us (paper-only book) | ❌ | ✅ (grade B) | ❌ | ❌ shorts PDT + re-WF ETF | 0 | 🟡 paper |

### Synthese cardinal
- **ACTIVE live** : **2 strats** (CAM + GOR sur ibkr_futures)
- **READY live-promotable dans 30j** : **3 strats** seulement (mes_monday, mes_pre_holiday, btc_asia long-only variante a wirer)
- **PROMOTABLE apres blockers** : mcl_overnight (friday re-WF), mib_estx50 (capital EUR +3.6K)
- **BLOQUEES WF** : gold_trend_mgc (V1 recalibration), us_sector_ls (re-WF ETF)
- **DISABLED definitivement** : fx_carry (ESMA), btc_dominance (REJECTED)

---

## 3. Frequence de trades observee (pas theorique)

### Source : IBKR + Binance trades + paper journals

| Periode | IBKR futures | Binance live | Binance paper | Total |
|---|---|---|---|---|
| 30 derniers jours | ~1-2 entries (MCL, CAM entries, gold_oil signals rares) | 0 (depuis drain 2026-04-19) | 0 encore (paper start 2026-04-18) | ~1-2 |
| Dernier 7j | 1 (MCL 2026-04-17) | 0 | 1 (alt_rel_strength init 2026-04-19, 6 legs) | ~2 reel |
| Cible user | — | — | — | **~1/jour moyenne 30j** |

**Verdict** : on est a **~0.1-0.2 trade/jour live**, soit **5-10x en dessous** de la cible "~1/jour".

### Pour atteindre ~1 trade/jour sans degrader l'edge
Options (additive, pas exclusive) :
1. Promouvoir **mes_monday + mes_wednesday + mes_pre_holiday** apres 30j paper = ~25-30 trades/an combines = ~0.1/jour (insuffisant seul).
2. Promouvoir **btc_asia_mes_leadlag long-only variante** apres wire = ~0.3 trades/jour.
3. Promouvoir **alt_rel_strength hebdo** apres 30j paper = 1 rebalance/sem = ~6 positions ouvertes moyen (occupe capital mais trade freq = 1/sem).
4. Promouvoir **mcl_overnight** apres friday re-WF = ~15 trades/an.
5. **Ne PAS forcer des trades sans edge** (anti-bullshit directive).

**Projection optimiste** (toutes promotions validees) : **~0.6-0.8 trade/jour** en 30-45 jours.

**Realiste conservative** : viser **0.3-0.5 trade/jour** en 30j, reconsiderer si gap persiste.

---

## 4. Blockers explicites par objectif

### Objectif A : 1 moteur live vraiment exploitable IBKR futures
**STATUT : DEJA ATTEINT** (CAM + GOR ACTIVE, 1 position ouverte +$295).

Blockers secondaires pour elargir a 4-5 live :
- **B1** : mes_monday/wednesday/pre_holiday : **30j paper obligatoire** (start 2026-04-16, earliest promote = 2026-05-16).
- **B2** : gold_trend_mgc V1 : **WF + MC pending** sur scripts/research/backtest_gold_trend_sl_variants.py (SL 0.4% / TP 0.8%). Pas de manifest. Blocker **CRITIQUE** pour ce moteur reconnu (V0 historique).
- **B3** : mcl_overnight Friday trigger : **re-WF "friday_trigger"** requis car runtime signal emis vendredi (vs backtest lundi) pour capturer weekend gap.

### Objectif B : 1 sleeve Binance candidate serieuse
**STATUT : EN COURS** (2 candidates paper_only depuis 2026-04-18).

Blockers critiques :
- **B4** : `alt_rel_strength_14_60_7` : paper 1 jour seul. Besoin 30j sans divergence > 1 sigma vs backtest. Earliest promote = **2026-05-18**.
- **B5** : `btc_asia_mes_leadlag_q70_v80` : mode **both** (long+short) incompatible Binance France spot (pas de short crypto retail FR). **Variante long-only q80_v80** (Sharpe +1.08) a wirer dans `worker.py:run_btc_asia_mes_leadlag_paper_cycle`. Sans ce wire, strat non promouvable live.
- **B6** : Data freshness : `BTCUSDT_1h.parquet`, alts parquets observes stale. Cron VPS refresh requis AVANT live_probation.

### Objectif C : Pas de fail-open
Blockers residuels :
- **B7** : logging worker **double binding** (chaque ligne apparait 2x dans logs/worker/worker.log). Hygiene, impact disque + parsing log analyzers.
- **B8** : `test_crypto_strategies.py` 80 tests skipped pointant vers fichiers archives (`strategies/crypto/btc_eth_dual_momentum.py` etc.). Non bloquant tests, mais **dette propre** pour quarantaine formelle `tests/_archive`.
- **B9** : paper journals non ecrits pour 9/10 paper strats sur VPS (alt_rel_strength seul). **Verifier week 2026-04-20** que les weekday triggers fired ; sinon P0.

### Objectif D : Capital usage lisible + PnL live visible
**STATUT : PARTIEL**.
- `scripts/live_pnl_tracker.py --summary` = "Insufficient history (need >=2 days)" car baseline commence 2026-04-19. **Acceptable**, mais build-up 30 jours requis.
- Pas de tableau capital_occupancy par strat. Gap livrable : `docs/audit/roc_capital_usage.md`.

### Objectif E : Runtime == whitelist == registries == dashboard
**STATUT : OK**. `runtime_audit.py --strict` VPS exit 0, 0 incoherence, dashboard widget LIVE avec 15 strats classees. Endpoint `/api/governance/strategies/status` repond.

---

## 5. Verdict par book

| Book | Mode | Live strats | Probation candidate | Prochaine action concrete |
|---|---|---|---|---|
| ibkr_futures | live_allowed | 2 (CAM, GOR) | mes_monday_long_oc (le plus solide, earliest 2026-05-16) | Finir WF V1 gold_trend_mgc (B2). Ecrire manifest physique. |
| binance_crypto | live_allowed | 0 | alt_rel_strength_14_60_7 PRIORITE | Wirer variante long-only q80_v80 btc_asia (B5). Cron refresh parquets (B6). Attendre 30j paper. |
| ibkr_eu | paper_only | 0 | mib_estx50_spread grade S | Verifier weekday paper runner ecrit sur VPS (B9). Bloquer live tant que margin EUR+3.6K non dispo. |
| alpaca_us | paper_only | 0 | us_sector_ls_40_5 | Construire Alpaca go/no-go rule machine-readable (voir `alpaca_go_25k_rule.md`). |
| ibkr_fx | disabled | 0 | — | Conserver code archive, pas de reactivation tant qu'ESMA inchangee. |

---

## 6. Score live readiness

**Score global live readiness : 6.5 / 10**

Decompose :
| Dimension | Note | Justification |
|---|---|---|
| Live engine existant | 8.0 | 1 moteur IBKR futures + 1 position active. Solide mais ne couvre que 2 strats sur 16. |
| Diversification promotable 30j | 5.0 | 3 candidates mais B2 (gold_trend WF) et B5 (btc_asia variante) bloquent. Earliest vrai elargissement = 2026-05-16. |
| Fail-open surface | 9.5 | governance fail-closed + per-strategy kill switch + preflight. Residuel = logging double + legacy tests. |
| Capital occupancy | 3.0 | ~1% seulement. **Massive gap.** 98.9% idle. |
| Trade frequency observee | 3.5 | ~0.1-0.2/jour seul le live. 5-10x sous cible. |
| Paper signal quality | 6.0 | 1/10 paper strats produit journal. Weekend = normal mais a valider semaine 20-24 avr. |
| Gouvernance | 9.5 | 12/12 DoD plan 9.0 + plan 9.5 fermes. Solide. |

**NOTE vs score 9.5 plateforme** : l'ecart entre score plateforme (9.5) et score live-readiness (6.5) est **correct et non contradictoire**. La plateforme EST bien cablee ; ce qu'on manque c'est le **temps de paper** et la **diversification live** qui ne se fabriquent pas instantanement.

---

## 7. Top 10 risques residuels (ranked)

1. **Gold trend MGC V1 WF pas livre** (B2) : perte d'un moteur second historiquement reconnu. **P0 recherche**.
2. **btc_asia long-only variante non wiree** (B5) : bloque promotion Binance simple. **P0 code**.
3. **Paper journals silencieux 9/10** : si non-weekend artefact, worker n'ecrit pas -> faux paper signal. **P0 verif lundi 2026-04-20**.
4. **Capital occupancy 1.1%** : un bon moteur idle a 98.9% detruit ROC. **P1 allocation**.
5. **Gold trend ouvert sous anciens params (SL 1.5% TP 3%)** : trade live sous specs V0, pas V1 recalibration. **P1 exit ou upgrade**.
6. **Data freshness parquets crypto** : cron VPS refresh non systematique. **P1 ops**.
7. **Logging double-binding** : hygiene + bruit alertes possibles. **P2**.
8. **Worker restarts frequents** (6/2h observed) : potentiellement lie a deploys iter2. A monitorer apres stabilization. **P2**.
9. **mib_estx50 capital gap EUR 3.6K** : strat grade S bloquee par margin. **P1 funding decision user**.
10. **mes_wednesday MC P(DD>30%) = 28.3%** : tres limite. **P2 Monte Carlo additionnel recommande avant promotion**.

---

## 8. Lundi matin 2026-04-20 — actions utilisateur

**Priorite 1 (15 min)** :
- Verifier que les weekday paper runners ont tourne : `ssh vps "tail -200 logs/worker/worker.log | grep -iE 'paper_cycle|runner'"`. Absence = P0 fix scheduler.

**Priorite 2 (1h)** :
- Lancer WF V1 gold_trend_mgc : `python scripts/wf_gold_trend_mgc_v1.py` (si n'existe pas, le scaffolder). Produire manifest physique. Sans ca, strat bloque.

**Priorite 3 (30 min)** :
- Wirer variante long-only `btc_asia_mes_leadlag_q80_v80` dans worker : creer `strategies/crypto/btc_asia_mes_leadlag_long_only.py` (copie signal, retire short leg) + update `worker.py:run_btc_asia_mes_leadlag_paper_cycle` pour switcher.

**Priorite 4 (10 min)** :
- Approuver nouvelle cron VPS refresh parquets crypto (BTCUSDT_1h, alts) si non deja scheduled.

**Priorite 5 (5 min)** :
- Decision funding : +EUR 3.6K IBKR pour debloquer mib_estx50 ? Arbitrer vs scale IBKR futures existant.
