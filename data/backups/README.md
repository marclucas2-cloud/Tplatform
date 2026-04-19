# data/backups/

**Producer** : `scripts/backup_state.py` + cron `scripts/backup.sh` (VPS daily 03:00 UTC).
**Consumer** : recovery manuel post-incident.
**Criticity** : high (backups critiques) mais **re-produit par cron**.
**Tolerance absence** : OK en dev. Sur VPS, surveiller cron log.
**Gitignore** : contenu (snapshots dates, tar.gz) ignore. Seul ce README versionne.

Structure typique : `data/backups/YYYY-MM-DD/` contient snapshots state files
+ parquets critiques. Retention 30 jours par defaut (cf backup.sh).

NE PAS commiter de contenu. Backups hors-site = hors scope (directive user).
