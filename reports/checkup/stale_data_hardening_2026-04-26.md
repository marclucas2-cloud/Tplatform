# Mission 1 — Hardening stale-data 2026-04-26

**Agent** : Claude Opus
**Trigger** : pipeline daily fix 2026-04-25 (commit 4e16158) — bug latent 3 semaines.
**Objectif** : empêcher qu'un bug "datetime legacy / stale silencieuse" survive 3 semaines sans alerte.

## Ce qui a été ajouté

### 1. Helper canonique `core/data/parquet_safe_loader.py`

Single source of truth pour charger un *_1D.parquet sans tomber dans le piège datetime legacy.
- `load_daily_parquet_safe(path)` : préserve DatetimeIndex valide, drop colonne `datetime` legacy, dedupe + sort + strip tz
- `parquet_content_age_days(path)` : âge en jours de la dernière bar visible via le safe loader (différent du mtime)

`core/worker/cycles/futures_runner.py:_load_futures_daily_frame` est désormais un wrapper conservatif qui délègue au helper canonique. Aucun changement de comportement runtime.

### 2. Preflight content freshness check

`core/runtime/preflight.py` ajoute `_check_parquet_content_freshness` :
- Charge le parquet via `parquet_safe_loader`
- Calcule l'âge de la dernière bar **visible** (pas le mtime fichier)
- Severity = **critical** si > seuil (4 jours pour live, 10 jours pour paper)

Différent du check existant `_check_parquet_freshness` qui base sur `path.stat().st_mtime` :
- mtime fresh + content stale = bug observé en avril (fichier réécrit chaque cron mais bars frozen via NaT)
- L'ancien check passait au vert sur ce bug ; le nouveau l'attrape

`boot_preflight` emit maintenant un `data_content::<sym>` par parquet critique.

Couverture étendue : MES, MES_LONG, MNQ, M2K, MGC, MCL, **VIX_1D** (manquait dans l'ancienne liste).

### 3. Tests non-régression `tests/test_stale_data_hardening_2026_04_26.py`

6 tests couvrant :
- `test_no_toxic_datetime_legacy_pattern_in_runtime` — scan source-level. Si un fichier runtime (worker.py, core/worker/, core/broker/, core/runtime/, strategies_v2/) utilise `df.index = pd.to_datetime(df["datetime"])` sans avoir d'abord un guard `isinstance(df.index, pd.DatetimeIndex) and df.index.notna()`, le test fail.
- `test_safe_loader_preserves_valid_datetimeindex` — corruption typique (datetime=NaT pour bars récents) → loader préserve l'index correct
- `test_safe_loader_falls_back_to_datetime_col_when_index_invalid` — comportement legacy préservé si vraiment besoin
- `test_parquet_content_age_days` — sanity sur l'helper d'âge
- `test_preflight_content_freshness_flags_corrupted_parquet` — le check preflight remonte severity=critical sur stale 30 jours
- `test_preflight_content_check_in_boot_preflight` — `boot_preflight` produit bien des checks `data_content::*`

**Résultat** : 6/6 pass + regression 3875 pass / 1 skip / 0 fail.

## Quels chemins étaient vulnérables (avant fix)

### Chemin runtime principal (corrigé)

`core/worker/cycles/futures_runner.py` ligne 192-195 (avant 4e16158) :
```python
if "datetime" in df.columns:
    df.index = pd.to_datetime(df["datetime"])  # NaT pour bars récents
df = df[df.index.notna()]                       # drop bars récents
```

C'est ce chemin qui a corrompu silencieusement toutes les sleeves desk pendant 3 semaines.

### Scripts research vulnérables (non bloqués)

Pattern toxique encore présent (mais non-runtime) dans :

1. `scripts/bt_cam_trailing_compare.py:58` — backtest CAM, utilisé manuellement en research
2. `scripts/wf_bucket_c_residuel.py:39` — WF research
3. `scripts/research/simulate_futures_today.py:26` — simulation research
4. `scripts/wf_futures_timeframe_test.py:25` — WF research
5. `scripts/wf_futures_all.py:23` — WF research

**Décision** : pas corrigés cette mission, par discipline.
- Ce sont des scripts manuels, pas du runtime
- Leurs résultats peuvent être biaisés sur futures données fraîches actuelles, mais comme ils sont lancés à la main, l'opérateur peut s'en rendre compte
- Mission 1 priorisait l'observabilité runtime + verrouillage source-level, pas refactor en cascade
- Recommandation : si un de ces scripts redevient lourdement utilisé, ajouter un import du helper canonique (3 lignes de modif).

Le test source-level `test_no_toxic_datetime_legacy_pattern_in_runtime` ne scanne PAS ces dossiers (`scripts/`, `tests/`). Il scanne uniquement les chemins runtime. Donc :
- Ils peuvent rester avec le pattern toxique (research, manuel)
- MAIS si quelqu'un copie ce pattern dans un fichier runtime → test fail immédiat

### Cas non vulnérable car déjà guardé

`scripts/refresh_macro_top1_etfs.py` (ajouté 0080865) utilise déjà la même hygiène que le helper canonique. Pas le même pattern toxique.

`scripts/refresh_futures_parquet.py` (modifié 4e16158) idem — patché en même temps.

## Ce qui est maintenant détecté automatiquement

| Cas | Détecté par | Severity |
|---|---|---|
| Fichier runtime ajoute pattern toxique `df.index = pd.to_datetime(df["datetime"])` | test source-level | test fail (pre-merge) |
| `*_1D.parquet` corrompu (last bar > 4j en live, > 10j en paper) | preflight `data_content::<sym>` | critical |
| `*_1D.parquet` mtime stale (> 48h en live) | preflight `data::<sym>` (existant) | warning |
| Parquet absent | preflight `data::<sym>` | warning |
| `boot_preflight` ne produit aucun `data_content::*` (régression) | test `test_preflight_content_check_in_boot_preflight` | test fail |

## Comportement runtime au prochain boot worker

À chaque restart du worker, `boot_preflight` va maintenant émettre 7 checks `data_content::<sym>` :
- MES_1D, MES_LONG, MNQ_1D, M2K_1D, MGC_1D, MCL_1D, VIX_1D

Si l'un d'eux est stale (last bar > 4 jours), severity=critical. En mode `fail_closed=False` (advisory), c'est loggué + remonté dans `runtime_audit --strict`. En mode `fail_closed=True`, le boot s'arrête.

Sur le VPS actuel, `boot_preflight(fail_closed=False)` est utilisé (advisory). Le check va remonter visuellement dans les logs. **Recommandation** (hors scope mission 1) : faire qu'au moins 1 sleeve live (CAM ou GOR) demande `fail_closed=True` sur ses parquets canoniques. Pas fait cette mission, pour ne pas changer le comportement de boot juste avant lundi.

## Ce qui reste hors scope (à ne pas oublier)

1. **Refactor des 5 scripts research vulnérables** — petit, sûr, mais pas urgent. Si un script research redevient pivot, on l'importe au helper canonique en 3 lignes.

2. **Décision `fail_closed` au boot worker** sur les parquets critiques — change le comportement de boot, à arbitrer hors mission stale-data.

3. **Audit rétroactif content de tous les parquets non daily** (4h, 5min, etc.) — le bug ciblait les `*_1D.parquet` qui avaient une colonne legacy `datetime`. Les autres (`*_4h.parquet`, etc.) n'ont pas été audités. Probablement pas affectés (yfinance pas la source unique pour les intraday) mais à vérifier.

4. **Alerting opérationnel** — Telegram / e-mail si content_freshness échoue. Aujourd'hui c'est juste loggué. À programmer si récurrent.

## Vérification end-to-end

Prochain boot worker (lundi matin avec restart) :
```
[boot_preflight]
  data_content::MES_1D    : passed   "MES_1D.parquet content fresh (last bar 2d old)"
  data_content::MNQ_1D    : passed   ...
  data_content::MGC_1D    : passed   ...
  data_content::MCL_1D    : passed   ...
  data_content::VIX_1D    : passed   ...
```

Si un parquet redevient stale (par exemple si yfinance fail 5 jours d'affilée) → ligne :
```
  data_content::MES_1D    : critical "MES_1D.parquet CONTENT STALE: last visible bar 6d old (max 4d for MES_1D). File mtime can be fresh while bars are frozen — safe loader sees frozen content. Audit refresh script + datetime column."
```

C'est cette ligne qui aurait évité 3 semaines de stale silencieuse en avril.

## Verdict mission 1

- ✅ Runtime non-régression : pattern toxique banni par test source-level
- ✅ Détection auto : `data_content::*` en preflight, severity critical
- ✅ Helper canonique : 1 source unique, déjà adopté par futures_runner
- ⚠️ Documentation : 5 scripts research restent vulnérables, listés
- 🔒 0 changement de logique de décision sleeve, 0 changement de schedule, 0 risque sur les cycles de lundi
