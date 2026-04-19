# data/incidents/

**Producer** : `core/monitoring/incident_report.py:log_incident_auto()` (runtime auto-log).
**Consumer** : `scripts/alpaca_go_25k_gate.py` (filter `incidents_open_p0p1`),
dashboard incident widget, post-mortem manuel.
**Criticity** : HIGH (audit trail P0/P1 incidents critiques).
**Tolerance absence** : OK en dev. **Sur VPS : incident tolere (dir vide = 0 incidents)**.
**Gitignore** : contenu `*.jsonl` auto-genere ignore. Seul ce README versionne.

Structure typique : `data/incidents/YYYY-MM-DD.jsonl` append-only JSONL par date
UTC. Chaque ligne = 1 incident avec schema :
```json
{
  "timestamp": "ISO8601+tz",
  "category": "reconciliation|preflight|promotion_gate|...",
  "severity": "P0|P1|CRITICAL|warning",
  "source": "component_name",
  "message": "description",
  "context": {"book": "...", "symbols": [...], ...}
}
```

**Politique audit trail partage** :
- Les JSONL bruts NE sont PAS versionnes (volume + potentiel leak positions/symbols).
- Pour partage historique, ecrire post-mortems synthetiques `docs/audit/post_mortems/YYYY-MM-DD_incident.md`.
- La **structure du dir** (ce README) est versionnee pour que tout environnement sache qu'il doit exister.

**Consommation par gate Alpaca** : `alpaca_go_25k_gate.py` filtre les incidents
severity P0/P1/CRITICAL + status open, scope depuis paper_start_at +
book=alpaca_us. Si incidents ouverts > 0 -> NO_GO_incident_open.
