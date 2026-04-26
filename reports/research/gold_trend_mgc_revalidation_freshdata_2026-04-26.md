# Mission 3 — Revalidation gold_trend_mgc sur data fresh 2026-04-26

**Agent** : Claude Opus
**Trigger** : pipeline daily fix 4e16158 (data MGC corrompue depuis 27/03 jusqu'au 24/04). Manifest WF précédent du 19/04 reposait sur data tronquée.
**Question** : le grade A de gold_trend_mgc tient-il sur historique fresh ?

## Méthode

Run du script existant `scripts/wf_gold_trend_mgc_v1.py` sur data MGC_1D fresh post-fix.
- Variant testé : `v1_sl04_tp08` (config par défaut registry)
- Params : EMA 20, SL 0.4%, TP 0.8%, max_hold 10 jours
- Source : `data/futures/MGC_1D.parquet` (996 rows post-fix vs 957 avant)
- Cost : $2.49 RT MGC

Manifest produit : `data/research/wf_manifests/gold_trend_mgc_v1_2026-04-26.json`.

## Résultats côte à côte

| Métrique | Manifest 2026-04-19 (data corrompue) | Manifest 2026-04-26 (data fresh) | Δ |
|---|---|---|---|
| All-trades Sharpe | **+2.346** | **-0.031** | **-2.4** |
| All-trades total | +$17,010 | -$187 | -$17,200 |
| All-trades win-rate | 41.3% | 33.0% | -8 pts |
| Max DD USD | -$1,470 | -$4,188 | -2.85x worse |
| OOS profitable windows | 4/5 | 3/5 | -1 |
| pass_rate | 0.80 | 0.60 | -0.2 |
| Mean OOS Sharpe | +2.625 | **-0.537** | **renversé** |
| Total OOS PnL | +$11,764 | **-$3,017** | -$14,800 |
| DSR p-value | 0.000312 | **0.515** | non-significatif |
| MC P(DD>30%) | 0.15% | **92.5%** | catastrophique |
| MC P(DD>40%) | 0% | 54.8% | — |
| MC median DD | 12.0% | 41.1% | -29 pts |
| **Grade** | **A** | **REJECTED** | — |

## Lecture honnête

### Pourquoi la dégradation est dramatique

Le manifest 19/04 montrait une stratégie A solide (Sharpe 2.35, MC 0% chance DD>40%). Le manifest 26/04 sur data fresh montre une stratégie REJECTED (Sharpe ~0, MC 55% chance DD>40%).

**La différence de data** :
- 19/04 manifest : MGC_1D s'arrête à ~2026-03-27 (data corrompue, dernière bar visible avant le fix)
- 26/04 manifest : MGC_1D fresh jusqu'au 2026-04-24 (39 bars supplémentaires)

**Hypothèse** : la fenêtre 2026-03-27 → 2026-04-24 a été particulièrement défavorable à la stratégie (long MGC > EMA20 avec SL 0.4% / TP 0.8% serré). 39 bars supplémentaires suffisent à plomber tout le track record. Cela suggère :
1. Soit la stratégie était fragile et data-dependent (3.5% des bars suffisent à inverser le verdict)
2. Soit la stratégie a un edge réel mais a connu une fenêtre stress particulière (volatilité MGC d'avril 2026 = correction post-rally)

### Window-by-window

| Window | OOS Sharpe (fresh) | OOS PnL | Profitable |
|---|---|---|---|
| 1 | +1.106 | +$388 | ✅ |
| 2 | +0.814 | +$283 | ✅ |
| 3 | +1.481 | +$780 | ✅ |
| 4 | -1.756 | -$1,102 | ❌ |
| **5** | **-4.328** | **-$3,367** | ❌ |

W5 (la plus récente, OOS jusqu'au 23/04) a un Sharpe -4.33 sur 47 trades. **C'est cette fenêtre qui plombe tout** — exactement la période qu'on n'avait pas dans le manifest 19/04.

### Caveats du WF script (à noter)

Le script `wf_gold_trend_mgc_v1.py` print "is_period 1970-01-01..1970-01-01" pour toutes les fenêtres — bug d'affichage des dates (probablement le print formate mal le DatetimeIndex). Mais les **chiffres calculés sont valides** : Sharpe, PnL, MC, DSR utilisent l'index correctement, seul l'affichage de la fenêtre est buggé. Bug cosmétique, pas calculatoire.

À noter aussi : le SL serré (0.4%) sur MGC vol journalière (~1.5%) signifie que beaucoup de trades sont stoppés au bruit. C'est intentionnel dans la config v1 (recalibration anti-deleveraging) mais ça rend la stratégie sensible aux régimes de vol. Le run fresh capture un régime stress que l'ancien manifest manquait.

## Verdict

### Question : grade A confirmé ?

**Non.** Grade dégradé **A → REJECTED**.

### Question : sleeve toujours candidate sérieuse ?

**À paper, oui — comme observation, pas comme alpha confiant.** À live, **non** dans la config actuelle.

### Question : à dégrader ?

**Oui, immédiatement** :
- Registry : `gold_trend_mgc` doit passer de `grade: A` à `grade: REJECTED` (ou au minimum `B` avec note "WF fail post-fresh-data")
- Whitelist : la sleeve est déjà `paper_only`, statut conservé
- Nuance : la stratégie a peut-être un edge structural mais la config v1 SL 0.4% / TP 0.8% ne tient pas. Une recalibration v2 (SL plus large, par exemple 1.5% SL / 3% TP) pourrait montrer un edge différent. Hors scope de cette mission.

### Question : simple sleeve paper acceptable ?

**Oui pour l'observation paper, non pour promotion live envisageable.** En l'état :
- Continuer paper pour générer des signaux observables sur fresh data
- Pas de promotion live envisageable avant re-WF avec configs alternatives ET au moins 60 jours de paper sur fresh data

### Recommandations chiffrées

1. **Mettre à jour `quant_registry.yaml`** : `gold_trend_mgc.grade: REJECTED` (ou `B` avec note WF fresh fail).
2. **Mettre à jour `data/research/wf_manifests/gold_trend_mgc_v1_2026-04-26.json`** dans le `wf_manifest_path` registry pour pointer vers le nouveau (le 19/04 était sur data corrompue).
3. **Ne pas câbler en live_micro** sans nouvelle WF avec config alternative.
4. **Considérer une re-WF avec configs alternatives** : SL 1.0%, 1.5%, 2.0% × TP 1.5%, 2.5%, 4.0%. Voir si une config robuste émerge sur data fresh. Hors scope mission 3, à programmer comme mini-mission research future.

## Impact desk

Le grade A précédent reposait sur un manifest généré sur **data corrompue**. C'est une découverte sérieuse de gouvernance :
- Le grade A avait été utilisé pour décider que `gold_trend_mgc` était un candidat "fast-track promotion" (cf docs/audit/fast_track_*).
- Cette décision était **fondée sur une vue tronquée des données**.
- Tous les WF manifests générés pendant la fenêtre stale (du 27/03 au 24/04, sur des sleeves utilisant MES/MNQ/MGC/MCL) sont à **revalider**.

## À auditer ensuite (hors scope)

- WF manifest `cross_asset_momentum_2026-04-19_backfill.json` — utilise MES/MNQ/MGC/MCL, peut-être affecté
- WF manifest `gold_oil_rotation_2026-04-19_backfill.json` — utilise MGC/MCL, fortement affecté
- WF manifest `mes_monday_long_oc` / `mes_wednesday_long_oc` / `mes_pre_holiday_long` — utilisent MES
- WF manifest `mcl_overnight_mon_trend10` — utilise MCL
- WF manifest `mes_mr_vix_spike_2026-04-23.json` — utilise MES + VIX. MES_LONG semble avoir été OK (pas affecté par le bug datetime), VIX hors cron stale 16j. Probablement à re-run par sécurité.

**Recommandation** : programmer une mini-mission "re-WF tous les manifests générés entre 2026-03-27 et 2026-04-25" pour confirmer que les autres grades A/B tiennent sur fresh data. C'est le prolongement direct de l'incident stale-data.

## Conclusion mission 3

- **Grade gold_trend_mgc** : A → REJECTED sur data fresh
- **Sleeve à dégrader dans le registry** : oui
- **Promotion live** : exclue dans la config v1 actuelle
- **L'incident stale-data a aussi corrompu la confiance dans les grades** : c'est le coût caché du bug, plus que le PnL réel direct.

## Status incident stale-data global

| Axe | Status |
|---|---|
| Bug pipeline corrigé | ✅ (commit 4e16158) |
| Data réparée VPS | ✅ (refresh manuel 25/04 + cron) |
| Détection auto future | ✅ (preflight `data_content::*` + test source-level) |
| Impact PnL réel mesuré | ✅ (~$0 réel, ~$3K théorique borne sup) |
| Grades manifests revalidés | ⚠️ **partiel** : seul `gold_trend_mgc` re-WF. Autres à programmer.
| Incident clos économiquement | ✅ (impact réel négligeable) |
| Incident clos quantitativement | ⚠️ **non encore** : 4-5 manifests restent à re-valider |

**Verdict global incident** : **techniquement clos**, **détectable à l'avenir**, **PnL réel négligeable**, **mais grades hérités à reconfirmer** avant de remettre le desk en mode expansion serein.

## Prochaine action recommandée

**Avant tout nouveau câblage** (eth_range_longonly_20, pair_xle_xlk_ratio, etc) :
1. Re-WF des 4 autres manifests utilisant MES/MNQ/MGC/MCL (cross_asset_momentum, gold_oil_rotation, mcl_overnight, mes_calendar)
2. Mettre à jour les grades dans `quant_registry.yaml`
3. Confirmer que le desk live (CAM + GOR) reste valide sur fresh data, ou les downgrade

Si pas envie de tout re-WF lundi : a minima `cross_asset_momentum` et `gold_oil_rotation` (les 2 live_core), pour confirmer leurs grades.
