# Executive Summary — Research autonome 2026-04-23

## TL;DR
**1 nouveau candidat paper recommandé** sur 10 ideas testées. Runtime wiring laissé à Marc.

## Meilleure stratégie trouvée
**`mes_mr_vix_spike`** (NEW)
- MES long après 3 down-days + VIX > 15, hold 4j, SL 25pts
- **Sharpe 0.72, WF 5/5 (ratio 1.00), DD -9.7%, 12 trades/an, 5.2Y backtest**
- Corrélation CAM 0.055, GOR -0.014 (**quasi-orthogonale**)
- Status paper_only 2026-04-23, earliest live_micro 2026-05-23

## Meilleure réhabilitation possible
Aucune. Deux sleeves paper existantes (`mes_monday_long_oc`, `mes_wednesday_long_oc`) échouent mon backtest simplifié 11Y (Sharpe -0.03 / -0.15) → **re-audit avec filtres intraday du code original recommandé** avant de juger grade B valide ou pas.

## Pire illusion rejetée
**`mes_3day_stretch` naïf sans filtre VIX** : thèse académique solide (post-stretch MR) mais Sharpe -0.24 sur 11Y. Confirme que la MR naïve sur indices n'a PAS d'edge brut sans filtre régime. Seul l'ajout VIX > 15 transforme en stratégie viable.

## Prochaine action desk
**Court terme (Marc)** :
1. Décider si câbler `mes_mr_vix_spike` runtime (dossier complet livré : code + WF manifest + tests 9/9 + doc + registry paper_only)
2. Si oui : ajouter call site dans `worker.py:_run_futures_cycle` + observer 30j paper

**Ce mois** :
- Re-audit `mes_monday_long_oc` + `mes_wednesday_long_oc` avec filtres intraday
- Pas de nouvelle exploration avant que v1 paper donne un verdict

## Livrables
- **1 strat new paper** : `strategies_v2/futures/mes_mr_vix_spike.py` + manifest WF + registry + whitelist + doc + 9 tests
- **4 scripts research** : backtest rounds 1+2, sensitivity 48 configs, comparaison vs paper existants
- **3 reports** : rapport principal 200+ lignes, executive summary, metrics JSON/parquet
- **0 risque live ajouté** (paper only, runtime non câblé)

## Score final candidat retenu
41/50 sur framework (Décorrélation 9, Tradabilité 9, ROC 6, Robustesse 9, Utilité 8).
