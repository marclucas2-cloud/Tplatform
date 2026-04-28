# Live Risk + Paper SL Fix — 2026-04-28

## Scope

Cette passe ferme deux P1 runtime distincts :

1. faux positif `DAILY_LOSS -11.39%` du kill switch live
2. faux `position gone` sur les futures paper après fill

## 1. Cause racine du faux kill switch live

Le problème n'était pas dans `core/kill_switch_live.py`.

Le vrai bug était dans `worker.py:run_live_risk_cycle()` :

- en cas d'échec temporaire du snapshot IBKR, le cycle conservait
  `config/limits_live.yaml -> capital: 10000`
- ce nominal de config était ensuite traité comme une vraie equity live
- face à un baseline journalier réel autour de `$11.3k`, cela fabrique un
  faux drawdown proche de `-11.39%`

Exemple :

- baseline réel : `$11,283`
- fallback config : `$10,000`
- pseudo-DD : `(10000 - 11283) / 11283 = -11.37%`

Le kill switch était donc armable sur une panne snapshot, pas sur une vraie
perte économique.

## 2. Fix appliqué

`worker.py`

- ajout de `_load_cached_live_equity_snapshot(max_age_minutes=30)`
- fallback vers `data/state/ibkr_futures/equity_state.json` ou
  `data/state/ibkr_equity.json` uniquement si le snapshot est frais
- si aucun snapshot fiable n'est disponible :
  - skip des checks numériques live
  - event log explicite
  - plus aucun faux trigger sur le capital config
- logging explicite du triplet :
  - source snapshot (`api` / `cache`)
  - equity
  - baseline
  - daily PnL

## 3. Cause racine du bug paper futures

Le bloc `FUTURES SL CHECK` dans `worker.py` lisait :

- `futures_positions_live.json`
- `futures_positions_paper.json`

mais ne se connectait qu'au :

- port live `4002`

Conséquence :

- les fills paper du port `4003` étaient invisibles
- le check concluait `position gone`
- les positions paper étaient supprimées du state 1 à 2 minutes après fill

## 4. Fix appliqué

`worker.py`

- routage explicite par mode :
  - `live -> IBKR_PORT / 4002`
  - `paper -> IBKR_PAPER_PORT / 4003`
- traitement state-file par mode, sans mélange live/paper
- usage de `_make_future_contract()` au lieu du hardcode `exchange="CME"`
  pour éviter un second bug latent sur `MCL/MGC`

## 5. Validation

Tests ciblés :

- `tests/test_worker_live_risk_snapshot_guard_2026_04_28.py`
- `tests/test_worker_futures_sl_check_ports_2026_04_28.py`

Suites vérifiées :

- `12 passed` ciblés nouveaux
- `115 passed` sur blocs proches worker/risk/futures
- suite complète : `3900 passed, 1 skipped`

## 6. Conséquence opérationnelle

Après déploiement :

- un échec temporaire du snapshot IBKR ne doit plus armer le kill switch live
  sur un faux `-11%`
- les sleeves futures paper ne doivent plus perdre leur track record juste
  parce que le check SL lisait le mauvais port

## 7. Ce que le fix ne fait pas

- il ne reset pas le kill switch à lui seul
- il ne traite pas la dette analytique `CL=F -> MCLZ6`
- il ne commit pas les modifs trailing stop `BacktesterV2`

## Verdict

Le faux positif kill switch live est corrigé à la source la plus plausible,
et le bug paper `position gone` est corrigé structurellement.

La prochaine étape raisonnable, après déploiement, est :

1. vérifier un cycle live risk avec snapshot `api` ou `cache`
2. vérifier qu'un fill futures paper reste bien en state
3. seulement ensuite réouvrir la décision de reset kill switch
