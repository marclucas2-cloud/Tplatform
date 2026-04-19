# data/reconciliation/

**Producer** : `core/governance/reconciliation_cycle.py` — snapshots par book par date.
**Consumer** : post-mortem reconciliation divergences, debug.
**Criticity** : medium (debug ops, non-critique live).
**Tolerance absence** : OK en dev. Attendu sur VPS quand le cycle tourne.
**Gitignore** : contenu `*.json` auto-genere ignore. Seul ce README versionne.

Structure typique : `{book}_YYYY-MM-DD.json` (ex `binance_crypto_2026-04-19.json`).
Contient divergences broker vs local : `only_in_broker`, `only_in_local`, severity.

Pour partage historique audit, preferer `docs/audit/reconciliation_issues.md`
synthese manuelle des divergences significatives.
