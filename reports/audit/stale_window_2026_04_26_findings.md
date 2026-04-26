# Audit Rétroactif — Fenêtre Stale 2026-03-27 → 2026-04-24

**Agent** : Claude Opus
**Trigger** : pipeline daily fix 2026-04-25 (commit 4e16158) — bug latent depuis ~2026-03-27 affectant 4 parquets futures + VIX hors cron.

## Question audit

Pour chaque jour ouvré dans la fenêtre stale, qu'auraient décidé les sleeves desk SI elles avaient vu data fresh, vs ce qu'elles ont décidé sur data stale ?

## Méthode

- Reproduire la "vue stale" en cappant chaque parquet à sa dernière date "non-corrompue" (selon disque pré-fix) :
  - MES/MNQ : last index visible = 2026-04-08
  - MGC/MCL : last index visible = 2026-03-27
  - VIX : last refresh manuel = 2026-04-09 (hors cron)
  - M2K : aucune corruption (pas de col `datetime`)
- Recompute les signaux sur "vue stale" vs "vue fresh"
- 21 business days dans la fenêtre

## Résultats

### Cross-Asset Momentum (CAM, live_core)

**11/21 jours divergents (52.4%)**

Univers MES/MNQ/M2K/MGC/MCL, top-1 momentum 20d, seuil min +2%.

| Période | Stale pick | Fresh pick | Note |
|---|---|---|---|
| 2026-03-31 → 2026-04-08 (8 jours) | **None** (pas de signal, momentum < 2%) | **MCL** | CAM aurait dû picker MCL, n'a rien pické |
| 2026-04-09 → 2026-04-10 (2 jours) | M2K | MCL | CAM pické M2K alors que MCL avait meilleur mom |
| 2026-04-23 → ... | M2K | MNQ | divergence sur fin de fenêtre |

**Impact réel** : CAM avait une position MCL ouverte courant avril, TP +$605 le 19/04. Donc le trade MCL a bien eu lieu, mais peut-être à un moment sub-optimal. Les 8 premiers jours où CAM stale = None sont les plus inquiétants — CAM ratait potentiellement des entries qu'elle aurait dû prendre.

### Gold-Oil Rotation (GOR, live_core)

**13/21 jours divergents (61.9%)**

Rotation MGC/MCL sur momentum 20d.

| Période | Stale pick | Fresh pick |
|---|---|---|
| 2026-03-31 → 2026-04-13 (10+ jours) | **MGC (figé)** | **MCL** |

**Impact réel** : GOR registry note "live_core depuis 2026-04-08, signal dormant". Si GOR n'a pas émis de fill dans la fenêtre (signal dormant = position figée), l'impact PnL réel est nul mais **le signal de rotation aurait dû se déclencher** (passer MGC → MCL). À vérifier dans state file `data/state/futures_positions_live.json` si GOR a eu une position MGC ouverte qui aurait dû flip vers MCL.

### gold_trend_mgc (paper)

**10/21 jours divergents (47.6%)**

Long MGC si close > EMA20.

| Période | Stale | Fresh |
|---|---|---|
| 2026-04-02 → 2026-04-21 (10 jours) | False (pas long) | True (long signal) |

**Impact réel** : sleeve paper. MGC a trended up dans la fenêtre. La sleeve aurait émis 10 jours de signaux long sur paper, manqués. **Pas de PnL réel impacté**, mais 10 jours d'observabilité paper perdue.

## Verdict par sleeve

### CAM (live_core)
- **Position fill réel** : MCL TP +$605 le 19/04 — semble cohérent même avec data partielle (M2K fresh + autres stale, M2K probablement pas top mom).
- **Risque incident** : 8 jours de "no signal" alors que fresh aurait dit MCL. Si CAM aurait pické MCL plus tôt, le trade aurait pu être différent (entry plus haute ? plus basse ?).
- **À vérifier** : timing exact du fill MCL. Entry + TP dates.

### GOR (live_core)
- **Position fill réel** : signal dormant (per registry).
- **Risque incident** : **13 jours où GOR aurait dû switch MGC → MCL mais ne l'a pas fait**. Si GOR avait une position MGC ouverte qui valait moins que MCL aurait valu, c'est un manque à gagner réel. À mesurer.
- **À vérifier** : state file live, position GOR active dans la fenêtre.

### gold_trend_mgc (paper)
- Impact paper seulement. 10 jours de long signaux manqués.
- **Note** : la sleeve a aussi le caveat "paper_only" en attente de promotion. Cet audit confirme qu'elle aurait été plus active sur data fresh, ce qui pourrait changer son grade en re-WF.

## Bonus — autres sleeves non auditées en détail

- `mcl_overnight_mon_trend10` (paper) : utilise MCL (stale 28j). Probablement même type de divergence. Non audité ici car cadence weekly + impact paper.
- `mes_monday/wednesday_long_oc` (paper) : utilise MES (stale 16j). Calendar effect, signal less dependent on momentum, mais impacté quand même.
- `mes_mr_vix_spike` (paper) : câblé seulement vendredi 24/04 → 1 cycle stale, pas dans la fenêtre auditée.

## Recommandations

### Immédiat (lundi, déjà prévu)
- Vérifier le cycle 14h UTC sur data fresh pour les 4 sleeves desk
- Confirmer journaux montrent dates fraîches cohérentes

### Court terme (cette semaine)
- **Audit fill réel CAM** : récupérer timing exact entry/TP MCL pour évaluer si timing aurait été différent sur fresh
- **Audit position GOR** : si MGC ouvert pendant la fenêtre, mesurer manque à gagner vs MCL aurait apporté
- **Re-WF gold_trend_mgc** : la stratégie a un grade A registry mais avec 10 jours de signaux manqués, il faut vérifier que le grade tient sur l'historique fresh (probablement oui, le bug est récent vs l'historique 11Y)

### Long terme (defensif)
- **Monitoring stale data** : ajouter un check au démarrage du worker qui flag toute parquet avec last_index > N jours d'âge. Le bug a duré 3 semaines sans alerte.
- **Test de non-regression** : ajouter un test au repo qui vérifie que les parquets _1D ne contiennent pas de colonne `datetime` corrompue.

## Conclusion honnête

**Le bug pipeline n'était pas anodin.** 52-62% des décisions live (CAM/GOR) divergent entre stale et fresh sur la fenêtre. Heureusement :
1. CAM avait quand même fill MCL (peut-être par chance ou parce que le bug n'invalidait pas tous les calculs)
2. GOR était signal dormant, pas de fill mauvais
3. Les paper sleeves ont juste perdu de l'observabilité

**Le PnL réel direct est probablement faible** mais l'épisode souligne deux choses :
- Le desk a tourné en mode "stale silencieux" sans alerte pendant 3+ semaines
- La défense en profondeur (guard freshness sleeve + monitoring stale) doit être ajoutée à toutes les sleeves data-dépendantes

## Artefacts

- Script audit : `scripts/_audit_stale_window_2026_04_26.py`
- JSON divergences : `reports/audit/stale_window_2026_04_26_divergences.json`
- Ce rapport
