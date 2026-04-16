# Gold Trend MGC — recalibration SL/TP V1 (Option B)

**Date** : 2026-04-16
**Auteur** : Marc + Claude (PO review by po-review subagent)
**Status** : V1 deployed in code, status `paper_only` until WF+MC validation

## Contexte

Aujourd'hui 2026-04-16, j'ai ouvert un trade LIVE sur la stratégie `gold_trend_mgc`
(BUY 1 MGCM6 @ 4809.50, SL 4731.25, TP 4947.40). Audit pre-trade a révélé un bug
structurel : **le SL natif strat n'est jamais touché en production** car le système
de risque `live_risk_cycle` déclenche le `deleveraging level_3_dd_pct: 0.018` à
-1.8% NAV (~ MGC drop -0.4%), bien avant le SL natif strat à -1.5% MGC (-6.4% NAV
sur 1 contrat).

## Diagnostic backtest

Backtest 5Y MGC daily (`scripts/research/backtest_gold_trend_sl_variants.py`) :

| # | Variante | SL | TP | Trades | Total $ | WR | Sharpe | MaxDD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| V0 | Baseline strat actuel | 1.5% | 3.0% | 321 | +26,549 | 43.3% | 0.73 | -20.9% |
| V1 | SL serré R/R 2:1 | 0.4% | 0.8% | 760 | +23,809 | **47.6%** | **1.58** | **-7.1%** |
| V2 | SL serré + TP large | 0.4% | 3.0% | 474 | **+40,920** | 25.5% | 1.34 | -8.1% |
| **V3** | ⚠️ Réalité prod V0+dlv | 1.5% | 3.0% +dlv -1.8% | 369 | **+11,700** | 35.0% | 0.33 | **-32.7%** |
| V4 | Baseline TP 6% | 1.5% | 6.0% | 253 | +27,570 | 40.7% | 0.65 | -23.8% |
| V5 | Baseline TP 10% | 1.5% | 10% | 240 | +34,102 | 40.8% | 0.69 | -21.3% |

**V3 = situation actuelle prod : -56% PnL et -28% MaxDD vs baseline V0**.
La strat est sabotée silencieusement par le deleveraging mal calibré.

## Décision : V1 (Option B)

Choix : V1 (SL 0.4% / TP 0.8%) plutôt que V2 (SL 0.4% / TP 3.0%) malgré PnL backtest
plus faible.

**Pourquoi V1 et pas V2 (validation PO)** :
- V1 Sharpe 1.58 > V2 Sharpe 1.34 (meilleure stabilité)
- V1 WR 47.6% vs V2 WR 25.5% : V2 = `kill_criteria` "5 consecutive losses"
  quasi-certain (P=24% par fenêtre de 5 trades) → strat retirée auto en < 6 mois
- V1 MaxDD -7.1% vs V2 -8.1% : équivalent
- V1 = 760 trades / 5Y = ~150/an = stable, V2 = 474 trades volatiles
- Backtest solo surévalue V2 car CAM `first-refusal` mange une partie des trades
  MGC en portefeuille → V2 n'atteindra jamais 474 trades en réalité live

## Implémentation no-regression

### Code modifié
- `strategies_v2/futures/gold_trend_mgc.py` : defaults `sl_pct=0.004`, `tp_pct=0.008`
  + commentaire historique V0 vs V1
- `config/live_whitelist.yaml` :
  - `gold_trend_mgc.status` : `live_core` → `paper_only`
  - `gold_trend_mgc.sl_pct` : 0.015 → 0.004
  - `gold_trend_mgc.tp_pct` : 0.03 → 0.008
  - `metadata.version` : 2 → 3
  - notes documentent la transition

### Trade live actuel (BUY 1 MGCM6 @ 4809.50)
- **NE PAS modifier** (recommandation PO formelle)
- SL natif sur IBKR : pattern de rejection silencieuse (5 tentatives échouées),
  position protégée par `live_risk_cycle` deleveraging level_3 (close all à -1.8% NAV
  ≈ MGC drop à 4791.5)
- Documenté dans `data/audit/orders_2026-04-16.jsonl`

### Régression évitée
- Aucun changement de `sl_pct`/`tp_pct` autres strats
- Aucun changement deleveraging level_1/2/3
- `gold_trend_mgc.status` = `paper_only` → strat ne peut pas placer de nouveau
  trade live tant que pas re-promue (whitelist enforcement actif)
- Le trade live actuel reste sous V0 params (SL 4731 / TP 4947) jusqu'à fermeture
  par TP, deleveraging, ou intervention manuelle

## Plan validation (Phase 1 PO)

### TODO obligatoires avant retour en `live_core`

1. **WF 5 windows** sur V1 (5Y MGC daily) — gate ≥ 3/5 OOS profitable
2. **Monte Carlo 1000 sims** bootstrap — gate P(DD>30%) < 15%
3. **Recalcul kill_criteria** "5 consecutive losses" pour V1 :
   - WR 47.6% → P(5 losses) = (1-0.476)^5 = 4.0% → survivable ✅
   - vs V2 WR 25.5% → P(5 losses) = 23.6% → quasi-mort
4. **Paper run 30 jours** sur V1 sans divergence vs backtest > 2 sigma
5. **Bumper `level_1_dd_pct`** de 0.009 (-0.9%) à 0.012 (-1.2%) pour ne pas
   prematurely cut V1 SL 0.4% (donner du marge intra-trade)

### Si tout pass → re-promote

- `config/live_whitelist.yaml` : `paper_only` → `live_core`
- Restart worker, monitor 1 semaine en live avec sizing réduit (1/2)

## Risques résiduels identifiés (PO)

1. **First-refusal CAM sur MGC** : backtest solo n'est jamais atteignable
2. **Backtest 5Y pas assez de bears** : 2018-19, 2020 COVID, 2022 → tester sur
   1Y supplémentaire (2017 ou ancien data si dispo)
3. **Pattern SL persistence IBKR** : 5 tentatives échouées sur le trade live actuel,
   à investiguer (STP outsideRth, OCA system-assigned, currency mismatch EUR/USD)
4. **Live_risk cycle stoppe la position avant SL natif** : même avec V1 (SL 0.4%
   = level_3 dlv), le `level_1_dd_pct: 0.009` (reduce 30%) déclenche encore
   AVANT SL natif → V1 SL n'est toujours pas le seul mécanisme
5. **Sample size live court** : ~150 trades/an V1, 30 jours paper = ~12 trades,
   pas assez pour validation statistique (P(false positive) élevée)

## Audit trail

- Trade live initial : `data/audit/orders_2026-04-16.jsonl`
- Backtest script : `scripts/research/backtest_gold_trend_sl_variants.py`
- PO review : déléguée à `po-review` subagent 2026-04-16 18:30 UTC
- Decision user : "GO Option B" 2026-04-16 18:50 UTC
