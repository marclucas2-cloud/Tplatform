# data/audit/

**Producer** : worker + scripts audit (`orders_YYYY-MM-DD.jsonl` append-only).
**Consumer** : reconstruction audit trail orders post-mortem.
**Criticity** : medium (audit trail orders, mais non-critique pour live).
**Tolerance absence** : OK en dev, attendu sur VPS prod.
**Gitignore** : contenu auto-genere ignore. Seul ce README versionne.

Pour synthese humaine, utiliser `docs/audit/*.md` (audit reviews, scorecards).
