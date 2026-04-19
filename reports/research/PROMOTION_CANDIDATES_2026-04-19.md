# Promotion candidates audit — 2026-04-19

**Contexte** : après audit comité senior (score 6.0/10 FRAGILE) et directive
"prouver rentabilité avant scaling capital", cet audit catalogue quelles
stratégies paper peuvent légitimement passer live, selon le promotion_gate
durci (DSR + grade S/A/B).

## Pipeline promotion_gate v2

Deux voies:
1. **Standard 30j/10 trades** + manual greenlight signé (toute strat VALIDATED)
2. **S-grade fast-track 14j/5 trades** + manual greenlight signé (seulement si
   wf manifest grade == "S")

**Commandes CLI** :
```bash
# Check standard
python scripts/promotion_check.py <strategy_id>

# Check fast-track S-grade
python scripts/promotion_check.py <strategy_id> --fast-track

# Granter greenlight quand eligible
python scripts/promotion_check.py <strategy_id> \\
    --grant-greenlight=live_probation --signer=marc \\
    --note="30j paper clean, divergence < 1 sigma, kill switch clean"
```

## Classification actuelle (2026-04-19)

| strategy_id | grade | WF | Sharpe OOS | MC DD>30% | status | start paper |
|---|---|---|---|---|---|---|
| gold_oil_rotation | **S** | 5/5 | +6.44 | - | live_core | déjà live |
| cross_asset_momentum | **A** | 4/5 | +0.87 | - | live_core | déjà live |
| mib_estx50_spread | **S** | 4/5 | +3.91 | - | paper_only | 2026-04-18 |
| mes_pre_holiday_long | **B** | 5/5 | +0.57 | 0% | paper_only | 2026-04-16 |
| mcl_overnight_mon_trend10 | **B** | 4/5 | +0.80 | 0% | paper_only | 2026-04-18 |
| btc_asia_mes_leadlag_q70_v80 | **B** | 4/5 | +1.07 | 0% | paper_only | 2026-04-18 |
| eu_relmom_40_3 | **B** | 4/5 | +0.71 | 0% | paper_only | 2026-04-18 |
| alt_rel_strength_14_60_7 | **B** | 3/5 | +1.11 | 0.5% | paper_only | 2026-04-18 |
| us_sector_ls_40_5 | **B** | 3/5 | +0.39 | 0% | paper_only | 2026-04-18 |
| mes_monday_long_oc | **B** | 3/5 | +0.40 | 9.8% | paper_only | 2026-04-16 |
| mes_wednesday_long_oc | **B** | 4/5 | +0.26 | 28.3% | paper_only | 2026-04-16 |

**Why DSR downgrades** :
- mes_pre_holiday_long (INT-A batch 7 variants testées) → DSR p=1.0
- alt_rel_strength_14_60_7 (T4-A2 batch 5 variants) → DSR p=1.0
- btc_asia_mes_leadlag_q70_v80 (n_obs=245 trop court) → DSR p=0.61

Le DSR pénalise **correctement** les strats issues de batches multi-test.
C'est le sens statistique : quand tu testes 7 stratégies et retiens la
meilleure, cette "meilleure" est souvent juste du bruit. Le seul moyen
d'échapper à cette pénalité : **plus de données OOS**.

## Calendrier de promotion réaliste

| date | strat | voie | prérequis avant promotion |
|---|---|---|---|
| **2026-05-02** | mib_estx50_spread | fast-track 14j | (a) 5 trades paper min (b) EUR 13.5K margin disponible sur IBKR (c) greenlight marc |
| **2026-05-16** | mes_pre_holiday_long | standard 30j | (a) 10 trades paper (rare, pre-holiday env. 8/an) → probable pas suffisant, bascule 2026-06 |
| **2026-05-18** | alt_rel_strength_14_60_7 | standard 30j | (a) 10 trades paper (hebdo donc ~4 trades sur 30j) → pas suffisant non plus |
| **2026-05-18** | btc_asia_mes_leadlag_q70_v80 | standard 30j | (a) data pipeline BTCUSDT_1h cron fix (b) short variant pour Binance France |
| **2026-05-18** | mcl_overnight_mon_trend10 | standard 30j | (a) data freshness MCL pipeline fix (b) friday_trigger re-WF |
| **2026-05-18** | eu_relmom_40_3 | standard 30j | (a) solution shorts EU indices (CFD ou futures mini) |

## Gaps bloquants (infra) par stratégie

### `mib_estx50_spread` — S-grade le + proche du live
- **Blocking** : besoin de EUR 13.5K margin pour 1 FIB + 3 FESX, vs EUR 9.9K
  dispo IBKR aujourd'hui. **Gap : -EUR 3.6K.**
- Option: wait pour capital additionnel, OU réduire size (0.5 FIB + 1.5 FESX)

### `btc_asia_mes_leadlag_q70_v80` — n_obs court pénalise S-grade
- BTCUSDT_1h.parquet stale ~20j (cron Binance à fixer)
- Mode "both" = long+short → Binance France ne supporte pas short spot
  facilement. **Alternative : long_only q80_v80** (Sharpe +1.08, 25 active days).
  Re-classify as candidate distinct.

### `mcl_overnight_mon_trend10`
- MCL_1D.parquet stale observé 2026-04-18
- Trigger shift vendredi au lieu de lundi pour capter gap weekend → nécessite
  **re-WF friday_trigger** avant promotion live

### `alt_rel_strength_14_60_7`
- Trade **weekly** (rebalance dimanche 01h UTC) → 4 trades sur 30j
- **Impossible d'avoir 10 trades paper en 30j avec une stratégie hebdo**.
  Fast-track 5 trades nécessaire mais grade B donc bloqué.
- Recommendation : **relâcher MIN_PAPER_TRADES à 5** pour les strats weekly
  quand grade = A.

### `mes_pre_holiday_long`
- **Rare**: pre-holidays NYSE ≈ 8-10 par an → 0-1 trade sur 30j typique.
- **Impossible d'atteindre 10 trades en 30j**. Nécessite 12 mois paper pour
  avoir statistical power.
- Recommendation : **extended paper 365j** avant live pour rare-signal strats.

## Actions opérateur recommandées

1. **Attendre 14j paper minimum** sur mib_estx50_spread (jusqu'au 2026-05-02)
   puis **décider** si EUR 13.5K margin ou size réduite
2. **Fixer les data pipelines** stale (BTCUSDT_1h, MCL_1D) — déblocage
   généralisé
3. **Écrire les variantes long-only** pour btc_asia_mes_leadlag_q70_v80 (Binance
   France contrainte)
4. **Accepter la réalité statistique** : le scoring DSR dit que les strats issues
   de batch multi-variants ne sont pas bulletproof — continuer paper pour
   accumuler n_obs **ou** redesigner les WF avec n_trials=1 (1 hypothèse
   pré-enregistrée, pas un batch fishing)
5. **Ajouter tier "C-grade" pour strats rares** (pre-holiday, weekly-rebalance)
   avec seuil MIN_PAPER_TRADES=3 + période 90j minimum

## Verdict rentabilité

**Réalisation** : si on respecte le promotion_gate strict (ce qui est le
design actuel), **zero nouvelle strat live avant mi-mai 2026** au plus tôt,
avec une vraie chance seulement pour **mib_estx50_spread** (si capital).

**Opportunité** : les 2 live_core (CAM + GOR) sur $9.9K IBKR ont bien un
budget risque $1000 (10%), donc occupancy 10% pas 5%. Le capital **travaille
déjà** sur le compte IBKR. Le vrai capital qui dort est **$8.7K Binance**
(0 live post-drain).

**Recommandation finale** :
- **Ne pas forcer de promotion ce mois-ci** — respecter le gate, c'est la
  discipline du système
- **Continuer à accumuler paper track record** pour les 9 candidates
- **Activer mib_estx50_spread le 2026-05-02** si les conditions (margin,
  5 trades paper, greenlight) sont remplies → une strat S-grade de plus,
  Sharpe 3.91 backtest
- **Prouver rentabilité sur les 2 live_core actuelles** — c'est le seul
  track record qui compte. Si Sharpe live 6 mois < 0.3, le problème n'est
  pas le nombre de strats, c'est l'edge lui-même

## Commandes prêtes à exécuter

```bash
# À partir du 2026-05-02, tester fast-track S-grade sur mib_estx50_spread:
python scripts/promotion_check.py mib_estx50_spread --fast-track

# Puis si PASS:
python scripts/promotion_check.py mib_estx50_spread \\
    --grant-greenlight=live_probation --signer=marc \\
    --note="fast-track S-grade, 14j paper clean, margin dispo EUR 13.5K"

# À partir du 2026-05-16, tester standard sur les B-grade:
python scripts/promotion_check.py mes_pre_holiday_long
```
