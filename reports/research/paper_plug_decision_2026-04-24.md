# Paper Plug Decision — 2026-04-24

**Agent** : Claude Opus
**Mandat** : review indépendante des travaux target-alpha de ChatGPT, choix d'UNE sleeve à câbler runtime paper, implémentation propre, deploy, verdict honnête.

## TL;DR

- **Sleeve câblée** : `macro_top1_rotation` (long-only rotation monthly sur 8 ETFs macro)
- **Statut** : câblée + prouvée vivante par dry-run VPS (signal_emit DBC top1, journal écrit, state propre)
- **Preuve scheduled** : attendue au cycle naturel 16h30 Paris aujourd'hui
- **Candidats #2/#3** : pair_xle_xlk = dossier gardé chaud ; stock_sector_ls_40_5 = recherche seulement

## Phase 0 — Vérité desk

| Item | Valeur |
|---|---|
| Git | main, 5 commits précédents aujourd'hui (fix TIF, paper wiring mes_mr_vix_spike, research) |
| Tests avant mission | 3840 pass |
| Desk paper ibkr_futures | 5 paper + 1 live_micro hier soir (mes_mr_vix_spike câblé) |
| alpaca_us | us_sector_ls_40_5 paper_only + us_stocks_daily frozen |
| Price cache | `data/research/target_alpha_us_sectors_2026_04_24_prices.parquet` (yfinance 2018-2026, 19 ETFs, 2088 rows) |

## Phase 1 — Review indépendante des 3 candidats ChatGPT

### Méthodologie scan

Script [scripts/research/target_alpha_us_sectors_and_new_assets_2026_04_24.py](../../scripts/research/target_alpha_us_sectors_and_new_assets_2026_04_24.py) relu ligne par ligne. Points vérifiés :

1. **Anti-lookahead** : chaque fonction utilise `pos = signal.shift(1)` — la position active au jour t repose sur signal calculé au close t-1. OK sur toutes les stratégies testées.
2. **Costs** : `ETF_RT_COST_PCT=0.0010` (10 bps RT), `ETF_SHORT_BORROW_DAILY=0.005/252` (~0.5%/an). Raisonnable pour ETFs liquides.
3. **Notionals** : `LEG_NOTIONAL=1000` pour legs L/S, `LONG_ONLY_NOTIONAL=2000` pour long-only. Cohérent.
4. **Survivorship** : les ETFs sectoriels et macros sont stables (SPDR family). Pas de biais ETF.

### Candidat A — `macro_top1_rotation` (priorité 1)

**Signaux propres ?** Oui. `mom = (1+rets).rolling(60).apply(np.prod) - 1` puis `target[t] = top-1 si rebalance day`. `pos = target.shift(1)` — aucun lookahead.

**Rotation exécutable ?** Triviale. 8 ETFs très liquides (SPY/TLT/GLD/DBC/UUP/IEF/HYG/QQQ), long-only, 1 seul ETF détenu à la fois, rebalance monthly (~12/an). Zero short borrow, zero complexity bracket.

**ETFs cohérents ?** Oui. 4 classes d'actifs (equity US, bonds, commodities, FX, credit) = diversification macro propre.

**Score tient-il après relecture ?** Oui + renforcé par sensitivity grid 12 configs que j'ai ajoutée :
  - 9/12 configs Sharpe > 0.5
  - 9/12 configs WF ratio >= 0.80
  - Sweet spot Sharpe : LB=30 HD=10 (Sharpe 0.83, WF 5/5) — écarté pour turnover 2x plus élevé
  - Config retenue : LB=60 HD=21 (ChatGPT) — monthly rebalance cohérent avec académiques momentum 3-12m

**Verdict** : ✅ **PLUG**.

### Candidat B — `pair_xle_xlk_ratio` (priorité 2)

**Spread robuste ?** Sensitivity 16 configs (LB={20,30,45,60} x band={0.5,1.0,1.5,2.0}) :
  - 11/16 Sharpe > 0.3, 14/16 WF >= 0.60
  - Config originale (LB=30, band=1.0) : Sharpe 0.532, WF 5/5 ✓
  - Sweet spot : LB=45 band=0.5 Sharpe 0.682 WF 5/5

**Pair implémentable dans infra actuelle ?** Plus complexe : 2 jambes simultanées (long + short ETF). Alpaca paper accepte les shorts virtuels, mais le runner devrait placer 2 ordres et gérer la fermeture symétrique. Non-trivial vs macro_top1 single-leg.

**Fréquence suffisante ?** Oui. Config originale 366 trade_days sur 2088 = ~18% signal flips. Turnover plus élevé que macro_top1 (97 trade_days).

**Artefact numérique ?** Check : `roll_std.where(roll_std > 1e-12, np.nan)` protège contre division par zéro. `ratio = log(A) - log(B)` stable. Pas d'artefact évident.

**Verdict** : ⚠️ **TIENT la revue mais non retenu ce tour**. macro_top1 supérieur sur Sharpe ET sur simplicité runtime.

### Candidat C — `stock_sector_ls_40_5` (priorité 3, non autorisé sans revue risque)

**Survivorship** : Le panier vient de `load_sector_return_matrix()` dans `backtest_t3b_us_sector_ls.py` → utilise les stocks US du S&P500 **actuels**. Aucune correction pour delistings / survivorship. **Biais documenté, non levé**.

**Complexité runtime** : Panier long = stocks du meilleur secteur GICS, panier short = pire. ~40-50 positions ouvertes simultanément (5-10 par secteur). Complexité d'exécution disproportionnée vs macro_top1 (1 position) ou pair (2 positions).

**Friction shorts** : Borrow fees single-name stocks US non modélisés (le script applique seulement l'ETF borrow rate 0.5%/an, inadéquat pour single-name où le borrow peut atteindre plusieurs %/an sur les noms less-liquid).

**Autre problème** : `trade_days=0` dans le metrics JSON — attribut non propagé depuis `variant_sector_ls`. Pas un bug fatal mais flag qu'une des sorties du pipeline est silencieuse.

**Verdict** : ❌ **NE PAS CÂBLER**. Garder en research seulement. Les 3 objections de Marc (survivorship + construction panier + complexité runtime) ne sont pas levées.

## Phase 2 — Décision

**Règle appliquée** : macro_top1_rotation tient la revue → elle est câblée. Les deux autres ne sont pas touchées.

**Pourquoi macro_top1 gagne** :
1. Sharpe le plus haut (0.676) des candidats propres sur cette étude
2. Long-only → zéro short borrow fiction
3. Monthly rebalance (1 ordre tous les ~21 jours) = cadence disciplinée, observable en paper
4. 8 ETFs tous très liquides, zéro doute sur l'exécution
5. Sensitivity robuste : 9/12 configs en version révisée passent Sharpe>0.5 AND WF>=0.80
6. Anti-lookahead vérifié, mécanisme simple, pas d'artefact

**Pourquoi pair_xle_xlk non choisi** : tient la revue, mais complexité runtime supérieure pour une métrique inférieure (Sharpe 0.53 vs 0.68). Reste dossier #2 gardé chaud si macro_top1 échoue.

**Pourquoi stock_sector_ls non choisi** : les 3 objections Marc (survivorship, panier, complexité) ne sont pas levées par les travaux ChatGPT. Mandate interdit le câblage sans démonstration noir-sur-blanc. Pas de démonstration livrée → pas de câblage.

## Phase 3 — Implémentation paper réelle

### Artefacts créés

| Fichier | Description |
|---|---|
| `strategies_v2/us/macro_top1_rotation.py` | Strategy `MacroTop1Rotation` : `compute_cumret()`, `should_rebalance()`, `decide()` returns `Top1Decision` |
| `core/worker/cycles/macro_top1_rotation_runner.py` | Runner paper : charge prices, state, appelle `decide()`, journal + state |
| `data/research/wf_manifests/macro_top1_rotation_2026-04-24.json` | WF manifest grade B VALIDATED |
| `config/quant_registry.yaml` | Entry alpaca_us paper_only |
| `config/live_whitelist.yaml` | Entry avec runtime_entrypoint réel |
| `data/research/target_alpha_us_sectors_2026_04_24_prices.parquet` | Price cache committé (-f gitignore) |
| `tests/test_macro_top1_rotation_2026_04_24.py` | 17 tests (8 strategy + 3 runner + 6 wiring) |

### Wiring worker.py

```python
from core.worker.cycles.macro_top1_rotation_runner import run_macro_top1_rotation_cycle

# In _runners dict:
"macro_top1_rotation": CycleRunner("macro_top1_rotation", run_macro_top1_rotation_cycle,
                                   alert_callback=_cycle_alert,
                                   metrics_callback=_cycle_metrics_cb,
                                   timeout_seconds=60.0),

# Schedule loop:
if is_weekday() and now_paris.hour == 16 and now_paris.minute >= 30 and not getattr(run_macro_top1_rotation_cycle, '_done_today', False):
    _runners["macro_top1_rotation"].run()
    run_macro_top1_rotation_cycle._done_today = True
if is_weekday() and now_paris.hour < 16:
    run_macro_top1_rotation_cycle._done_today = False
```

Cycle : weekday 16h30 Paris (10h30 ET, 30 min après open US).

### Garde-fous

1. **Zéro ordre broker** — runner 100% simulation locale. Test `test_no_broker_order_call_in_runner` scanne le source pour forbidden imports (`placeOrder`, `submit_order`, `AlpacaClient`, `ibkr_bracket`, `binance`). Pass.
2. **Freshness check** : si `age_days > 7` sur le cache prices → warning log, continue avec stale data (pas de block).
3. **State roundtrip** : state file lu/écrit atomiquement à chaque cycle. Sur corruption → reset to defaults + log warning.
4. **Journal append-only** : JSONL, chaque événement horodaté UTC.
5. **Event taxonomy** : `signal_emit` / `hold` / `no_signal` / `skip_reason`.

### Tests

17/17 pass local + 17/17 pass VPS. Full regression 3860 pass, 1 skip, 0 fail.

## Phase 4 — Déploiement VPS paper-only

### Séquence exécutée

```
git fetch origin && git reset --hard origin/main   # commit 0729ed0 + f0a5d30
pytest tests/test_macro_top1_rotation_2026_04_24.py   # 17/17 pass
# Dry-run manual pour prouver que le runner vit
python -c "from core.worker.cycles.macro_top1_rotation_runner import run_macro_top1_rotation_cycle; run_macro_top1_rotation_cycle()"
# -> state.json + journal.jsonl écrits
# -> event signal_emit target=DBC top3=[DBC(+21.4%), UUP(+4%), QQQ(+3.3%)]
rm -f data/state/macro_top1_rotation/state.json data/state/macro_top1_rotation/journal.jsonl   # clean pour cycle scheduled
systemctl restart trading-worker.service   # active
```

runtime_audit --strict : exit 0 (pas de régression).

## Phase 5 — Validation

### Câblée vs vivante

- **Câblée** : oui. Code, runtime, registry, whitelist, schedule, état initial pur.
- **Vivante par dry-run manuel** : oui. State + journal produits correctement sur VPS.
- **Vivante par cycle scheduled** : **pas encore prouvée**. Le prochain cycle naturel est 16h30 Paris aujourd'hui (~8h). C'est là qu'il faudra vérifier que :
  - `logs/worker/worker.log` contient la ligne `=== MACRO_TOP1_ROTATION PAPER CYCLE ===`
  - `data/state/macro_top1_rotation/journal.jsonl` a une nouvelle entrée avec l'event (`signal_emit` ou `hold`)
  - `data/state/macro_top1_rotation/state.json` reflète le résultat
  - Aucune autre sleeve n'a bougé entre-temps

### Discipline respectée

- UNE seule sleeve câblée (pas 2, pas 3)
- Aucun changement sur mes_estx50_divergence, mgc_mes_ratio_rotation (laissés PENDING_MARC_DECISION)
- Aucun changement sur mes_mr_vix_spike (sleeve câblée hier, cycle naturel à 14h UTC aujourd'hui)
- Pas de nouvelle idée
- Pas de plomberie gratuite
- Zéro risque live ajouté

## Résumé exécutable final

| Candidat | Statut final |
|---|---|
| `macro_top1_rotation` | **câblée paper + prouvée vivante par dry-run VPS** — preuve scheduled attendue 16h30 Paris |
| `pair_xle_xlk_ratio` | dossier gardé chaud (priorité #2 si macro_top1 déraille) |
| `stock_sector_ls_40_5` | research seulement (objections survivorship / panier / complexité non levées) |

## Ce qui reste à vérifier par Marc

Quand tu te connecteras après 16h30 Paris :

```bash
ssh -i ~/.ssh/id_hetzner root@178.104.125.74 '
grep "MACRO_TOP1_ROTATION PAPER" /opt/trading-platform/logs/worker/worker.log | tail -5
cat /opt/trading-platform/data/state/macro_top1_rotation/state.json
tail -3 /opt/trading-platform/data/state/macro_top1_rotation/journal.jsonl
'
```

**Critère de succès** :
- Une ligne `=== MACRO_TOP1_ROTATION PAPER CYCLE ===` aujourd'hui dans le log
- Une entrée journal avec event = `signal_emit` ou `hold`
- Aucune erreur / stacktrace

Si ces 3 checks passent → la sleeve est **vivante**, pas seulement câblée.
Si anomalie → rapport diagnostic + attendre.

Pas d'autre action proposée tant que le cycle scheduled n'a pas tourné.
