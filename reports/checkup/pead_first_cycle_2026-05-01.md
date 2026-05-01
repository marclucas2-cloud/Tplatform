# PEAD premier cycle paper — 2026-05-01

## Verdict initial : **CYCLE_FAILED**
## Verdict apres fix : **CYCLE_OK_NO_SIGNAL** (mais cause structurelle a adresser)

## Timeline

- **2026-04-30 20:30:26 UTC** (= 22:30 Paris) : premier cycle PEAD declenche par worker
- **2026-04-30 20:30:26 UTC** : `[ERROR] PEAD price load failed: Price cache missing: /opt/trading-platform/data/us_research/sp500_prices_cache.parquet`
- Journal genere : 1 event `cycle_error` avec reason=price_load_failed
- State NON cree (le cycle plante avant la persistance de state.json)
- **2026-05-01 06:07:30 UTC** : checkup matinal -> diagnostic
- **2026-05-01 06:07:52 UTC** : SCP du cache prix local + re-run manuel cycle -> OK, state cree, 0 entry

## Cause racine
Lors du cablage runtime de PEAD le 30/04 (commit 561a27d), j'ai SCP `earnings_history.parquet` mais oublie `sp500_prices_cache.parquet`. Le runner a 2 dependances data, j'en ai cable 1.

## Fix immediat (applique)
SCP `data/us_research/sp500_prices_cache.parquet` (469KB) vers VPS.
Re-run manuel : cycle OK, state cree (`cycle_count=1`, `last_cycle_utc=2026-05-01T06:07:52Z`), 0 entry, 0 erreur.

## Cause secondaire — 0 entries au cycle manuel
- Cache prix local date du **16 avril** : last_close ne va pas jusqu'a fin avril
- Fenetre `[J-2, J]` du runner = [29 avril, 1er mai] : les earnings GOOGL/META (28 avril) sont en dehors
- Seule LLY (30 avril, surprise +27%) tombe dedans, mais le gap calcule sur prix stale (16 avril) ne reflete pas la realite

## Actions recommandees

1. **Refresh cache prix sp500** : ajouter un cron yfinance daily comme pour les futures.
   Sans ca, le runner travaille sur prix stales et rate les signaux courants.
   Script existant : `scripts/research/_alpaca_discovery_pead_2026-04-30.py` recharge le cache mais
   n'est pas planifie. Soit le rendre cron-callable, soit ecrire un mini-script
   `scripts/refresh_us_prices.py` daily 21h Paris (avant cycle PEAD 22h30).

2. **Elargir window_days** dans `get_recent_earnings()` : actuellement `EARNINGS_WINDOW_DAYS=2`.
   Passer a 3-5 jours pour capturer earnings publiees AMC vendredi -> entry lundi.
   Modif minimale dans `pead_runner.py` ligne 53.

3. **Test smoke daily** : ajouter un test qui valide que les 2 caches data
   (`sp500_prices_cache.parquet`, `earnings_history.parquet`) ont mtime < 24h.
   Sans ca, on peut tourner 1 mois en silencieux sans realiser que les data sont stales.

## State actuel post-fix
```json
{
  "active_positions": {},
  "last_cycle_utc": "2026-05-01T06:07:52.165186+00:00",
  "cycle_count": 1
}
```

## Prochaine fenetre d'observation
Cycle PEAD weekday 22:30 Paris (= ~20:30 UTC). Demain 2 mai vendredi -> cycle tournera.
Mais sans refresh du cache prix, restera 0 entry. Action 1 (cron prices) a faire AVANT
le prochain cycle pour que le test soit valide.
