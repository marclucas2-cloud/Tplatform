# MidCap Statistical Arbitrage

**Extrait de `temp/` -> `scripts/research/midcap_stat_arb/` iter3-fix hygiene-T1c-B3.**

Stratégie stat arb paires sur midcap SP500. Backtest + scanner + strategy
file + config yaml + test unitaire. Strategy complete, non encore
integrée au worker principal.

## Fichiers
- `midcap_stat_arb_strategy.py` : logique strat (entry/exit signals)
- `midcap_stat_arb_scanner.py` : scanner de paires
- `midcap_stat_arb_backtest.py` : runner backtest
- `midcap_stat_arb.yaml` : config params
- `test_midcap_stat_arb.py` : test unitaire

## Status
Research uniquement. Pas encore canonique (pas dans `quant_registry.yaml`).
Si decision future de promotion : ajouter entry canonique + WF manifest.

## Deplacement 2026-04-19
Ces fichiers etaient dans `temp/` (brouillon hygiene violation). Extraits
vers `scripts/research/midcap_stat_arb/` pour preservation + `temp/` est
desormais gitignore.
