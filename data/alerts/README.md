# data/alerts/

**Producer** : worker alert pipeline (`core/worker/alerts.py`, `_send_alert`, `_log_event`).
**Consumer** : Telegram fallback JSONL, post-mortem review manuel.
**Criticity** : low (runtime convenience, alternative Telegram V2 primary).
**Tolerance absence** : OK (repo-local dev env peut manquer).
**Gitignore** : contenu `*.jsonl` auto-genere ignore. Seul ce README versionne.

Produit typiquement `alerts.jsonl` — append-only log des alertes emises.
Pour partage d'audit historique, preferer les post-mortems synthetiques
dans `docs/audit/` ou `reports/review/`.
